# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt
"""Tests for `bulk_update_tickets` — Unity SPA bulk-edit endpoint."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.api.unity_helpdesk import (
	ALLOWED_BULK_FIELDS,
	BULK_UPDATE_MAX,
	bulk_update_tickets,
)


SUBJECT_PREFIX = "BULKUPD-"


def _create_ticket(idx, status="Open", priority="Medium"):
	doc = frappe.get_doc(
		{
			"doctype": "HD Ticket",
			"subject": f"{SUBJECT_PREFIX}{idx:03d}",
			"description": "bulk-update fixture",
			"status": status,
			"priority": priority,
		}
	).insert(ignore_permissions=True)
	return doc.name


class TestBulkUpdateTickets(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.tickets = [_create_ticket(i) for i in range(3)]
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.db.sql(
			f"DELETE FROM `tabHD Ticket` WHERE subject LIKE '{SUBJECT_PREFIX}%'"
		)
		frappe.db.commit()
		super().tearDownClass()

	def test_rejects_unknown_field(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			bulk_update_tickets(self.tickets, "subject", "hacked")

	def test_rejects_empty_field(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			bulk_update_tickets(self.tickets, "", "Open")

	def test_rejects_empty_names(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			bulk_update_tickets([], "status", "Resolved")

	def test_rejects_over_limit(self):
		fake_names = [f"FAKE-{i}" for i in range(BULK_UPDATE_MAX + 1)]
		with self.assertRaises(frappe.exceptions.ValidationError):
			bulk_update_tickets(fake_names, "status", "Resolved")

	def test_allowed_fields_constant(self):
		# Lock the allow-list to expected fields so a new contributor can't silently
		# add `subject` or other free-form fields without bumping the test.
		self.assertEqual(
			ALLOWED_BULK_FIELDS,
			{"status", "priority", "_assign", "ticket_type", "agent_group"},
		)

	def test_bulk_status_resolved_updates_all(self):
		res = bulk_update_tickets(self.tickets, "status", "Resolved")
		self.assertEqual(set(res["updated"]), set(self.tickets))
		self.assertEqual(res["failed"], [])
		for name in self.tickets:
			self.assertEqual(frappe.db.get_value("HD Ticket", name, "status"), "Resolved")
		# Reset for downstream tests
		for name in self.tickets:
			frappe.db.set_value("HD Ticket", name, "status", "Open", update_modified=False)
		frappe.db.commit()

	def test_bulk_priority_low_updates_all(self):
		res = bulk_update_tickets(self.tickets, "priority", "Low")
		self.assertEqual(set(res["updated"]), set(self.tickets))
		for name in self.tickets:
			self.assertEqual(frappe.db.get_value("HD Ticket", name, "priority"), "Low")
		# Reset
		for name in self.tickets:
			frappe.db.set_value("HD Ticket", name, "priority", "Medium", update_modified=False)
		frappe.db.commit()

	def test_bulk_status_on_hold_sets_flag(self):
		res = bulk_update_tickets(self.tickets, "status", "On Hold")
		self.assertEqual(set(res["updated"]), set(self.tickets))
		for name in self.tickets:
			self.assertEqual(
				frappe.db.get_value("HD Ticket", name, "custom_is_on_hold"),
				1,
			)
		# Reset
		for name in self.tickets:
			ticket = frappe.get_doc("HD Ticket", name)
			ticket.status = "Open"
			ticket.custom_is_on_hold = 0
			ticket.save(ignore_permissions=True)
		frappe.db.commit()

	def test_invalid_status_value_throws(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			bulk_update_tickets(self.tickets, "status", "Bogus")

	def test_failed_row_isolated_from_succeeded_rows(self):
		# Pass one bogus name alongside two real ones — the bogus row should
		# land in failed[] while the real ones get updated.
		bogus = "TKT-DOES-NOT-EXIST"
		res = bulk_update_tickets(
			[bogus, self.tickets[0], self.tickets[1]], "priority", "High"
		)
		self.assertIn(bogus, [row["name"] for row in res["failed"]])
		self.assertIn(self.tickets[0], res["updated"])
		self.assertIn(self.tickets[1], res["updated"])
		# Reset
		for name in (self.tickets[0], self.tickets[1]):
			frappe.db.set_value("HD Ticket", name, "priority", "Medium", update_modified=False)
		frappe.db.commit()

	def test_bulk_assign_calls_assign_helper(self):
		# Verify _assign routes through the assignment helper rather than writing
		# the JSON field directly — keeps tabToDo / activity log in sync.
		with patch("helpdesk.api.unity_helpdesk.assign_ticket_to_agent") as mock_assign:
			res = bulk_update_tickets(self.tickets, "_assign", "agent@example.com")
		self.assertEqual(set(res["updated"]), set(self.tickets))
		self.assertEqual(mock_assign.call_count, len(self.tickets))
		called_with = {call.args[0] for call in mock_assign.call_args_list}
		self.assertEqual(called_with, set(self.tickets))

	def test_bulk_clear_assignment(self):
		with patch("helpdesk.api.unity_helpdesk.clear_all_assignments") as mock_clear:
			res = bulk_update_tickets(self.tickets, "_assign", "")
		self.assertEqual(set(res["updated"]), set(self.tickets))
		self.assertEqual(mock_clear.call_count, len(self.tickets))

	def test_string_names_payload_is_parsed(self):
		# The SPA may send the names array as JSON-encoded string (frappe.call
		# stringifies arrays on form-data submissions).
		import json

		res = bulk_update_tickets(json.dumps(self.tickets), "priority", "Medium")
		self.assertEqual(set(res["updated"]), set(self.tickets))
