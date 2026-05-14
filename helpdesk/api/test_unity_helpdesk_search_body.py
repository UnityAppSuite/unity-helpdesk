# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt
"""Tests for the searchable body field on HD Ticket — smart head+tail truncation
and re-indexing on Communication / HD Ticket Comment edits."""

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.api.unity_helpdesk import (
	SEARCH_BODY_MAX,
	SEARCH_HEAD_BUDGET,
	SEARCH_TAIL_BUDGET,
	_build_ticket_message_search_values,
	update_ticket_message_search_index,
)


def _delete_if_exists(doctype, name):
	if frappe.db.exists(doctype, name):
		frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)


def _add_communication(ticket_name, content, sent_or_received="Sent", subject=""):
	doc = frappe.get_doc(
		{
			"doctype": "Communication",
			"reference_doctype": "HD Ticket",
			"reference_name": ticket_name,
			"sent_or_received": sent_or_received,
			"content": content,
			"subject": subject or f"Re: {ticket_name}",
			"communication_medium": "Email",
		}
	).insert(ignore_permissions=True)
	return doc


class TestSearchBodyTruncation(FrappeTestCase):
	"""The 12KB body field must keep BOTH the opening message AND the latest reply,
	even for very long threads."""

	def setUp(self):
		self._tickets = []

	def tearDown(self):
		for name in self._tickets:
			_delete_if_exists("HD Ticket", name)
		frappe.db.commit()

	def _make_ticket(self, subject="search body smoke", description="OPENING_TOKEN customer wrote in"):
		doc = frappe.get_doc(
			{
				"doctype": "HD Ticket",
				"subject": subject,
				"raised_by": "search-body-test@example.com",
				"description": description,
			}
		).insert(ignore_permissions=True)
		self._tickets.append(doc.name)
		return doc

	def test_short_thread_includes_everything(self):
		ticket = self._make_ticket()
		_add_communication(ticket.name, "first reply RESP_A", sent_or_received="Sent")
		_add_communication(ticket.name, "second reply RESP_B", sent_or_received="Sent")
		update_ticket_message_search_index(ticket.name)
		body = frappe.db.get_value("HD Ticket", ticket.name, "custom_search_message_body") or ""
		self.assertIn("opening_token", body)
		self.assertIn("resp_a", body)
		self.assertIn("resp_b", body)

	def test_long_thread_keeps_opening_and_latest_reply(self):
		# 20 communications × ~1500 chars each → would overflow the old 12KB budget
		# and drop the newest replies. New head+tail layout must keep both ends.
		ticket = self._make_ticket()
		filler = "filler " * 200  # ~1400 chars
		for i in range(19):
			_add_communication(
				ticket.name,
				f"reply number {i} {filler}",
				sent_or_received="Sent" if i % 2 else "Received",
			)
		_add_communication(
			ticket.name,
			f"LATEST_AGENT_TOKEN closing reply {filler}",
			sent_or_received="Sent",
		)
		update_ticket_message_search_index(ticket.name)
		body = frappe.db.get_value("HD Ticket", ticket.name, "custom_search_message_body") or ""

		self.assertLessEqual(len(body), SEARCH_BODY_MAX)
		self.assertIn("opening_token", body, "opening complaint dropped from head budget")
		self.assertIn("latest_agent_token", body, "latest reply dropped from tail budget")

	def test_head_budget_caps_oversized_primary_message(self):
		# Primary message larger than the head budget — head should clip but the
		# tail (latest reply) must still survive.
		giant = "PRIMARY_TOKEN " + ("x" * (SEARCH_HEAD_BUDGET + 1500))
		ticket = self._make_ticket(description=giant)
		_add_communication(
			ticket.name, "LATEST_AGENT_TOKEN follow-up", sent_or_received="Sent"
		)
		update_ticket_message_search_index(ticket.name)
		body = frappe.db.get_value("HD Ticket", ticket.name, "custom_search_message_body") or ""

		self.assertIn("primary_token", body)
		self.assertIn("latest_agent_token", body)
		self.assertLessEqual(len(body), SEARCH_BODY_MAX)

	def test_no_communications_falls_back_to_description(self):
		ticket = self._make_ticket(description="LONELY_DESC just the original")
		update_ticket_message_search_index(ticket.name)
		body = frappe.db.get_value("HD Ticket", ticket.name, "custom_search_message_body") or ""
		self.assertIn("lonely_desc", body)

	def test_helper_pure_function_packs_in_order(self):
		# Pure-function check on _assemble_search_body via the public path.
		# Opening goes before tail, subject prepends both.
		from helpdesk.api.unity_helpdesk import _assemble_search_body

		combined = _assemble_search_body(
			"the subject",
			["OPENING content here"],
			["TAIL_NEWEST", "TAIL_OLDER"],
		)
		self.assertTrue(combined.startswith("the subject"))
		# Tail is packed newest-first by the caller, so TAIL_NEWEST appears before TAIL_OLDER
		self.assertLess(combined.lower().find("tail_newest"), combined.lower().find("tail_older"))
		self.assertIn("opening content here", combined.lower())


class TestSearchBodyOnUpdate(FrappeTestCase):
	"""Edits to existing Communication / HD Ticket Comment must refresh the body index."""

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
				"subject": "on-update body smoke",
				"raised_by": "on-update-test@example.com",
				"description": "initial customer message",
			}
		).insert(ignore_permissions=True)
		self._tickets.append(doc.name)
		return doc

	def test_edit_communication_refreshes_body(self):
		ticket = self._make_ticket()
		comm = _add_communication(ticket.name, "original content here", sent_or_received="Sent")
		body_before = frappe.db.get_value("HD Ticket", ticket.name, "custom_search_message_body") or ""
		self.assertIn("original content", body_before)
		self.assertNotIn("edited_token", body_before.lower())

		# Save the doc with new content — fires on_update hook
		comm.content = "EDITED_TOKEN new content after edit"
		comm.save(ignore_permissions=True)

		body_after = frappe.db.get_value("HD Ticket", ticket.name, "custom_search_message_body") or ""
		self.assertIn("edited_token", body_after.lower())

	def test_edit_comment_refreshes_body(self):
		ticket = self._make_ticket()
		comment = frappe.get_doc(
			{
				"doctype": "HD Ticket Comment",
				"reference_ticket": ticket.name,
				"content": "<p>first internal note</p>",
				"commented_by": frappe.session.user,
			}
		).insert(ignore_permissions=True)
		body_before = frappe.db.get_value("HD Ticket", ticket.name, "custom_search_message_body") or ""
		self.assertIn("first internal note", body_before.lower())

		comment.content = "<p>UPDATED_NOTE_TOKEN content</p>"
		comment.save(ignore_permissions=True)

		body_after = frappe.db.get_value("HD Ticket", ticket.name, "custom_search_message_body") or ""
		self.assertIn("updated_note_token", body_after.lower())
