# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt
"""Tests for keeping HD Ticket.agent_group in step with the assignee.

Two properties carry almost all the risk here:

  1. **Every rule that isn't "stamp the team" must be a NO-OP, not a write.**
     52 of the 61 agents on this site have no HD Team membership, and 57k
     tickets already carry "Calling Team". A rule that blanked or overwrote the
     group in those cases would strip the triage queue off the backlog as it
     gets worked — silently, and across tens of thousands of rows.

  2. **The write must not bump `modified`.** That is not cosmetic. A full save
     would run hd_ticket.py::remove_assignment_if_not_in_team, which CLEARS the
     assignee when agent_group moves to a team they aren't in — undoing the very
     assignment that triggered the sync. `update_modified=False` on a raw
     set_value is what keeps that (and the assignment-rule re-run) from firing,
     so it is asserted directly.

`_user_teams` is mocked rather than fixtured: creating a User or HD Agent is
pathologically slow on benches carrying the full Walnut app set (see
test_unity_agent_candidates.py), and HD Agent.on_update drags Assignment Rule
saves in with it. Tickets are cheap, so those are real.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.api.unity_agent_group import (
	on_todo_change,
	sync_agent_group_for_ticket,
)

# `_user_teams` is imported INSIDE sync_agent_group_for_ticket (deferred, so a
# site-wide ToDo hook doesn't drag all of unity_helpdesk into every process), so
# it never becomes an attribute of unity_agent_group. Patch it at its source —
# the deferred import re-resolves it on every call, so this still takes effect.
_TEAMS = "helpdesk.api.unity_helpdesk._user_teams"
SUBJECT_PREFIX = "AGENTGRP-"
USER = "agentgroup-fixture@example.com"


def _teams(*names):
	"""Shape _user_teams returns: [{team, ignore_restrictions}], ORDER BY name."""
	return [{"team": n, "ignore_restrictions": 0} for n in names]


def _todo(ticket, user=USER, status="Open", reference_type="HD Ticket"):
	return frappe._dict(
		{
			"doctype": "ToDo",
			"reference_type": reference_type,
			"reference_name": ticket,
			"allocated_to": user,
			"status": status,
		}
	)


class _TicketFixture(FrappeTestCase):
	"""Shared real-ticket fixture. Only agent_group is exercised."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls._n = 0

	@classmethod
	def tearDownClass(cls):
		frappe.db.sql(f"DELETE FROM `tabHD Ticket` WHERE subject LIKE '{SUBJECT_PREFIX}%'")
		frappe.db.commit()
		super().tearDownClass()

	def _ticket(self, agent_group=None, status="Open"):
		type(self)._n += 1
		doc = frappe.get_doc(
			{
				"doctype": "HD Ticket",
				"subject": f"{SUBJECT_PREFIX}{type(self)._n:03d}",
				"description": "agent-group fixture",
				"status": status,
			}
		).insert(ignore_permissions=True)
		# Set agent_group AFTER insert and out of band: the DocField default
		# ("Calling Team", from an edu_quality Property Setter) would otherwise
		# win, and a save here would run the very on_update we're testing around.
		frappe.db.set_value("HD Ticket", doc.name, "agent_group", agent_group, update_modified=False)
		return doc.name

	def _group(self, ticket):
		return frappe.db.get_value("HD Ticket", ticket, "agent_group")


