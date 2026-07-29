# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt
"""Tests for per-user column preferences in the Unity Helpdesk tickets list."""

import json

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.api.unity_helpdesk import (
	AVAILABLE_TICKET_COLUMN_KEYS,
	AVAILABLE_TICKET_COLUMNS,
	COLUMN_AUTOADD_DEFAULT_KEY,
	COLUMN_PREFS_DEFAULT_KEY,
	COLUMN_PREFS_MAX_ITEMS,
	COLUMN_WIDTH_MAX,
	COLUMN_WIDTH_MIN,
	DEFAULT_COLUMN_ORDER,
	TICKET_DOCTYPE,
	UNITY_TICKET_FIELDS,
	_default_column_preferences,
	_load_column_preferences,
	_selected_column_fields,
	update_column_preferences,
)


FIXED_KEYS = [c["key"] for c in AVAILABLE_TICKET_COLUMNS if c["fixed"]]


def _clear_default(user):
	# Both keys, not just the prefs one: _load_column_preferences() writes the
	# autoadd flag as a side effect, so leaving it behind leaks state into sibling
	# tests and makes them order-dependent.
	frappe.db.sql(
		"DELETE FROM `tabDefaultValue` WHERE parent=%s AND defkey IN (%s, %s)",
		(user, COLUMN_PREFS_DEFAULT_KEY, COLUMN_AUTOADD_DEFAULT_KEY),
	)
	frappe.clear_cache(user=user)


def _column_def(key):
	return next(c for c in AVAILABLE_TICKET_COLUMNS if c["key"] == key)


class TestColumnPreferencesLoad(FrappeTestCase):
	def setUp(self):
		_clear_default(frappe.session.user)

	def tearDown(self):
		_clear_default(frappe.session.user)

	def test_default_returned_when_no_user_setting(self):
		self.assertEqual(_load_column_preferences(), _default_column_preferences())

	def test_round_trip_persists_user_choice(self):
		update_column_preferences(json.dumps([{"key": "priority", "width": 200}]))
		loaded = _load_column_preferences()
		# Fixed columns are prepended, then user's selection
		fixed_loaded = [p["key"] for p in loaded if p["key"] in FIXED_KEYS]
		self.assertEqual(fixed_loaded, FIXED_KEYS)
		priority = next((p for p in loaded if p["key"] == "priority"), None)
		self.assertIsNotNone(priority)
		self.assertEqual(priority["width"], 200)


