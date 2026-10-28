# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt
"""Tests for the per-reply To / Cc / Bcc an agent chooses in the Unity reply composer."""

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.api.unity_helpdesk_ext import create_ticket, reply
from helpdesk.helpdesk.doctype.hd_ticket.api import get_ticket_thread_components


def _sent_rows(ticket_name):
	return frappe.get_all(
		"Communication",
		filters={"reference_doctype": "HD Ticket", "reference_name": ticket_name, "sent_or_received": "Sent"},
		fields=["recipients", "cc", "bcc"],
		order_by="creation asc",
	)


class TestReplyRecipients(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.ticket = frappe.get_doc(
			{
				"doctype": "HD Ticket",
				"subject": "reply recipients",
				"raised_by": "parent@example.com",
				"description": "opening message",
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Communication",
				"communication_type": "Communication",
				"communication_medium": "Email",
				"sent_or_received": "Received",
				"sender": "parent@example.com",
				"sender_full_name": "Kamini Parent",
				"recipients": "feedback@school.example",
				"cc": "guardian2@example.com",
				"subject": "hi",
				"content": "<p>from parent</p>",
				"reference_doctype": "HD Ticket",
				"reference_name": self.ticket.name,
				"status": "Linked",
			}
		).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.delete_doc("HD Ticket", self.ticket.name, force=True, ignore_permissions=True)

	def test_inbound_sender_shows_header_name_and_email(self):
		comm = get_ticket_thread_components(self.ticket.name).communications[0]
		self.assertEqual(comm.user["name"], "Kamini Parent")
		self.assertEqual(comm.user["email"], "parent@example.com")

	def test_empty_to_falls_back_to_raised_by(self):
		row = reply(self.ticket.name, "<p>r</p>", recipients="", cc="")["communication"]
		self.assertEqual(row["recipients"], "parent@example.com")

	def test_redirected_reply_does_not_change_raised_by(self):
		row = reply(
			self.ticket.name, "<p>r</p>", recipients="Other@Example.com, second@example.com", cc="guardian2@example.com"
		)["communication"]
		self.assertEqual(row["recipients"], "other@example.com, second@example.com")
		self.assertEqual(row["cc"], "guardian2@example.com")
		self.assertEqual(frappe.db.get_value("HD Ticket", self.ticket.name, "raised_by"), "parent@example.com")

		# The redirect was per-reply: the next default reply goes to raised_by again.
		row = reply(self.ticket.name, "<p>r2</p>", recipients="", cc="")["communication"]
		self.assertEqual(row["recipients"], "parent@example.com")

	def test_cleared_cc_is_not_reinherited_from_last_message(self):
		# The inbound mail had a Cc; an agent who clears the field must send with none.
		row = reply(self.ticket.name, "<p>r</p>", recipients="", cc="")["communication"]
		self.assertFalse(row["cc"])

	def test_invalid_to_rejected_and_nothing_saved(self):
		before = len(_sent_rows(self.ticket.name))
		with self.assertRaises(frappe.ValidationError):
			reply(self.ticket.name, "<p>r</p>", recipients="not-an-email")
		self.assertEqual(len(_sent_rows(self.ticket.name)), before)

	def test_invalid_cc_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			reply(self.ticket.name, "<p>r</p>", cc="x@, y")

	def test_one_reply_creates_exactly_one_communication(self):
		# Guards the no-outgoing-account fallback: reply_via_agent inserts its
		# Communication BEFORE throwing "No sender email", so the fallback must not
		# insert a second one.
		reply(self.ticket.name, "<p>r</p>", recipients="", cc="")
		self.assertEqual(len(_sent_rows(self.ticket.name)), 1)

	def test_multiple_cc_and_bcc_are_all_stored(self):
		row = reply(
			self.ticket.name,
			"<p>r</p>",
			recipients="parent@example.com",
			cc="cc1@example.com, cc2@example.com; CC3@Example.com",
			bcc="bcc1@example.com, bcc2@example.com",
		)["communication"]
		self.assertEqual(row["cc"], "cc1@example.com, cc2@example.com, cc3@example.com")
		self.assertEqual(row["bcc_visible"], "bcc1@example.com, bcc2@example.com")

	def test_invalid_bcc_rejected_and_nothing_saved(self):
		before = len(_sent_rows(self.ticket.name))
		with self.assertRaises(frappe.ValidationError):
			reply(self.ticket.name, "<p>r</p>", bcc="good@example.com, bad")
		self.assertEqual(len(_sent_rows(self.ticket.name)), before)

	def test_audit_bcc_is_merged_but_hidden_from_thread(self):
		# HD Settings monitoring address must be BCC'd on the wire but never shown
		# to agents as part of "their" Bcc.
		orig = frappe.db.get_single_value("HD Settings", "unity_bulk_email_default_recipients")
		frappe.db.set_single_value("HD Settings", "unity_bulk_email_default_recipients", "audit@school.example")
		try:
			row = reply(self.ticket.name, "<p>r</p>", recipients="parent@example.com", bcc="bcc1@example.com")["communication"]
			self.assertIn("audit@school.example", row["bcc"])
			self.assertEqual(row["bcc_visible"], "bcc1@example.com")

			thread_row = next(c for c in get_ticket_thread_components(self.ticket.name).communications if c.name == row["name"])
			self.assertEqual(thread_row.bcc_visible, "bcc1@example.com")

			# No agent Bcc at all -> nothing shown, audit still on the wire.
			row = reply(self.ticket.name, "<p>r2</p>", recipients="parent@example.com", bcc="")["communication"]
			self.assertEqual(row["bcc_visible"], "")
			self.assertIn("audit@school.example", row["bcc"])
		finally:
			frappe.db.set_single_value("HD Settings", "unity_bulk_email_default_recipients", orig or "")


class TestCreateTicketRecipients(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.ticket_type = frappe.get_all("HD Ticket Type", pluck="name", limit=1)[0]
		self.created = []

	def tearDown(self):
		frappe.set_user("Administrator")
		for name in self.created:
			if frappe.db.exists("HD Ticket", name):
				frappe.delete_doc("HD Ticket", name, force=True, ignore_permissions=True)

	def _create(self, **kw):
		res = create_ticket(
			subject="create recipients",
			raised_by="parent@example.com",
			message="<p>hello</p>",
			ticket_type=self.ticket_type,
			**kw,
		)
		self.created.append(res["ticket"]["name"])
		return res["ticket"]["name"]

	def test_cc_and_bcc_are_stored_on_the_outgoing_mail(self):
		name = self._create(cc="cc1@example.com, CC2@Example.com", bcc="bcc1@example.com")
		rows = _sent_rows(name)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].recipients, "parent@example.com")
		self.assertEqual(rows[0].cc, "cc1@example.com, cc2@example.com")
		self.assertEqual(rows[0].bcc, "bcc1@example.com")

	def test_invalid_cc_rejected_before_ticket_is_created(self):
		before = frappe.db.count("HD Ticket", {"subject": "create recipients"})
		with self.assertRaises(frappe.ValidationError):
			create_ticket(
				subject="create recipients",
				raised_by="parent@example.com",
				message="<p>x</p>",
				ticket_type=self.ticket_type,
				cc="not-an-email",
			)
		self.assertEqual(frappe.db.count("HD Ticket", {"subject": "create recipients"}), before)
