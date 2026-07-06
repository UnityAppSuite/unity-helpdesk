# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt
"""Regression tests for the student-search backfill hardening.

Covers the prod incident (numeric HD Ticket name passed to the callee as an int
crashed the sweep into an infinite Error-Log loop) and the clean-completion fix
(the callee always stamps the field non-NULL, and completeness is tracked with a
raw IS NULL count so no-student / no-raised_by tickets drain instead of keeping
the sweep forever-incomplete)."""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import cstr

from helpdesk.api.unity_helpdesk import populate_ticket_student_search_fields

_FIELD = "custom_search_student_names"


def _delete_if_exists(doctype, name):
	if frappe.db.exists(doctype, name):
		frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)


class TestStudentSearchBackfill(FrappeTestCase):
	def setUp(self):
		self._tickets = []

	def tearDown(self):
		for name in self._tickets:
			_delete_if_exists("HD Ticket", name)
		frappe.db.commit()

	def _make_ticket(self, raised_by="student-search-test@example.com"):
		doc = frappe.get_doc(
			{
				"doctype": "HD Ticket",
				"subject": "student search backfill smoke",
				"raised_by": raised_by,
				"description": "customer wrote in",
			}
		).insert(ignore_permissions=True)
		self._tickets.append(doc.name)
		return doc

	def test_int_name_does_not_crash_and_writes_field(self):
		# The prod bug: the backfill passes frappe.get_all(...).name, which for a
		# numeric HD Ticket is an int. The callee must fetch the doc, not treat the
		# int as a doc (`'int' object has no attribute 'get'`).
		ticket = self._make_ticket()
		frappe.db.set_value("HD Ticket", ticket.name, _FIELD, None, update_modified=False)

		populate_ticket_student_search_fields(int(ticket.name))  # int, as the sweep passes it

		value = frappe.db.get_value("HD Ticket", cstr(ticket.name), _FIELD)
		# No match for the test email → empty string, but crucially NON-NULL so the
		# IS NULL sweep drains it.
		self.assertIsNotNone(value, "callee left the field NULL — row would never drain")

	def test_no_raised_by_ticket_is_stamped_empty_not_null(self):
		# The write-hole: a ticket with no raised_by used to return early without
		# writing the field, so it stayed NULL and the IS NULL sweep looped on it.
		ticket = self._make_ticket()
		frappe.db.set_value(
			"HD Ticket",
			ticket.name,
			{"raised_by": "", _FIELD: None},
			update_modified=False,
		)

		populate_ticket_student_search_fields(ticket.name)

		value = frappe.db.get_value("HD Ticket", cstr(ticket.name), _FIELD)
		self.assertEqual(value, "", "no-raised_by ticket must be stamped '' (non-NULL) so it drains")

	def test_drain_semantics_empty_is_not_null(self):
		# The completion switch: '' (processed, no students) must NOT be counted as
		# pending, while NULL (never processed) must be. This is why the patch uses a
		# raw IS NULL count instead of Frappe's "is not set" (which matches both).
		ticket = self._make_ticket()

		frappe.db.set_value("HD Ticket", ticket.name, _FIELD, None, update_modified=False)
		still_null = frappe.db.sql(
			f"SELECT COUNT(*) FROM `tabHD Ticket` WHERE name=%s AND `{_FIELD}` IS NULL",
			(ticket.name,),
		)[0][0]
		self.assertEqual(still_null, 1, "NULL must count as pending")

		frappe.db.set_value("HD Ticket", ticket.name, _FIELD, "", update_modified=False)
		still_null = frappe.db.sql(
			f"SELECT COUNT(*) FROM `tabHD Ticket` WHERE name=%s AND `{_FIELD}` IS NULL",
			(ticket.name,),
		)[0][0]
		self.assertEqual(still_null, 0, "empty '' must NOT count as pending (row drains)")