class TestUpdateColumnPreferences(FrappeTestCase):
	def setUp(self):
		_clear_default(frappe.session.user)

	def tearDown(self):
		_clear_default(frappe.session.user)

	def test_non_list_payload_throws(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			update_column_preferences(json.dumps({"not": "a list"}))

	def test_invalid_json_string_throws(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			update_column_preferences("{not-json")

	def test_invalid_column_key_sanitized_out(self):
		# Unknown key should be dropped; defaults injected back in
		result = update_column_preferences(
			json.dumps([{"key": "DROP TABLE tickets", "width": 100}])
		)
		keys = {p["key"] for p in result["column_preferences"]}
		self.assertNotIn("DROP TABLE tickets", keys)
		# Falls back to defaults when everything is invalid (after fixed-col injection)
		self.assertIn("name", keys)
		self.assertIn("subject", keys)

	def test_width_clamped_to_min(self):
		result = update_column_preferences(
			json.dumps([{"key": "priority", "width": 10}])
		)
		priority = next(p for p in result["column_preferences"] if p["key"] == "priority")
		self.assertEqual(priority["width"], COLUMN_WIDTH_MIN)

	def test_width_clamped_to_max(self):
		result = update_column_preferences(
			json.dumps([{"key": "priority", "width": 9999}])
		)
		priority = next(p for p in result["column_preferences"] if p["key"] == "priority")
		self.assertEqual(priority["width"], COLUMN_WIDTH_MAX)

	def test_fixed_columns_always_present(self):
		# Submit prefs omitting name/subject — they must still appear at the front
		result = update_column_preferences(
			json.dumps([{"key": "priority", "width": 130}])
		)
		keys = [p["key"] for p in result["column_preferences"]]
		for k in FIXED_KEYS:
			self.assertIn(k, keys)
		# Fixed keys come before the user-chosen ones
		first_non_fixed = next(i for i, k in enumerate(keys) if k not in FIXED_KEYS)
		fixed_indices = [keys.index(k) for k in FIXED_KEYS]
		self.assertTrue(all(idx < first_non_fixed for idx in fixed_indices))

	def test_dedups_duplicate_keys(self):
		result = update_column_preferences(
			json.dumps(
				[
					{"key": "priority", "width": 200},
					{"key": "priority", "width": 300},
				]
			)
		)
		priority_widths = [p["width"] for p in result["column_preferences"] if p["key"] == "priority"]
		self.assertEqual(priority_widths, [200])

	def test_oversize_payload_rejected(self):
		oversize = [{"key": "priority", "width": 130}] * (COLUMN_PREFS_MAX_ITEMS + 1)
		with self.assertRaises(frappe.exceptions.ValidationError):
			update_column_preferences(json.dumps(oversize))


class TestRelativeDateColumns(FrappeTestCase):
	"""The "Created" / "Last Modified" columns render elapsed time client-side
	("2 months ago"), alongside the absolute "Created On" / "Last Updated" pair.

	Both are virtual: there is no `creation_age` / `modified_age` field on HD
	Ticket, and `creation` / `modified` are already fetched for every row, so
	neither column may reach the SQL SELECT or trigger a refetch in the SPA.
	"""

	# (virtual key, source field, label, the absolute column it sits beside)
	RELATIVE_COLUMNS = [
		("creation_age", "creation", "Created"),
		("modified_age", "modified", "Last Modified"),
	]

	def setUp(self):
		_clear_default(frappe.session.user)

	def tearDown(self):
		_clear_default(frappe.session.user)

	def test_columns_registered(self):
		for key, _source, label in self.RELATIVE_COLUMNS:
			with self.subTest(key=key):
				self.assertIn(key, AVAILABLE_TICKET_COLUMN_KEYS)
				col = _column_def(key)
				self.assertEqual(col["label"], label)
				self.assertFalse(col["fixed"])

	def test_columns_are_virtual(self):
		# Guards the SPA's columnNeedsFetch() contract — without this flag, adding
		# either column would force a full get_tickets reload every single time.
		for key, _source, _label in self.RELATIVE_COLUMNS:
			with self.subTest(key=key):
				self.assertTrue(_column_def(key).get("virtual"))

	def test_columns_are_not_ticket_fields(self):
		# If someone ever adds a real field with one of these names, the virtual key
		# would silently start being fetched. Fail loudly here instead.
		meta = frappe.get_meta(TICKET_DOCTYPE)
		for key, _source, _label in self.RELATIVE_COLUMNS:
			with self.subTest(key=key):
				self.assertFalse(meta.has_field(key))

	def test_source_fields_always_fetched(self):
		# The columns render from these without asking for them, so they must be
		# unconditionally present in the list query.
		for _key, source, _label in self.RELATIVE_COLUMNS:
			with self.subTest(source=source):
				self.assertIn(source, UNITY_TICKET_FIELDS)

	def test_excluded_from_selected_fields(self):
		update_column_preferences(
			json.dumps(
				[
					{"key": "creation_age", "width": 130},
					{"key": "modified_age", "width": 130},
					{"key": "priority", "width": 130},
				]
			)
		)
		fields = _selected_column_fields()
		self.assertNotIn("creation_age", fields)
		self.assertNotIn("modified_age", fields)
		# ...but real keys alongside them still come through — the filter targets
		# virtual columns specifically, not everything.
		self.assertIn("priority", fields)

	def test_ordered_beside_their_absolute_twin(self):
		keys = [c["key"] for c in AVAILABLE_TICKET_COLUMNS]
		for key, source, _label in self.RELATIVE_COLUMNS:
			with self.subTest(key=key):
				self.assertEqual(keys.index(key), keys.index(source) + 1)

	def test_in_default_preferences(self):
		defaults = {p["key"] for p in _default_column_preferences()}
		for key, _source, _label in self.RELATIVE_COLUMNS:
			with self.subTest(key=key):
				self.assertIn(key, defaults)

	def test_default_view_uses_the_requested_sequence(self):
		# The first nine columns are specified exactly; the rest follow.
		keys = [p["key"] for p in _default_column_preferences()]
		self.assertEqual(keys[:9], [
			"name",  # Ticket ID
			"subject",  # Subject
			"priority",  # Priority
			"ticket_type",  # Ticket Type
			"_assign",  # Assigned To
			"creation_age",  # Created
			"modified_age",  # Last Modified
			"custom_hold_reason",  # Reason Of Hold
			"creation",  # Created On
		])

	def test_relative_pair_stays_adjacent(self):
		# "raised a day ago, touched 20 days ago" only reads as a pair.
		keys = [p["key"] for p in _default_column_preferences()]
		self.assertEqual(keys.index("modified_age"), keys.index("creation_age") + 1)

	def test_every_default_column_appears_in_the_default_order(self):
		# DEFAULT_COLUMN_ORDER is maintained by hand; a new default:True column
		# that nobody added to it would silently be appended to the far right.
		defaults = {c["key"] for c in AVAILABLE_TICKET_COLUMNS if c["default"]}
		self.assertEqual(defaults - set(DEFAULT_COLUMN_ORDER), set())
		# ...and the reverse: a stale key left behind after a column was removed.
		self.assertEqual(set(DEFAULT_COLUMN_ORDER) - defaults, set())

	def test_absolute_twins_still_registered(self):
		# All four date columns coexist — the relative ones are additive and must
		# not have displaced the exact-datetime pair.
		self.assertEqual(_column_def("creation")["label"], "Created On")
		self.assertEqual(_column_def("modified")["label"], "Last Updated")

	def test_autoadded_for_existing_user_once(self):
		user = frappe.session.user
		# A user who saved prefs before these columns existed, and who has already
		# been auto-given the previous new default ("owner").
		frappe.db.set_default(
			COLUMN_PREFS_DEFAULT_KEY,
			json.dumps([{"key": "subject", "width": 280}, {"key": "owner", "width": 180}]),
			user,
		)
		frappe.db.set_default(COLUMN_AUTOADD_DEFAULT_KEY, json.dumps(["owner"]), user)
		frappe.clear_cache(user=user)

		loaded = {p["key"] for p in _load_column_preferences()}
		self.assertIn("creation_age", loaded)
		self.assertIn("modified_age", loaded)

		# The user then hides them. That must stick — no re-adding on the next load.
		kept = [
			p
			for p in _load_column_preferences()
			if p["key"] not in ("creation_age", "modified_age")
		]
		update_column_preferences(json.dumps(kept))
		reloaded = {p["key"] for p in _load_column_preferences()}
		self.assertNotIn("creation_age", reloaded)
		self.assertNotIn("modified_age", reloaded)
