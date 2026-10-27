# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt
"""Tests for editing an internal note (HD Ticket Comment) from the Unity SPA:
author-only permission, the `is_edited` / `edited_at` stamp, and the no-op guard."""

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.api.unity_helpdesk_ext import add_comment, update_comment
from helpdesk.helpdesk.doctype.hd_ticket.api import get_comments


class TestUpdateComment(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.ticket = frappe.get_doc(
			{
				"doctype": "HD Ticket",
				"subject": "editable internal note",
				"raised_by": "edit-note-test@example.com",
				"description": "customer opening message",
			}
		).insert(ignore_permissions=True)
		self.note = add_comment(self.ticket.name, "<p>original note</p>")["comment"]

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.delete_doc("HD Ticket", self.ticket.name, force=True, ignore_permissions=True)

	def test_fresh_note_is_not_marked_edited(self):
		self.assertEqual(self.note["is_edited"], 0)
		self.assertIsNone(self.note["edited_at"])

	def test_author_edit_updates_body_and_stamps_edited(self):
		res = update_comment(self.note["name"], "<p>changed note</p>")["comment"]
		self.assertEqual(res["content"], "<p>changed note</p>")
		self.assertEqual(res["is_edited"], 1)
		self.assertTrue(res["edited_at"])

		# The thread read path (what the SPA renders on load) carries the marker too.
		row = next(c for c in get_comments(self.ticket.name) if c.name == self.note["name"])
		self.assertEqual(row.content, "<p>changed note</p>")
		self.assertEqual(row.is_edited, 1)
		self.assertTrue(row.edited_at)

	def test_unchanged_content_does_not_stamp_edited(self):
		res = update_comment(self.note["name"], "<p>original note</p>")["comment"]
		self.assertEqual(res["is_edited"], 0)
		self.assertIsNone(res["edited_at"])

	def test_blank_content_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			update_comment(self.note["name"], "   ")
		self.assertEqual(
			frappe.db.get_value("HD Ticket Comment", self.note["name"], "content"),
			"<p>original note</p>",
		)

	def test_non_author_cannot_edit_even_with_full_ticket_access(self):
		# Administrator passes every ticket-level gate, so re-pointing the author
		# isolates the author check as the only thing that can reject the edit.
		frappe.db.set_value(
			"HD Ticket Comment", self.note["name"], "commented_by", "someone-else@example.com"
		)
		with self.assertRaises(frappe.PermissionError):
			update_comment(self.note["name"], "<p>hijacked</p>")
		self.assertEqual(
			frappe.db.get_value("HD Ticket Comment", self.note["name"], "content"),
			"<p>original note</p>",
		)

	def test_missing_note_rejected(self):
		with self.assertRaises(frappe.DoesNotExistError):
			update_comment("does-not-exist-xyz", "<p>x</p>")
