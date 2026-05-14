# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt
"""Tests for Unity Helpdesk bulk-email send and validation."""

import json
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.api.unity_helpdesk_ext import (
	RECIPIENT_HARD_CAP,
	_split_email_list,
	_split_email_list_with_counts,
	bulk_send_email,
)


def _has_outgoing_account_patch():
	"""Patch the upfront outgoing-account check so tests don't depend on site config."""
	return patch(
		"helpdesk.api.unity_helpdesk_ext.EmailAccount.find_default_outgoing"
		if False  # We import path differently — patch the module-level import instead.
		else "frappe.email.doctype.email_account.email_account.EmailAccount.find_default_outgoing",
		return_value=MagicMock(name="fake_email_account"),
	)


class TestSplitEmailList(FrappeTestCase):
	def test_empty_input_returns_empty(self):
		self.assertEqual(_split_email_list_with_counts(None), ([], 0))
		self.assertEqual(_split_email_list_with_counts(""), ([], 0))

	def test_csv_string(self):
		out, invalid = _split_email_list_with_counts("a@x.com, b@x.com; c@x.com")
		self.assertEqual(out, ["a@x.com", "b@x.com", "c@x.com"])
		self.assertEqual(invalid, 0)

	def test_invalid_counted(self):
		out, invalid = _split_email_list_with_counts("notanemail, ok@x.com, alsobad")
		self.assertEqual(out, ["ok@x.com"])
		self.assertEqual(invalid, 2)

	def test_dedup_case_insensitive(self):
		out, invalid = _split_email_list_with_counts("A@x.com,a@x.com,A@X.com")
		self.assertEqual(out, ["a@x.com"])
		self.assertEqual(invalid, 0)

	def test_legacy_helper_compat(self):
		self.assertEqual(_split_email_list("a@x.com,b@x.com"), ["a@x.com", "b@x.com"])


class TestBulkSendEmailValidation(FrappeTestCase):
	"""Tests that don't actually queue mail — they exercise validation paths only."""

	def test_subject_required(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			bulk_send_email(subject="", message="hi", recipients=json.dumps(["a@x.com"]))

	def test_message_required(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			bulk_send_email(subject="hi", message="   ", recipients=json.dumps(["a@x.com"]))

	def test_no_outgoing_email_account_throws_upfront(self):
		# No patch on find_default_outgoing → real lookup (none configured on test site)
		with self.assertRaises(frappe.OutgoingEmailError):
			bulk_send_email(
				subject="hi",
				message="body",
				recipients=json.dumps(["a@x.com"]),
			)


class TestBulkSendEmailSendPath(FrappeTestCase):
	"""End-to-end tests that mock outgoing-account + sendmail."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls._created_tickets = []

	@classmethod
	def tearDownClass(cls):
		for name in cls._created_tickets:
			if frappe.db.exists("HD Ticket", name):
				frappe.delete_doc("HD Ticket", name, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	def _run(self, **kwargs):
		defaults = {
			"subject": "Test bulk",
			"message": "<p>Hello world</p>",
			"recipients": json.dumps(["a@x.com", "b@x.com"]),
		}
		defaults.update(kwargs)
		with (
			_has_outgoing_account_patch(),
			patch("frappe.sendmail") as mock_sendmail,
		):
			result = bulk_send_email(**defaults)
		if result.get("ticket"):
			self._created_tickets.append(result["ticket"])
		return result, mock_sendmail

	def test_no_valid_recipients_throws(self):
		with _has_outgoing_account_patch():
			with self.assertRaises(frappe.exceptions.ValidationError):
				bulk_send_email(
					subject="x",
					message="<p>hi</p>",
					recipients=json.dumps(["not-an-email", "also bad"]),
				)

	def test_invalid_emails_counted_and_dropped(self):
		result, _ = self._run(recipients=json.dumps(["a@x.com", "notanemail", "b@x.com"]))
		self.assertEqual(result["queued"], 2)
		self.assertEqual(result["invalid_count"], 1)

	def test_recipients_deduplicated_case_insensitive(self):
		result, _ = self._run(recipients=json.dumps(["A@x.com", "a@x.com", "B@x.com"]))
		self.assertEqual(result["queued"], 2)

	def test_recipient_cap_exceeded_throws(self):
		recipients = [f"u{i}@x.com" for i in range(RECIPIENT_HARD_CAP + 1)]
		with _has_outgoing_account_patch():
			with self.assertRaises(frappe.exceptions.ValidationError):
				bulk_send_email(
					subject="x",
					message="<p>hi</p>",
					recipients=json.dumps(recipients),
				)

	def test_html_message_sanitized_no_active_script(self):
		# sanitize_html HTML-escapes script tags so they render as inert text.
		# What matters is that no executable <script> survives in the DOM.
		result, _ = self._run(
			message="<script>alert(1)</script><p>hello</p>",
		)
		desc = frappe.db.get_value("HD Ticket", result["ticket"], "description") or ""
		# No raw <script> tag — escaped form is fine (&lt;script&gt;) since it renders as text.
		self.assertNotIn("<script>", desc.lower())
		self.assertIn("&lt;script&gt;", desc.lower())
		self.assertIn("hello", desc.lower())

	def test_html_message_onerror_handler_stripped(self):
		# Inline event handlers must be stripped, not just escaped.
		result, _ = self._run(
			message='<img src=x onerror="alert(1)"><p>safe text</p>',
		)
		desc = frappe.db.get_value("HD Ticket", result["ticket"], "description") or ""
		self.assertNotIn("onerror", desc.lower())
		self.assertIn("safe text", desc.lower())

	def test_audit_ticket_has_unity_portal_flag(self):
		result, _ = self._run()
		flag = frappe.db.get_value("HD Ticket", result["ticket"], "custom_via_unity_portal")
		self.assertEqual(int(flag or 0), 1)

	def test_audit_ticket_has_bulk_email_flag(self):
		result, _ = self._run()
		flag = frappe.db.get_value("HD Ticket", result["ticket"], "custom_is_bulk_email")
		self.assertEqual(int(flag or 0), 1)

	def test_invalid_cc_bcc_counts_in_response(self):
		result, _ = self._run(
			recipients=json.dumps(["good@x.com"]),
			cc="okcc@x.com, badcc, alsobad",
			bcc="okbcc@x.com, malformed",
		)
		self.assertEqual(result["invalid_cc_count"], 2)
		self.assertEqual(result["invalid_bcc_count"], 1)

	def test_sendmail_uses_bcc_for_recipients(self):
		# BCC behavior is what hides recipients from one another
		_, mock_sendmail = self._run(recipients=json.dumps(["a@x.com", "b@x.com"]))
		mock_sendmail.assert_called_once()
		_, kwargs = mock_sendmail.call_args
		self.assertIn("a@x.com", kwargs.get("bcc") or [])
		self.assertIn("b@x.com", kwargs.get("bcc") or [])
