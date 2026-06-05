# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt
"""Tests for Unity Helpdesk bulk-email send and validation."""

import json
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.api.unity_helpdesk import get_student_guardian_emails
from helpdesk.api.unity_helpdesk_ext import (
	RECIPIENT_HARD_CAP,
	_split_email_list,
	_split_email_list_with_counts,
	bulk_send_email,
)


def _ensure_ticket_type(name="Question"):
	"""Tests need a real HD Ticket Type since bulk_send_email now requires it."""
	if not frappe.db.exists("HD Ticket Type", name):
		frappe.get_doc({"doctype": "HD Ticket Type", "name": name, "description": name}).insert(
			ignore_permissions=True
		)
		frappe.db.commit()
	return name


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
		_ensure_ticket_type("Question")
		# No patch on find_default_outgoing → real lookup (none configured on test site)
		with self.assertRaises(frappe.OutgoingEmailError):
			bulk_send_email(
				subject="hi",
				message="body",
				recipients=json.dumps(["a@x.com"]),
				ticket_type="Question",
			)

	def test_ticket_type_required(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			bulk_send_email(
				subject="hi",
				message="body",
				recipients=json.dumps(["a@x.com"]),
			)

	def test_invalid_ticket_type_throws(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			bulk_send_email(
				subject="hi",
				message="body",
				recipients=json.dumps(["a@x.com"]),
				ticket_type="DoesNotExist-xyz",
			)


class TestBulkSendEmailSendPath(FrappeTestCase):
	"""End-to-end tests that mock outgoing-account + sendmail."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls._created_tickets = []
		cls._ticket_type = _ensure_ticket_type("Question")

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
			"ticket_type": self._ticket_type,
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
					ticket_type=self._ticket_type,
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
					ticket_type=self._ticket_type,
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

	def test_cc_bcc_accept_json_array_payload(self):
		# SPA now sends cc/bcc as JSON-encoded arrays. Regression guard: this must
		# not be CSV-split into garbage like '"a@x.com"'.
		result, mock_sendmail = self._run(
			recipients=json.dumps(["good@x.com"]),
			cc=json.dumps(["cc1@x.com", "cc2@x.com"]),
			bcc=json.dumps(["bcc1@x.com"]),
		)
		self.assertEqual(result["invalid_cc_count"], 0)
		self.assertEqual(result["invalid_bcc_count"], 0)
		_, kwargs = mock_sendmail.call_args
		self.assertIn("cc1@x.com", kwargs.get("cc") or [])
		self.assertIn("cc2@x.com", kwargs.get("cc") or [])
		self.assertIn("bcc1@x.com", kwargs.get("bcc") or [])


def _delete_if_exists(doctype, name):
	if frappe.db.exists(doctype, name):
		frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)


class TestGetStudentGuardianEmails(FrappeTestCase):
	"""Tests for the guardian-lookup endpoint that backs the bulk-email BCC auto-fill."""

	# Test fixtures all share this prefix so teardown can wipe them cleanly.
	_PREFIX = "BulkEmailGuardianTest"

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		# The edu_quality override on Student.autoname requires a Student Applicant
		# and Student.after_insert creates Google accounts — both are out of scope.
		# Patch them for the lifetime of this test class.
		try:
			from edu_quality.edu_quality.overrides.student import CustomStudent
		except Exception:
			CustomStudent = None
		cls._student_patches = []
		if CustomStudent is not None:
			counter = {"n": 0}

			def _stub_autoname(self):
				counter["n"] += 1
				self.name = f"{cls._PREFIX}-{counter['n']:03d}"

			def _stub_after_insert(self):
				return

			p1 = patch.object(CustomStudent, "autoname", _stub_autoname)
			p2 = patch.object(CustomStudent, "after_insert", _stub_after_insert)
			p1.start()
			p2.start()
			cls._student_patches.extend([p1, p2])

		# Guardian docs first (referenced by Student Guardian rows).
		cls._guardians = []
		for i, email in enumerate(
			[
				f"g1.{cls._PREFIX.lower()}@x.in",
				f"g2.{cls._PREFIX.lower()}@x.in",
				f"shared.{cls._PREFIX.lower()}@x.in",
			],
			start=1,
		):
			g = frappe.get_doc(
				{
					"doctype": "Guardian",
					"guardian_name": f"{cls._PREFIX} G{i}",
					"email_address": email,
				}
			).insert(ignore_permissions=True)
			cls._guardians.append(g.name)
		# A guardian with no email — should be silently skipped.
		g_no_email = frappe.get_doc(
			{
				"doctype": "Guardian",
				"guardian_name": f"{cls._PREFIX} NoEmail",
			}
		).insert(ignore_permissions=True)
		cls._guardians.append(g_no_email.name)

		# Students. Each student email is unique (Student.student_email_id is unique).
		cls._students = []

		def _make_student(local, guardian_names):
			# Use lowercase email so the doctype storage matches the normalized
			# lookup key returned by the endpoint (which always lowercases).
			email = f"{local}.{cls._PREFIX.lower()}@x.in".lower()
			doc = frappe.get_doc(
				{
					"doctype": "Student",
					"first_name": f"{cls._PREFIX}-{local}",
					"student_email_id": email,
					"guardians": [{"guardian": gn} for gn in guardian_names],
				}
			).insert(ignore_permissions=True)
			cls._students.append((doc.name, email))
			return doc.name, email

		# Student A → guardians g1, g2
		cls._student_a_name, cls._student_a_email = _make_student(
			"sA", [cls._guardians[0], cls._guardians[1]]
		)
		# Student B → guardian g1 only (so g1 is "shared" across A and B's lookups
		# at the endpoint-output level even though each row is per-student)
		cls._student_b_name, cls._student_b_email = _make_student("sB", [cls._guardians[0]])
		# Student C → only the no-email guardian → should yield empty list / no key
		cls._student_c_name, cls._student_c_email = _make_student("sC", [cls._guardians[3]])
		# Student D → guardian with no email + a real one (g3 = shared)
		cls._student_d_name, cls._student_d_email = _make_student(
			"sD", [cls._guardians[3], cls._guardians[2]]
		)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		for name, _email in cls._students:
			_delete_if_exists("Student", name)
		for gname in cls._guardians:
			_delete_if_exists("Guardian", gname)
		frappe.db.commit()
		for p in getattr(cls, "_student_patches", []):
			try:
				p.stop()
			except Exception:
				pass
		super().tearDownClass()

	def test_no_match_returns_empty(self):
		result = get_student_guardian_emails(json.dumps(["nobody.exists@nowhere.test"]))
		self.assertEqual(result, {})

	def test_single_student_returns_guardian_emails(self):
		result = get_student_guardian_emails(json.dumps([self._student_a_email]))
		self.assertIn(self._student_a_email, result)
		emails = set(result[self._student_a_email])
		self.assertEqual(
			emails,
			{
				f"g1.{self._PREFIX.lower()}@x.in",
				f"g2.{self._PREFIX.lower()}@x.in",
			},
		)

	def test_two_students_independent_mapping(self):
		result = get_student_guardian_emails(
			json.dumps([self._student_a_email, self._student_b_email])
		)
		self.assertEqual(
			set(result[self._student_a_email]),
			{
				f"g1.{self._PREFIX.lower()}@x.in",
				f"g2.{self._PREFIX.lower()}@x.in",
			},
		)
		self.assertEqual(
			set(result[self._student_b_email]),
			{f"g1.{self._PREFIX.lower()}@x.in"},
		)

	def test_shared_guardian_appears_under_both_students(self):
		# Both A and B link to g1; both should list it in their guardian list.
		result = get_student_guardian_emails(
			json.dumps([self._student_a_email, self._student_b_email])
		)
		shared = f"g1.{self._PREFIX.lower()}@x.in"
		self.assertIn(shared, result[self._student_a_email])
		self.assertIn(shared, result[self._student_b_email])

	def test_guardian_without_email_skipped(self):
		# Student C has only a guardian with no email_address → omitted entirely.
		result = get_student_guardian_emails(json.dumps([self._student_c_email]))
		self.assertNotIn(self._student_c_email, result)

	def test_mixed_valid_invalid_input_silently_skips(self):
		# Invalid emails are dropped at normalization; valid match still resolves.
		result = get_student_guardian_emails(
			json.dumps(["not-an-email", "  ", self._student_b_email, ""])
		)
		self.assertEqual(
			set(result[self._student_b_email]),
			{f"g1.{self._PREFIX.lower()}@x.in"},
		)
		self.assertEqual(len(result), 1)

	def test_mixed_guardians_filter_empty_email(self):
		# Student D has one no-email guardian + one with email. Only the real one returns.
		result = get_student_guardian_emails(json.dumps([self._student_d_email]))
		self.assertEqual(
			set(result[self._student_d_email]),
			{f"shared.{self._PREFIX.lower()}@x.in"},
		)

	def test_accepts_json_string_and_list(self):
		as_string = get_student_guardian_emails(json.dumps([self._student_a_email]))
		as_list = get_student_guardian_emails([self._student_a_email])
		self.assertEqual(as_string, as_list)

	def test_dedupe_input_emails(self):
		# Duplicate input emails (case-mixed) collapse to one entry.
		result = get_student_guardian_emails(
			json.dumps([self._student_a_email, self._student_a_email.upper()])
		)
		self.assertEqual(len(result), 1)

	def test_permission_required(self):
		original = frappe.session.user
		try:
			frappe.set_user("Guest")
			with self.assertRaises(frappe.PermissionError):
				get_student_guardian_emails(json.dumps([self._student_a_email]))
		finally:
			frappe.set_user(original)
