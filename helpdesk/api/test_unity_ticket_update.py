# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt
"""Regression tests for the ticket-update UX fixes:
- a non-empty hold reason implies On Hold (so a reason typed from the list reflects),
- a scalar save (priority/hold/status) does NOT rebuild the whole-thread search body
  (the gate that removes the slow-save latency), while a subject change still does.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.api.unity_helpdesk_ext import update_ticket

_SENTINEL = "SENTINEL_SEARCH_BODY_DO_NOT_REBUILD"


def _delete_if_exists(doctype, name):
	if frappe.db.exists(doctype, name):
		frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)


class TestTicketUpdateHoldAndReindex(FrappeTestCase):
	def setUp(self):
		self._tickets = []

	def tearDown(self):
		for name in self._tickets:
			_delete_if_exists("HD Ticket", name)
		frappe.db.commit()

	def _make_ticket(self):
		doc = frappe.get_doc(
			{
				"doctype": "HD Ticket",
				"subject": "update-ux smoke",
				"raised_by": "update-ux-test@example.com",
				"description": "customer wrote in",
			}
		).insert(ignore_permissions=True)
		self._tickets.append(doc.name)
		return doc

	def _on_hold(self, name):
		return int(frappe.db.get_value("HD Ticket", name, "custom_is_on_hold") or 0)

	def test_hold_reason_implies_on_hold(self):
		# A reason set without an explicit is_on_hold (the list's hold-reason edit)
		# must put the ticket On Hold, not silently do nothing.
		t = self._make_ticket()
		update_ticket(name=t.name, hold_reason="Waiting on parent")
		self.assertEqual(self._on_hold(t.name), 1)
		self.assertEqual(
			frappe.db.get_value("HD Ticket", t.name, "custom_hold_reason"), "Waiting on parent"
		)

	def test_explicit_unhold_wins_over_reason(self):
		# If the caller explicitly clears the flag in the same request, respect it.
		t = self._make_ticket()
		update_ticket(name=t.name, hold_reason="x", is_on_hold=0)
		self.assertEqual(self._on_hold(t.name), 0)

	def test_scalar_save_does_not_rebuild_search_body(self):
		# A hold/priority/status save must NOT re-run the whole-thread reindex
		# (the latency fix) — the pre-seeded search body survives.
		t = self._make_ticket()
		frappe.db.set_value(
			"HD Ticket", t.name, "custom_search_message_body", _SENTINEL, update_modified=False
		)
		doc = frappe.get_doc("HD Ticket", t.name)
		doc.custom_hold_reason = "on leave"
		doc.save(ignore_permissions=True)
		self.assertEqual(
			frappe.db.get_value("HD Ticket", t.name, "custom_search_message_body"),
			_SENTINEL,
			"scalar save should not rebuild the search body (reindex must be gated)",
		)

	def test_subject_change_rebuilds_search_body(self):
		# Changing the subject DOES affect the search body, so the reindex must run.
		t = self._make_ticket()
		frappe.db.set_value(
			"HD Ticket", t.name, "custom_search_message_body", _SENTINEL, update_modified=False
		)
		doc = frappe.get_doc("HD Ticket", t.name)
		doc.subject = "a completely different subject token"
		doc.save(ignore_permissions=True)
		self.assertNotEqual(
			frappe.db.get_value("HD Ticket", t.name, "custom_search_message_body"),
			_SENTINEL,
			"a subject change should rebuild the search body",
		)
