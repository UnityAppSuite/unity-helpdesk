# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt
"""Tests for Team Settings the per-team colour and membership editor.

Three things carry the risk:

  1. **The feature-detect contract.** `list_teams` / `_agent_group_options`
     include `custom_color` only when the column exists, and the SPA hides its
     whole Color column on that key's absence. If the key ever ships
     unconditionally, a site that hasn't run the schema patch gets "Unknown
     column" and because the same endpoint feeds the Agent Group FILTER and
     the bulk-edit dialog, the failure is much wider than a missing colour.

  2. **Colour normalisation.** The renderer only honours /^#[0-9a-fA-F]{6}$/,
     so a stored 3-char hex would save cleanly and then never render, with no
     error anywhere. `_normalize_hex_color` expands it server-side.

  3. **Membership is not cosmetic.** HD Team.users feeds `_user_teams`, which
     decides which team gets stamped on that person's next assignment.

Every class except TestCreateTeam reads the site's existing teams and skips when
there are none, the same read-existing/skip-if-absent shape as
test_unity_agent_candidates.py, which documents why User/HD Agent inserts are
unusable on this bench.

TestCreateTeam is the one class that INSERTS, and it has to: `after_insert`
fires `create_assignment_rule()`, which writes an Assignment Rule plus a row per
weekday, and *that the rule comes out disabled* is one of the guarantees
create_team owes. It cleans up by name in tearDown rather than trusting the
transaction, because FrappeTestCase only rolls back at CLASS teardown and
TestWrites in this same module commits.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.api.unity_helpdesk import (
	_agent_group_options,
	_clean_team_users,
	_normalize_hex_color,
	_user_teams,
	create_team,
	list_teams,
	update_team_color,
	update_team_members,
)

_MODULE = "helpdesk.api.unity_helpdesk"


def _a_team():
	return frappe.db.get_value("HD Team", {}, "name")


class TestColorNormalisation(FrappeTestCase):
	"""Pure function no DB, always runs."""

	def test_blank_clears_to_none(self):
		for blank in (None, "", "   "):
			with self.subTest(value=blank):
				self.assertIsNone(_normalize_hex_color(blank))

	def test_six_char_hex_passes_through(self):
		self.assertEqual(_normalize_hex_color("#3b82f6"), "#3b82f6")

	def test_three_char_hex_is_expanded(self):
		"""The trap this exists to avoid: #abc saves fine and then never
		renders, because the SPA regex demands six digits."""
		self.assertEqual(_normalize_hex_color("#abc"), "#aabbcc")
		self.assertEqual(_normalize_hex_color("#ABC"), "#aabbcc")

	def test_eight_char_hex_is_accepted_unchanged(self):
		self.assertEqual(_normalize_hex_color("#3b82f6ff"), "#3b82f6ff")

	def test_garbage_is_rejected(self):
		for bad in ("red", "3b82f6", "#", "#12", "<script>", "#0123456789"):
			with self.subTest(value=bad), self.assertRaises(frappe.exceptions.ValidationError):
				_normalize_hex_color(bad)


class TestFeatureDetect(FrappeTestCase):
	def test_color_key_absent_when_column_absent(self):
		"""The contract the SPA's teamColorAvailable computed depends on."""
		with patch.object(frappe.db, "has_column", return_value=False):
			rows = _agent_group_options()
		for row in rows:
			with self.subTest(team=row.get("name")):
				self.assertNotIn("custom_color", row)

	def test_color_key_present_when_column_present(self):
		if not frappe.db.has_column("HD Team", "custom_color"):
			self.skipTest("custom_color column not on this site yet")
		rows = _agent_group_options()
		if not rows:
			self.skipTest("no HD Team records on this site")
		for row in rows:
			with self.subTest(team=row.get("name")):
				self.assertIn("custom_color", row)

	def test_options_are_name_ordered(self):
		names = [r["name"] for r in _agent_group_options()]
		self.assertEqual(names, sorted(names))


