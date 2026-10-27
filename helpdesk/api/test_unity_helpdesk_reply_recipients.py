# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt
"""Tests for the per-reply To / Cc an agent chooses in the Unity reply composer."""

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.api.unity_helpdesk_ext import reply
from helpdesk.helpdesk.doctype.hd_ticket.api import get_ticket_thread_components


def _sent_rows(ticket_name):
	return frappe.get_all(
		"Communication",
		filters={"reference_doctype": "HD Ticket", "reference_name": ticket_name, "sent_or_received": "Sent"},
		fields=["recipients", "cc"],
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
