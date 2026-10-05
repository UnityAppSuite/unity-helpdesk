# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt
"""Edit / delete / pin of internal notes (HD Ticket Comment): author-or-manager rule,
ticket access, and the generic REST bypass. Real authorization, rollback-only."""

import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.api import unity_helpdesk_ext as ext

AUTHOR = "notes-author@example.test"
PEER = "notes-peer@example.test"  # assigned agent, not the author
OUTSIDER = "notes-outsider@example.test"  # agent, not assigned
MANAGER = "notes-manager@example.test"  # Helpdesk Admin
PLAIN = "notes-plain@example.test"  # no helpdesk role


def _ensure_user(email, roles, agent=False):
	if frappe.db.exists("HD Agent", email):
		frappe.delete_doc("HD Agent", email, force=True, ignore_permissions=True)
	if frappe.db.exists("User", email):
		frappe.delete_doc("User", email, force=True, ignore_permissions=True)
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": email.split("@")[0],
			"send_welcome_email": 0,
			"enabled": 1,
		}
	)
	for role in roles:
		user.append("roles", {"role": role})
	user.insert(ignore_permissions=True)
	if agent:
		# Real agents have an HD Agent row, which HD Ticket's permission_query needs for
		# get_list. db_insert skips the controller's support-rotation side effects.
		frappe.get_doc(
			{"doctype": "HD Agent", "name": email, "user": email, "agent_name": email, "is_active": 1}
		).db_insert()
	return user


