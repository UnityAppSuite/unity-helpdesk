# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt
"""Tests for Unity Helpdesk agent-group (HD Team) visibility scoping.

Upstream Helpdesk already has this rule, but only inside
HDTicket.get_list_filters(), which is reachable solely through
helpdesk.extends.client.get_list — the legacy desk endpoint. Unity never calls
it, and the real Frappe hook (hd_ticket.permission_query) returns None for every
agent, so before this change no Unity read path was team-scoped.

_team_restriction_filters() closes that, reusing the SAME two HD Settings
switches so Desk and Unity can't drift apart. What matters here:

  1. It is INERT by default. `restrict_tickets_by_agent_group` ships as 0, and
     with it off this must return [] — otherwise every existing site silently
     changes behaviour on deploy.
  2. It never returns an empty condition set when it means "deny". Upstream
     builds Criterion.any([]) for a user in zero teams, which is not a denial.
  3. The emitted tuple must actually match untagged (NULL agent_group) rows when
     untagged access is allowed — that hinges on Frappe rendering
     `in [..., ""]` as ifnull(col, '') IN (...).

Settings and membership are patched rather than inserted: HD Settings is a
Single shared by the whole site, and creating Users/Teams is very slow on
benches carrying the full Walnut app set.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.api.unity_helpdesk import (
	TICKET_DOCTYPE,
	_build_filters,
	_count,
	_team_restriction_filters,
	_user_teams,
)

_MODULE = "helpdesk.api.unity_helpdesk"


def _restriction(settings, teams, super_admin=False):
	"""Run _team_restriction_filters() against a synthetic configuration."""
	with patch(f"{_MODULE}._team_restriction_settings", return_value=settings), patch(
		f"{_MODULE}._user_teams", return_value=teams
	), patch(f"{_MODULE}._is_super_admin", return_value=super_admin):
		return _team_restriction_filters()


def _team(name, ignore=0):
	return {"team": name, "ignore_restrictions": ignore}


class TestRestrictionIsOffByDefault(FrappeTestCase):
	def test_disabled_setting_returns_nothing(self):
		self.assertEqual(_restriction((0, 0), [_team("Support")]), [])
		self.assertEqual(_restriction((0, 1), [_team("Support")]), [])

	def test_live_site_default_is_unrestricted(self):
		"""Guards the deploy: shipping this must not change what anyone sees
		until someone deliberately ticks the box in HD Settings."""
		enabled = frappe.db.get_single_value("HD Settings", "restrict_tickets_by_agent_group")
		if int(enabled or 0):
			self.skipTest("restriction is deliberately enabled on this site")
		self.assertEqual(_team_restriction_filters(), [])


class TestUserTeamsOrdering(FrappeTestCase):
	"""_user_teams feeds TWO callers with different needs.

	The restriction wants the whole list; unity_agent_group.sync takes `[0]` as
	"the assignee's team". Without a deterministic ORDER BY, a multi-team agent's
	tickets would flap between teams on successive assignments — and because the
	sync writes with update_modified=False, that churn would be invisible in the
	ticket's own audit trail.
	"""

	def test_query_is_ordered(self):
		import inspect

		src = inspect.getsource(_user_teams)
		self.assertIn("ORDER BY", src.upper())

	def test_repeated_calls_agree(self):
		# Uses whatever real membership the site has; passes on an empty site.
		user = frappe.db.get_value("HD Team Member", {"parenttype": "HD Team"}, "user")
		if not user:
			self.skipTest("no HD Team Member rows on this site")

		def _uncached():
			# _user_teams memoizes on frappe.local._unity_request_cache; drop only
			# THAT (never frappe.local.request_cache, which frappe's own
			# @request_cache helpers key by function object).
			frappe.local._unity_request_cache = {}
			return [r["team"] for r in _user_teams(user)]

		first, second = _uncached(), _uncached()
		self.assertEqual(first, second)
		self.assertEqual(first, sorted(first))


class TestRestrictionScoping(FrappeTestCase):
	def test_scopes_to_the_users_teams(self):
		self.assertEqual(
			_restriction((1, 0), [_team("Support"), _team("Fees")]),
			[[TICKET_DOCTYPE, "agent_group", "in", ["Support", "Fees"]]],
		)

	def test_untagged_allowed_adds_the_blank_sentinel(self):
		# "" is not padding: frappe.get_list renders an `in` list containing ""
		# as ifnull(col, '') IN (...), which is what makes NULL agent_group rows
		# match. See _qb_in_condition for the pypika mirror.
		self.assertEqual(
			_restriction((1, 1), [_team("Support")]),
			[[TICKET_DOCTYPE, "agent_group", "in", ["Support", ""]]],
		)

	def test_untagged_not_allowed_omits_the_blank(self):
		built = _restriction((1, 0), [_team("Support")])
		self.assertNotIn("", built[0][3])

	def test_super_admin_is_exempt(self):
		# Whoever administers the system keeps full visibility, or turning this
		# on locks them out of the tickets they're responsible for.
		self.assertEqual(_restriction((1, 0), [_team("Support")], super_admin=True), [])

	def test_team_with_ignore_restrictions_is_exempt(self):
		self.assertEqual(_restriction((1, 0), [_team("Support", ignore=1)]), [])

	def test_one_exempt_team_exempts_the_whole_user(self):
		# Matches upstream: membership of any unrestricted team lifts the scope.
		self.assertEqual(_restriction((1, 0), [_team("Support"), _team("Leads", ignore=1)]), [])

	def test_no_teams_but_untagged_allowed_sees_only_untagged(self):
		self.assertEqual(
			_restriction((1, 1), []),
			[[TICKET_DOCTYPE, "agent_group", "in", [""]]],
		)

	def test_no_teams_and_no_untagged_denies_explicitly(self):
		"""Deny must be a condition that matches nothing, not an empty list.

		An empty list means "no restriction" everywhere else in this file, so
		returning one here would hand a team-less user the entire table — the
		exact inversion of what the setting asks for. (Upstream's
		Criterion.any([]) has this shape; deliberately not copied.)
		"""
		built = _restriction((1, 0), [])
		self.assertEqual(built, [[TICKET_DOCTYPE, "name", "in", ["__unity_no_teams__"]]])
		self.assertEqual(_count(built), 0)

	def test_blank_team_names_are_dropped(self):
		# A malformed membership row must not widen the scope to `agent_group=""`.
		built = _restriction((1, 0), [_team("Support"), _team("  ")])
		self.assertEqual(built[0][3], ["Support"])


class TestRestrictionReachesEveryReadPath(FrappeTestCase):
	def test_it_rides_on_build_filters(self):
		"""_build_filters is the single choke point: list, both search branches,
		the cards aggregate and _summary_cache_key all consume its output. If the
		restriction is present here it cannot be missing from any of them."""
		with patch(f"{_MODULE}._team_restriction_filters", return_value=[
			[TICKET_DOCTYPE, "agent_group", "in", ["Support", ""]]
		]):
			built = _build_filters("all", {"status": "Open"})
		self.assertIn([TICKET_DOCTYPE, "agent_group", "in", ["Support", ""]], built)
		self.assertIn([TICKET_DOCTYPE, "status", "=", "Open"], built)

	def test_summary_cache_key_changes_with_the_restriction(self):
		"""The cards are Redis-cached for 30s keyed on the BUILT filter list, so
		a restriction that didn't reach that list would serve one user's counts
		to another."""
		from helpdesk.api.unity_helpdesk import _summary_cache_key

		unrestricted = _summary_cache_key({"effective_view": "all", "list_filters": []})
		restricted = _summary_cache_key(
			{
				"effective_view": "all",
				"list_filters": [[TICKET_DOCTYPE, "agent_group", "in", ["Support"]]],
			}
		)
		self.assertNotEqual(unrestricted, restricted)

	def test_emitted_condition_executes(self):
		built = _restriction((1, 1), [_team("Support")])
		frappe.get_list(TICKET_DOCTYPE, fields=["name"], filters=built, page_length=1)
