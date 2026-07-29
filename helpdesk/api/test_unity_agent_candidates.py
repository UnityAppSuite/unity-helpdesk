# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt
"""Tests for the "Add Agent" candidate picker.

Regression context: candidates used to be the first 200 enabled System Users by
`full_name`, filtered client-side. On a site with ~8k System Users that window
ended inside the "Aa..." names, so every user sorting after it — including any
newly created one — could not be found or added at all.

These tests read existing users rather than creating them: `User.insert()` is
unusably slow on benches carrying the full Walnut app set (a `User` hook in one
of the installed apps stalls the insert), which would hang the suite. Cases that
need a specific kind of user look one up and skip when the site has none, so the
suite still passes on a bare site — the invariant checks below always run.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.api.unity_helpdesk import (
	AGENT_CANDIDATE_LIMIT,
	_agent_candidates,
)


def _names(rows):
	return [r["name"] for r in rows]


def _a_user(user_type="System User", enabled=1, exclude_agents=True):
	"""One real user of the given kind, or None."""
	sql = """
		SELECT u.name, u.full_name
		FROM `tabUser` u
		WHERE u.enabled = %(enabled)s
		  AND u.user_type = %(user_type)s
		  AND u.name NOT IN ('Administrator', 'Guest')
	"""
	if exclude_agents:
		sql += " AND u.name NOT IN (SELECT user FROM `tabHD Agent` WHERE user IS NOT NULL)"
	sql += " ORDER BY u.full_name DESC LIMIT 1"
	rows = frappe.db.sql(sql, {"enabled": enabled, "user_type": user_type}, as_dict=True)
	return rows[0] if rows else None


class TestAgentCandidates(FrappeTestCase):
	# --- invariants: always run, no site data required ---

	def test_respects_limit(self):
		self.assertLessEqual(len(_agent_candidates(limit=5)), 5)

	def test_limit_is_bounded_and_sanitised(self):
		# Junk or oversized limits must not turn into an unbounded dump. Falsy
		# limits (0, None, "") fall back to the default rather than meaning
		# "unlimited" — page_length=0 is unbounded in Frappe, and that footgun
		# must not reach a table with thousands of users.
		self.assertLessEqual(len(_agent_candidates(limit="not-a-number")), AGENT_CANDIDATE_LIMIT)
		self.assertLessEqual(len(_agent_candidates(limit=10_000)), 100)
		self.assertLessEqual(len(_agent_candidates(limit=0)), AGENT_CANDIDATE_LIMIT)
		self.assertLessEqual(len(_agent_candidates(limit=None)), AGENT_CANDIDATE_LIMIT)

	def test_wildcard_query_cannot_dump_the_table(self):
		# A bare '%' is stripped, leaving an empty search -> bounded browse list.
		self.assertLessEqual(len(_agent_candidates(search="%")), AGENT_CANDIDATE_LIMIT)
		self.assertLessEqual(len(_agent_candidates(search="%_%")), AGENT_CANDIDATE_LIMIT)

	def test_short_query_returns_browse_list(self):
		# One character matches thousands of rows; fall back to the default list.
		self.assertLessEqual(len(_agent_candidates(search="a")), AGENT_CANDIDATE_LIMIT)

	def test_excludes_administrator_and_guest(self):
		found = _names(_agent_candidates(search="admin", limit=100))
		found += _names(_agent_candidates(search="guest", limit=100))
		self.assertNotIn("Administrator", found)
		self.assertNotIn("Guest", found)

	def test_non_agents_rank_before_agents(self):
		flags = [r["is_agent"] for r in _agent_candidates(limit=100)]
		self.assertEqual(flags, sorted(flags), "already-agent rows must sort last")

	def test_rows_carry_the_fields_the_picker_renders(self):
		rows = _agent_candidates(limit=1)
		if not rows:
			self.skipTest("no enabled System Users on this site")
		for key in ("name", "full_name", "email", "user_image", "is_agent"):
			self.assertIn(key, rows[0])

	# --- behaviour against real users, skipped when the site has none ---

	def test_finds_user_sorting_past_the_old_window(self):
		"""The core regression — a user nowhere near the first 200 by full_name."""
		user = _a_user()  # ordered full_name DESC, so the far end of the alphabet
		if not user:
			self.skipTest("no eligible System User on this site")
		rows = _agent_candidates(search=user["name"], limit=100)
		self.assertIn(
			user["name"],
			_names(rows),
			f"{user['name']} ({user['full_name']}) unreachable — the 200-row window regressed",
		)

	def test_search_matches_full_name_not_just_email(self):
		user = _a_user()
		if not user or not (user["full_name"] or "").strip():
			self.skipTest("no eligible System User with a full name")
		rows = _agent_candidates(search=user["full_name"].strip(), limit=100)
		self.assertIn(user["name"], _names(rows))

	def test_existing_agents_are_flagged_not_hidden(self):
		"""Searching an existing agent must say "already an agent", not "no match"."""
		agent_user = frappe.db.get_value("HD Agent", {"user": ("is", "set")}, "user")
		if not agent_user:
			self.skipTest("no HD Agent on this site")
		rows = _agent_candidates(search=agent_user, limit=100)
		match = next((r for r in rows if r["name"] == agent_user), None)
		self.assertIsNotNone(match, f"existing agent {agent_user} missing from results")
		self.assertEqual(match["is_agent"], 1)

	def test_excludes_website_users(self):
		user = _a_user(user_type="Website User")
		if not user:
			self.skipTest("no enabled Website User on this site")
		self.assertNotIn(user["name"], _names(_agent_candidates(search=user["name"], limit=100)))

	def test_excludes_disabled_users(self):
		user = _a_user(enabled=0)
		if not user:
			self.skipTest("no disabled System User on this site")
		self.assertNotIn(user["name"], _names(_agent_candidates(search=user["name"], limit=100)))