class TestUnityNotes(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_user(AUTHOR, ["Agent"], agent=True)
		_ensure_user(PEER, ["Agent"], agent=True)
		_ensure_user(OUTSIDER, ["Agent"], agent=True)
		_ensure_user(MANAGER, ["Helpdesk Admin"])
		_ensure_user(PLAIN, [])

	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		frappe.set_user("Administrator")
		# Assignment-rule notifications would mail through the muted site's dummy
		# Email Account (no email_id) and crash; they are irrelevant to these tests.
		notify = patch("frappe.desk.form.assign_to.notify_assignment")
		notify.start()
		self.addCleanup(notify.stop)
		self.ticket = self._make_ticket()
		self.other_ticket = self._make_ticket()
		self.note = self._make_note(self.ticket.name, "<p>Wrong room</p>")

	def _make_ticket(self):
		doc = frappe.get_doc(
			{
				"doctype": "HD Ticket",
				"subject": "notes smoke",
				"raised_by": "notes-raiser@example.test",
				"description": "customer text",
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value("HD Ticket", doc.name, "_assign", json.dumps([AUTHOR, PEER]), update_modified=False)
		return doc

	def _make_note(self, ticket, content):
		return frappe.get_doc(
			{
				"doctype": "HD Ticket Comment",
				"reference_ticket": ticket,
				"commented_by": AUTHOR,
				"content": content,
			}
		).insert(ignore_permissions=True)

	def _body(self, ticket):
		return (frappe.db.get_value("HD Ticket", ticket, "custom_search_message_body") or "").lower()

	def test_author_edit_preserves_identity_and_flags(self):
		before = frappe.db.get_value(
			"HD Ticket Comment", self.note.name, ["commented_by", "creation", "reference_ticket", "is_pinned"]
		)
		frappe.set_user(AUTHOR)
		result = ext.update_comment(self.note.name, "<p>Correct room</p>")
		self.assertTrue(result["ok"])
		self.assertEqual(result["comment"]["content"], "<p>Correct room</p>")
		self.assertTrue(result["comment"]["can_edit"] and result["comment"]["can_delete"])
		after = frappe.db.get_value(
			"HD Ticket Comment", self.note.name, ["commented_by", "creation", "reference_ticket", "is_pinned"]
		)
		self.assertEqual(before, after)
		self.assertIn("correct room", self._body(self.ticket.name))

		detail = ext.get_ticket_detail(self.ticket.name)
		in_thread = [r for r in detail.thread if r.name == self.note.name][0]
		in_comments = [r for r in detail.comments if r.name == self.note.name][0]
		for row in (in_thread, in_comments):
			self.assertEqual(row.content, "<p>Correct room</p>")
			self.assertTrue(row.can_edit and row.can_delete)

	def test_author_delete_removes_thread_entry_and_search_text(self):
		frappe.set_user(AUTHOR)
		self.assertEqual(ext.delete_comment(self.note.name), {"ok": True, "name": self.note.name})
		self.assertFalse(frappe.db.exists("HD Ticket Comment", self.note.name))
		detail = ext.get_ticket_detail(self.ticket.name)
		self.assertNotIn(self.note.name, [r.name for r in detail.thread])
		self.assertNotIn("wrong room", self._body(self.ticket.name))

	def test_assigned_nonauthor_denied_everywhere(self):
		frappe.set_user(PEER)
		with self.assertRaises(frappe.PermissionError):
			ext.update_comment(self.note.name, "<p>hijack</p>")
		with self.assertRaises(frappe.PermissionError):
			ext.delete_comment(self.note.name)
		with self.assertRaises(frappe.PermissionError):
			frappe.client.set_value("HD Ticket Comment", self.note.name, "content", "<p>hijack</p>")
		with self.assertRaises(frappe.PermissionError):
			frappe.client.delete("HD Ticket Comment", self.note.name)
		self.assertEqual(frappe.db.get_value("HD Ticket Comment", self.note.name, "content"), "<p>Wrong room</p>")
		detail = ext.get_ticket_detail(self.ticket.name)
		flags = [r for r in detail.comments if r.name == self.note.name][0]
		self.assertFalse(flags.can_edit or flags.can_delete)

	def test_forged_author_or_reference_grants_nothing(self):
		frappe.set_user(PEER)
		with self.assertRaises(frappe.PermissionError):
			frappe.client.set_value("HD Ticket Comment", self.note.name, "commented_by", PEER)
		doc = frappe.get_doc("HD Ticket Comment", self.note.name)
		doc.commented_by = PEER
		doc.reference_ticket = self.other_ticket.name
		with self.assertRaises(frappe.PermissionError):
			doc.save()
		self.assertEqual(frappe.db.get_value("HD Ticket Comment", self.note.name, "commented_by"), AUTHOR)

	def test_manager_can_edit_and_delete(self):
		frappe.set_user(MANAGER)
		result = ext.update_comment(self.note.name, "<p>manager edit</p>")
		self.assertEqual(result["comment"]["content"], "<p>manager edit</p>")
		self.assertEqual(result["comment"]["commented_by"], AUTHOR)
		self.assertTrue(ext.delete_comment(self.note.name)["ok"])

	def test_inaccessible_ticket_guest_and_nonhelpdesk_denied(self):
		for user in (OUTSIDER, PLAIN, "Guest"):
			frappe.set_user(user)
			with self.assertRaises(frappe.PermissionError):
				ext.update_comment(self.note.name, "<p>x</p>")
			with self.assertRaises(frappe.PermissionError):
				ext.delete_comment(self.note.name)
			with self.assertRaises(frappe.PermissionError):
				ext.set_comment_pinned(self.note.name, 1)
		frappe.set_user(OUTSIDER)
		with self.assertRaises(frappe.PermissionError):
			frappe.client.delete("HD Ticket Comment", self.note.name)
		self.assertTrue(frappe.db.exists("HD Ticket Comment", self.note.name))

	def test_blank_content_and_missing_note_rejected(self):
		frappe.set_user(AUTHOR)
		for blank in ("", "<p><br></p>", "<p>&nbsp;</p>", None):
			with self.assertRaises(frappe.ValidationError):
				ext.update_comment(self.note.name, blank)
		self.assertEqual(frappe.db.get_value("HD Ticket Comment", self.note.name, "content"), "<p>Wrong room</p>")
		with self.assertRaises(frappe.DoesNotExistError):
			ext.update_comment("no-such-note", "<p>x</p>")
		with self.assertRaises(frappe.DoesNotExistError):
			ext.delete_comment("no-such-note")

	def test_nonauthor_can_pin_but_only_with_boolean(self):
		frappe.set_user(PEER)
		result = ext.set_comment_pinned(self.note.name, True)
		self.assertTrue(result["is_pinned"])
		self.assertEqual(frappe.db.get_value("HD Ticket Comment", self.note.name, "is_pinned"), 1)
		self.assertEqual(frappe.db.get_value("HD Ticket Comment", self.note.name, "content"), "<p>Wrong room</p>")
		ext.set_comment_pinned(self.note.name, "0")
		self.assertEqual(frappe.db.get_value("HD Ticket Comment", self.note.name, "is_pinned"), 0)
		with self.assertRaises(frappe.ValidationError):
			ext.set_comment_pinned(self.note.name, "maybe")

	def test_add_comment_payload_carries_flags(self):
		frappe.set_user(PEER)
		payload = ext.add_comment(self.ticket.name, "<p>peer note</p>")["comment"]
		self.assertTrue(payload["can_edit"] and payload["can_delete"])
		self.assertEqual(payload["commented_by"], PEER)