class TestListTeams(FrappeTestCase):
	def test_every_team_carries_a_users_list(self):
		teams = list_teams()
		if not teams:
			self.skipTest("no HD Team records on this site")
		for team in teams:
			with self.subTest(team=team["name"]):
				self.assertIsInstance(team.get("users"), list)

	def test_members_match_the_child_table(self):
		team = _a_team()
		if not team:
			self.skipTest("no HD Team records on this site")
		expected = set(
			frappe.get_all(
				"HD Team Member",
				filters={"parent": team, "parenttype": "HD Team"},
				pluck="user",
			)
		)
		got = next(t for t in list_teams() if t["name"] == team)
		self.assertEqual(set(got["users"]), expected)


class TestWrites(FrappeTestCase):
	"""Mutates a real team. FrappeTestCase rolls back per test, but colour is
	written with db.set_value so it is restored explicitly."""

	def setUp(self):
		super().setUp()
		self.team = _a_team()
		if not self.team:
			self.skipTest("no HD Team records on this site")
		self._color = frappe.db.get_value("HD Team", self.team, "custom_color")
		self._members = frappe.get_all(
			"HD Team Member",
			filters={"parent": self.team, "parenttype": "HD Team"},
			pluck="user",
		)

	def tearDown(self):
		frappe.db.set_value("HD Team", self.team, "custom_color", self._color)
		doc = frappe.get_doc("HD Team", self.team)
		doc.set("users", [{"user": u} for u in self._members])
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		super().tearDown()

	def test_color_round_trips_and_normalises(self):
		update_team_color(self.team, "#abc")
		self.assertEqual(
			frappe.db.get_value("HD Team", self.team, "custom_color"), "#aabbcc"
		)

	def test_empty_color_clears(self):
		update_team_color(self.team, "#3b82f6")
		update_team_color(self.team, "")
		self.assertIsNone(frappe.db.get_value("HD Team", self.team, "custom_color"))

	def test_unknown_team_rejected(self):
		with self.assertRaises(frappe.DoesNotExistError):
			update_team_color("__no_such_team__", "#3b82f6")
		with self.assertRaises(frappe.DoesNotExistError):
			update_team_members("__no_such_team__", [])

	def test_members_are_replaced_not_appended(self):
		user = self._members[0] if self._members else frappe.db.get_value("User", {"enabled": 1}, "name")
		if not user:
			self.skipTest("no enabled users on this site")
		update_team_members(self.team, [user])
		update_team_members(self.team, [user])  # idempotent
		rows = frappe.get_all(
			"HD Team Member",
			filters={"parent": self.team, "parenttype": "HD Team"},
			pluck="user",
		)
		self.assertEqual(rows, [user])

	def test_duplicate_input_is_deduped(self):
		user = self._members[0] if self._members else frappe.db.get_value("User", {"enabled": 1}, "name")
		if not user:
			self.skipTest("no enabled users on this site")
		update_team_members(self.team, [user, user, user])
		rows = frappe.get_all(
			"HD Team Member",
			filters={"parent": self.team, "parenttype": "HD Team"},
			pluck="user",
		)
		# A duplicate would make _user_teams return the same team twice and skew
		# the teams[0] pick that stamps agent_group.
		self.assertEqual(rows, [user])

	def test_unknown_user_rejected(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			update_team_members(self.team, ["__no_such_user__@example.com"])

	def test_membership_change_is_visible_to_user_teams(self):
		"""The link that makes the agent_group sync pick a new member up."""
		user = frappe.db.get_value("User", {"enabled": 1, "name": ("not in", ("Guest",))}, "name")
		if not user:
			self.skipTest("no enabled users on this site")
		update_team_members(self.team, [user])
		frappe.local._unity_request_cache = {}  # _user_teams memoises per request
		self.assertIn(self.team, [r["team"] for r in _user_teams(user)])


class TestCleanTeamUsers(FrappeTestCase):
	"""The helper create_team and update_team_members share. No inserts."""

	def _a_user(self):
		user = frappe.db.get_value("User", {"enabled": 1, "name": ("not in", ("Guest",))}, "name")
		if not user:
			self.skipTest("no enabled users on this site")
		return user

	def test_accepts_list_json_string_and_bare_string(self):
		user = self._a_user()
		self.assertEqual(_clean_team_users([user]), [user])
		self.assertEqual(_clean_team_users(frappe.as_json([user])), [user])
		self.assertEqual(_clean_team_users(user), [user])

	def test_blank_payloads_give_empty_list(self):
		for blank in (None, "", [], "[]"):
			with self.subTest(value=blank):
				self.assertEqual(_clean_team_users(blank), [])

	def test_dedupes_preserving_order(self):
		user = self._a_user()
		other = frappe.db.get_value(
			"User", {"enabled": 1, "name": ("not in", ("Guest", user))}, "name"
		)
		if not other:
			self.skipTest("need two enabled users on this site")
		# Order is load-bearing: unity_agent_group stamps agent_group from
		# _user_teams(user)[0].
		self.assertEqual(_clean_team_users([other, user, other, "  "]), [other, user])

	def test_unknown_user_throws(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			_clean_team_users(["__no_such_user__@example.com"])


class TestCreateTeam(FrappeTestCase):
	"""The only class here that inserts an HD Team.

	Each insert drags an Assignment Rule plus 7 Assignment Rule Day rows with it
	(HDTeam.after_insert), so every name this class mints is tracked and torn
	down explicitly rather than trusting the class-level rollback.
	"""

	def setUp(self):
		super().setUp()
		self._created = []

	def _mint(self):
		"""A name no site would ever hold, unique per run so a crashed earlier
		run cannot collide with this one."""
		name = f"ZZ Unity Test Team {frappe.generate_hash(length=6)}"
		self._created.append(name)
		return name

	def tearDown(self):
		for name in self._created:
			if frappe.db.exists("HD Team", name):
				# on_trash deletes the linked rotation rule for us.
				frappe.delete_doc(
					"HD Team", name, force=True, ignore_permissions=True, delete_permanently=True
				)
			# Belt and braces for a team whose insert half-failed and left an
			# orphaned rule behind.
			for rule in frappe.get_all(
				"Assignment Rule",
				filters={"name": ("like", f"{name} - Support Rotation%")},
				pluck="name",
			):
				frappe.delete_doc("Assignment Rule", rule, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDown()

	def _a_user(self):
		user = frappe.db.get_value("User", {"enabled": 1, "name": ("not in", ("Guest",))}, "name")
		if not user:
			self.skipTest("no enabled users on this site")
		return user

	# --- happy path ---------------------------------------------------------

	def test_creates_team_with_members_and_colour(self):
		user = self._a_user()
		name = self._mint()
		row = create_team(name, color="#abc", users=[user, user, "  "])

		self.assertEqual(row["name"], name)
		self.assertEqual(row["users"], [user])  # deduped, blanks dropped
		self.assertTrue(frappe.db.exists("HD Team", name))
		self.assertEqual(
			frappe.get_all(
				"HD Team Member", filters={"parent": name, "parenttype": "HD Team"}, pluck="user"
			),
			[user],
		)
		if frappe.db.has_column("HD Team", "custom_color"):
			# #abc must have been expanded; the SPA renderer only honours 6 digits.
			self.assertEqual(row["custom_color"], "#aabbcc")
			self.assertEqual(frappe.db.get_value("HD Team", name, "custom_color"), "#aabbcc")
		else:
			self.assertNotIn("custom_color", row)

	def test_creates_team_without_colour_or_members(self):
		name = self._mint()
		row = create_team(name)
		self.assertEqual(row["users"], [])
		self.assertTrue(frappe.db.exists("HD Team", name))
		if frappe.db.has_column("HD Team", "custom_color"):
			self.assertIsNone(row["custom_color"])

	def test_return_shape_matches_a_list_teams_row(self):
		name = self._mint()
		row = create_team(name)
		listed = next(t for t in list_teams() if t["name"] == name)
		self.assertEqual(set(row.keys()), set(listed.keys()))
		self.assertEqual(row["users"], listed["users"])

	def test_new_member_is_visible_to_user_teams(self):
		"""The link that makes the agent_group sync pick the new team up."""
		user = self._a_user()
		name = self._mint()
		create_team(name, users=[user])
		frappe.local._unity_request_cache = {}  # _user_teams memoises per request
		self.assertIn(name, [r["team"] for r in _user_teams(user)])

	def test_rotation_rule_is_created_and_disabled(self):
		"""The 653-email guard. after_insert always makes a rotation rule; it
		must arrive disabled so creating a team is inert until a human enables
		it in desk."""
		name = self._mint()
		create_team(name)
		rule = frappe.db.get_value("HD Team", name, "assignment_rule")
		self.assertTrue(rule, "after_insert should have linked a rotation rule")
		self.assertEqual(
			frappe.db.get_value("Assignment Rule", rule, "disabled"),
			1,
			"an ENABLED rotation once auto-assigned every open ticket and sent 653 emails",
		)

	# --- rejections ---------------------------------------------------------

	def test_duplicate_name_is_rejected_not_silently_returned(self):
		user = self._a_user()
		name = self._mint()
		create_team(name)
		with self.assertRaises(frappe.exceptions.ValidationError):
			create_team(name, users=[user])
		# create_ticket_type's idempotent early-return would have swallowed the
		# members instead of reporting the clash.
		self.assertEqual(
			frappe.get_all(
				"HD Team Member", filters={"parent": name, "parenttype": "HD Team"}, pluck="user"
			),
			[],
		)

	def test_duplicate_is_case_insensitive(self):
		"""tabHD Team.name is utf8mb4_unicode_ci, so the unique index would
		otherwise reject this with a raw DuplicateEntryError."""
		name = self._mint()
		create_team(name)
		self._created.append(name.lower())
		with self.assertRaises(frappe.exceptions.ValidationError):
			create_team(name.lower())

	def test_blank_name_rejected(self):
		for blank in (None, "", "   "):
			with self.subTest(value=blank), self.assertRaises(frappe.exceptions.ValidationError):
				create_team(blank)

	def test_over_long_name_rejected(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			create_team("x" * 141)

	def test_angle_brackets_rejected_as_validation_not_name_error(self):
		# frappe.NameError is not a ValidationError subclass; this asserts we
		# pre-empt it so the SPA gets a readable message instead of a 500.
		with self.assertRaises(frappe.exceptions.ValidationError):
			create_team("<script>Team")

	def test_unknown_user_rejected_before_the_insert(self):
		name = self._mint()
		with self.assertRaises(frappe.exceptions.ValidationError):
			create_team(name, users=["__no_such_user__@example.com"])
		self.assertFalse(
			frappe.db.exists("HD Team", name),
			"members must be validated before the insert, otherwise a bad payload "
			"leaves a team, an assignment rule and 7 child rows behind",
		)

	def test_bad_colour_rejected_before_the_insert(self):
		name = self._mint()
		with self.assertRaises(frappe.exceptions.ValidationError):
			create_team(name, color="red")
		self.assertFalse(frappe.db.exists("HD Team", name))


class TestPermissions(FrappeTestCase):
	def test_writers_reject_non_admin(self):
		team = _a_team()
		if not team:
			self.skipTest("no HD Team records on this site")
		caps = frappe._dict({"can_view_my_tickets": True, "can_manage_unity_settings": False})
		never = "ZZ Unity Test Team never-created"
		with patch(f"{_MODULE}._get_capabilities", return_value=caps):
			with self.assertRaises(frappe.PermissionError):
				update_team_color(team, "#3b82f6")
			with self.assertRaises(frappe.PermissionError):
				update_team_members(team, [])
			with self.assertRaises(frappe.PermissionError):
				list_teams()
			with self.assertRaises(frappe.PermissionError):
				create_team(never)
		self.assertFalse(frappe.db.exists("HD Team", never))