class TestSyncRules(_TicketFixture):
	def test_no_team_leaves_group_untouched(self):
		"""The COMMON path — 52 of 61 agents. Must never blank the group."""
		ticket = self._ticket(agent_group="Calling Team")
		with patch(_TEAMS, return_value=[]):
			self.assertIsNone(sync_agent_group_for_ticket(ticket, USER))
		self.assertEqual(self._group(ticket), "Calling Team")

	def test_no_team_does_not_blank_an_already_blank_group(self):
		ticket = self._ticket(agent_group=None)
		with patch(_TEAMS, return_value=[]):
			sync_agent_group_for_ticket(ticket, USER)
		self.assertIn(self._group(ticket), (None, ""))

	def test_current_group_already_the_users_team_is_a_noop(self):
		# Reassigning within a team must not disturb a deliberate manual choice.
		ticket = self._ticket(agent_group="Tech")
		with patch(_TEAMS, return_value=_teams("Tech")):
			self.assertIsNone(sync_agent_group_for_ticket(ticket, USER))
		self.assertEqual(self._group(ticket), "Tech")

	def test_group_moves_to_the_assignees_team(self):
		"""The headline behaviour: monika hands a triage ticket to a Tech agent."""
		ticket = self._ticket(agent_group="Calling Team")
		with patch(_TEAMS, return_value=_teams("Tech")):
			self.assertEqual(sync_agent_group_for_ticket(ticket, USER), "Tech")
		self.assertEqual(self._group(ticket), "Tech")

	def test_blank_group_gets_filled(self):
		ticket = self._ticket(agent_group=None)
		with patch(_TEAMS, return_value=_teams("Admin")):
			self.assertEqual(sync_agent_group_for_ticket(ticket, USER), "Admin")
		self.assertEqual(self._group(ticket), "Admin")

	def test_multi_team_agent_picks_first_and_is_stable(self):
		"""_user_teams is ORDER BY t.name; without it agent_group would flap
		between teams every time the same agent was reassigned."""
		ticket = self._ticket(agent_group="Calling Team")
		teams = _teams("Accounts", "Tech")
		with patch(_TEAMS, return_value=teams):
			self.assertEqual(sync_agent_group_for_ticket(ticket, USER), "Accounts")
			# Second run must be a no-op, not a re-pick.
			self.assertIsNone(sync_agent_group_for_ticket(ticket, USER))
		self.assertEqual(self._group(ticket), "Accounts")

	def test_multi_team_keeps_a_valid_non_first_group(self):
		# Ticket is on the agent's SECOND team — still valid, so leave it.
		ticket = self._ticket(agent_group="Tech")
		with patch(_TEAMS, return_value=_teams("Accounts", "Tech")):
			self.assertIsNone(sync_agent_group_for_ticket(ticket, USER))
		self.assertEqual(self._group(ticket), "Tech")

	def test_blank_team_names_are_ignored(self):
		ticket = self._ticket(agent_group="Calling Team")
		with patch(_TEAMS, return_value=[{"team": "  ", "ignore_restrictions": 0}]):
			self.assertIsNone(sync_agent_group_for_ticket(ticket, USER))
		self.assertEqual(self._group(ticket), "Calling Team")

	def test_missing_args_are_noops(self):
		with patch(_TEAMS, return_value=_teams("Tech")) as m:
			self.assertIsNone(sync_agent_group_for_ticket(None, USER))
			self.assertIsNone(sync_agent_group_for_ticket("x", None))
			m.assert_not_called()


class TestModifiedIsNotBumped(_TicketFixture):
	def test_write_does_not_touch_modified(self):
		"""Guards the update_modified=False contract.

		If this starts failing, the sync has become a doc.save() — which means
		remove_assignment_if_not_in_team can now clear the assignee the sync was
		reacting to, and assignment_rule.apply can round-robin it elsewhere.
		"""
		ticket = self._ticket(agent_group="Calling Team")
		before = frappe.db.get_value("HD Ticket", ticket, "modified")
		with patch(_TEAMS, return_value=_teams("Tech")):
			sync_agent_group_for_ticket(ticket, USER)
		after = frappe.db.get_value("HD Ticket", ticket, "modified")
		self.assertEqual(before, after)
		self.assertEqual(self._group(ticket), "Tech")


class TestUpdateTicketDoesNotClobber(_TicketFixture):
	"""The lost-update regression — the bug that made this feature look broken.

	`unity_helpdesk_ext.update_ticket` loads the ticket, THEN assigns (which
	makes our hook rewrite agent_group with a raw set_value), THEN calls
	ticket.save(). Because db_update() emits a full-column UPDATE from the
	in-memory doc, the save wrote the STALE agent_group straight back — and the
	row the endpoint returns is re-read afterwards, so the UI confidently showed
	the old team. A `ticket.reload()` after the assign block fixes it.

	This is the ONLY path Monika actually uses (the list-row dropdown and the
	detail-view select both call it), so without this test the feature passes
	every unit test and still does nothing in production.
	"""

	def _team_member(self):
		row = frappe.db.sql(
			"""SELECT m.user, m.parent AS team
			   FROM `tabHD Team Member` m
			   JOIN `tabHD Agent` a ON a.name = m.user
			   WHERE m.parenttype = 'HD Team' AND a.is_active = 1
			   ORDER BY m.parent LIMIT 1""",
			as_dict=True,
		)
		return row[0] if row else None

	def test_assign_via_update_ticket_survives_the_save(self):
		member = self._team_member()
		if not member:
			self.skipTest("no active HD Agent with an HD Team membership on this site")

		from helpdesk.api.unity_helpdesk_ext import update_ticket

		ticket = self._ticket(agent_group="Calling Team")
		update_ticket(name=ticket, assignee=member.user)
		frappe.db.commit()

		self.assertEqual(
			self._group(ticket),
			member.team,
			"agent_group was clobbered by ticket.save() — is the reload() still there?",
		)
		# ...and the assignment itself must have survived. If this fails,
		# remove_assignment_if_not_in_team has become reachable again.
		assign = frappe.db.get_value("HD Ticket", ticket, "_assign") or ""
		self.assertIn(member.user, assign, "the assignment was wiped")


class TestTodoHook(_TicketFixture):
	def test_assignment_todo_syncs(self):
		ticket = self._ticket(agent_group="Calling Team")
		with patch(_TEAMS, return_value=_teams("Tech")):
			on_todo_change(_todo(ticket))
		self.assertEqual(self._group(ticket), "Tech")

	def test_cancelled_todo_is_ignored(self):
		"""Un-assignment must never move the group.

		Reassignment arrives as clear()->Cancelled then add()->new ToDo, so the
		Cancelled event carries the OLD assignee. Acting on it would stamp the
		previous agent's team a moment before the new one lands.
		"""
		for status in ("Cancelled", "Closed"):
			with self.subTest(status=status):
				ticket = self._ticket(agent_group="Calling Team")
				with patch(_TEAMS, return_value=_teams("Tech")) as m:
					on_todo_change(_todo(ticket, status=status))
					m.assert_not_called()
				self.assertEqual(self._group(ticket), "Calling Team")

	def test_other_doctypes_are_ignored(self):
		ticket = self._ticket(agent_group="Calling Team")
		with patch(_TEAMS, return_value=_teams("Tech")) as m:
			on_todo_change(_todo(ticket, reference_type="Task"))
			m.assert_not_called()
		self.assertEqual(self._group(ticket), "Calling Team")

	def test_todo_without_reference_name_is_ignored(self):
		with patch(_TEAMS, return_value=_teams("Tech")) as m:
			on_todo_change(_todo(None))
			m.assert_not_called()

	def test_hook_never_raises(self):
		"""The hook runs inside the assignment's own transaction.

		A raise here would abort the assignment itself — a ticket showing a
		stale team is much cheaper than an assignment that silently didn't
		happen. So the hook swallows and logs.
		"""
		ticket = self._ticket(agent_group="Calling Team")
		with patch(_TEAMS, side_effect=RuntimeError("boom")):
			with patch.object(frappe, "log_error") as logged:
				on_todo_change(_todo(ticket))  # must not raise
				self.assertTrue(logged.called)
		self.assertEqual(self._group(ticket), "Calling Team")
