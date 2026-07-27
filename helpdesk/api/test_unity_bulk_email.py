# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt
"""Tests for Unity Helpdesk bulk-email send and validation.

The send path is exercised by running the REAL background job (``_bulk_send_email_job``)
directly against a per-send tracking record (Unity Bulk Email Batch), then asserting on
the created HD Tickets + the batch counters + the failed list — the shape the current,
enqueue-based feature actually produces. Regression tests lock the fixed bugs:
BUG-1 (emailless shared-guardian siblings), BUG-2 (failures recorded), BUG-4 (duplicate
guard), BUG-6 (CC sent once, hidden), BUG-7 (merge value sanitised in the mail), and the
"``student.user`` only, never ``student_email_id``" rule.
"""

import json
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.api import unity_helpdesk_ext as ext
from helpdesk.api.unity_helpdesk import get_student_guardian_emails
from helpdesk.api.unity_helpdesk_ext import (
	RECIPIENT_HARD_CAP,
	_bulk_fingerprint,
	_group_key,
	_split_email_list,
	_split_email_list_with_counts,
	_student_primary_email,
	bulk_send_email,
)


def _ensure_ticket_type(name="Question"):
	if not frappe.db.exists("HD Ticket Type", name):
		frappe.get_doc({"doctype": "HD Ticket Type", "name": name, "description": name}).insert(
			ignore_permissions=True
		)
		frappe.db.commit()
	return name


def _outgoing_patch(present=True):
	return patch(
		"frappe.email.doctype.email_account.email_account.EmailAccount.find_default_outgoing",
		return_value=(MagicMock(name="fake_email_account") if present else None),
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


class TestBulkEmailHelpers(FrappeTestCase):
	"""Unit tests for the two guard helpers introduced by the fixes."""

	def test_group_key_is_by_student_not_email(self):
		# BUG-1: two emailless siblings share a recipient address but are different
		# students -> different keys -> neither is skipped.
		a = _group_key({"student": "WACB39", "emails": ["shared@x.com"]})
		b = _group_key({"student": "WALE57", "emails": ["shared@x.com"]})
		self.assertNotEqual(a, b)
		self.assertTrue(a.startswith("s:"))

	def test_group_key_free_email_falls_back_to_email(self):
		self.assertTrue(_group_key({"student": None, "emails": ["x@y.com"]}).startswith("e:"))

	def test_student_primary_email_uses_user_only(self):
		# The "student.user only, never student_email_id" rule.
		self.assertEqual(
			_student_primary_email({"user": "a@x.com", "student_email_id": "b@x.com"}),
			"a@x.com",
		)
		self.assertEqual(_student_primary_email({"student_email_id": "b@x.com"}), "")
		self.assertEqual(_student_primary_email({}), "")

	def test_fingerprint_stable_and_recipient_sensitive(self):
		g1 = [{"student": None, "emails": ["a@x.com"]}]
		g2 = [{"student": None, "emails": ["b@x.com"]}]
		self.assertEqual(
			_bulk_fingerprint("u", "s", "m", "T", g1),
			_bulk_fingerprint("u", "s", "m", "T", g1),
		)
		self.assertNotEqual(
			_bulk_fingerprint("u", "s", "m", "T", g1),
			_bulk_fingerprint("u", "s", "m", "T", g2),
		)


class TestBulkSendEmailValidation(FrappeTestCase):
	"""Validation throws happen before the send; the access gate is patched so these
	assert the validation, not the caller's capabilities."""

	def setUp(self):
		self._gate = patch.object(
			ext, "_require_unity_access", return_value={"can_view_all_tickets": True}
		)
		self._gate.start()
		self.addCleanup(self._gate.stop)

	def test_subject_required(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			bulk_send_email(subject="", message="hi", recipients=json.dumps(["a@x.com"]))

	def test_message_required(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			bulk_send_email(subject="hi", message="   ", recipients=json.dumps(["a@x.com"]))

	def test_ticket_type_required(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			bulk_send_email(subject="hi", message="body", recipients=json.dumps(["a@x.com"]))

	def test_invalid_ticket_type_throws(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			bulk_send_email(
				subject="hi",
				message="body",
				recipients=json.dumps(["a@x.com"]),
				ticket_type="DoesNotExist-xyz",
			)

	def test_no_outgoing_email_account_throws_upfront(self):
		_ensure_ticket_type("Question")
		with _outgoing_patch(present=False):
			with self.assertRaises(frappe.OutgoingEmailError):
				bulk_send_email(
					subject="hi",
					message="body",
					recipients=json.dumps(["a@x.com"]),
					ticket_type="Question",
				)


class _JobTestBase(FrappeTestCase):
	"""Runs the real per-student job against a fresh batch record, sendmail mocked."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls._ticket_type = _ensure_ticket_type("Question")
		cls._tickets = []
		cls._batches = []

	@classmethod
	def tearDownClass(cls):
		# Direct row deletes only. delete_doc runs a dynamic-link scan (Email Unsubscribe
		# etc.) that 1205-locks for ~50s against the running workers on this bench; the
		# raw DELETE skips that scan. Orphan child rows are harmless test residue.
		for name in set(cls._tickets):
			frappe.db.delete(
				"Communication", {"reference_doctype": "HD Ticket", "reference_name": str(name)}
			)
			frappe.db.delete("HD Ticket", {"name": name})
		for bid in set(cls._batches):
			frappe.db.delete("Unity Bulk Email Batch", {"name": bid})
		frappe.db.commit()
		super().tearDownClass()

	def _run_job(self, groups, cc_list=None, subject="Test bulk", message="<p>Hello world</p>", fake_students=None):
		batch_id = frappe.generate_hash(length=12)
		frappe.get_doc(
			{
				"doctype": "Unity Bulk Email Batch",
				"batch_id": batch_id,
				"status": "Queued",
				"sender": "Administrator",
				"subject": subject,
				"ticket_type": self._ticket_type,
				"total_count": len(groups),
				"failed_rows": "[]",
				"processed_keys": "[]",
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		self._batches.append(batch_id)

		calls = []
		orig = ext._students_by_name
		try:
			if fake_students is not None:
				ext._students_by_name = lambda ids: fake_students
			with patch("frappe.sendmail", side_effect=lambda **k: calls.append(k)):
				ext._bulk_send_email_job(
					subject=subject,
					message=message,
					groups=groups,
					cc_list=cc_list or [],
					sender="Administrator",
					file_names=[],
					ticket_type=self._ticket_type,
					now=False,
					batch_id=batch_id,
					batch_name=batch_id,
				)
		finally:
			ext._students_by_name = orig

		row = frappe.db.get_value(
			"Unity Bulk Email Batch",
			batch_id,
			["sent_count", "failed_count", "processed_count", "skipped_count", "status", "failed_rows"],
			as_dict=True,
		)
		tickets = frappe.get_all(
			"HD Ticket",
			filters={"custom_bulk_batch_id": batch_id},
			fields=[
				"name",
				"subject",
				"description",
				"raised_by",
				"custom_via_unity_portal",
				"custom_is_bulk_email",
			],
		)
		for t in tickets:
			self._tickets.append(t["name"])
		# student mails vs the single broadcast CC copy
		student_calls = [c for c in calls if not str(c.get("subject", "")).startswith("[Broadcast copy]")]
		cc_calls = [c for c in calls if str(c.get("subject", "")).startswith("[Broadcast copy]")]
		return batch_id, row, tickets, student_calls, cc_calls


class TestBulkSendJob(_JobTestBase):
	def test_one_ticket_and_send_per_group(self):
		_bid, row, tickets, student_calls, _ = self._run_job(
			[{"student": None, "emails": ["a@x.com"]}, {"student": None, "emails": ["b@x.com"]}]
		)
		self.assertEqual(len(tickets), 2)
		self.assertEqual(row.sent_count, 2)
		self.assertEqual(row.failed_count, 0)
		self.assertEqual(row.status, "Completed")
		self.assertEqual(len(student_calls), 2)

	def test_sendmail_uses_visible_recipients_not_bcc(self):
		_bid, _row, _t, student_calls, _ = self._run_job([{"student": None, "emails": ["a@x.com", "b@x.com"]}])
		self.assertEqual(len(student_calls), 1)
		kwargs = student_calls[0]
		self.assertIn("a@x.com", kwargs.get("recipients") or [])
		self.assertFalse(kwargs.get("bcc"))
		self.assertFalse(kwargs.get("cc"))
		self.assertEqual(kwargs.get("expose_recipients"), "header")

	def test_description_is_message_only(self):
		_bid, _row, tickets, _sc, _cc = self._run_job([{"student": None, "emails": ["a@x.com"]}])
		desc = (tickets[0]["description"] or "").lower()
		self.assertIn("hello world", desc)
		self.assertNotIn("recipients (", desc)

	def test_merge_fields_rendered_per_group(self):
		_bid, _row, tickets, _sc, _cc = self._run_job(
			[{"student": None, "emails": ["a@x.com"], "data": {"first_name": "Asha"}}],
			subject="Hi {{first_name}}",
			message="<p>Dear {{first_name}}</p>",
		)
		self.assertEqual(tickets[0]["subject"], "Hi Asha")
		self.assertIn("dear asha", (tickets[0]["description"] or "").lower())

	def test_ticket_flags_set(self):
		_bid, _row, tickets, _sc, _cc = self._run_job([{"student": None, "emails": ["a@x.com"]}])
		self.assertEqual(int(tickets[0]["custom_via_unity_portal"] or 0), 1)
		self.assertEqual(int(tickets[0]["custom_is_bulk_email"] or 0), 1)

	def test_recipient_cap_exceeded_throws(self):
		groups = json.dumps([{"emails": [f"u{i}@x.com" for i in range(RECIPIENT_HARD_CAP + 1)]}])
		with _outgoing_patch():
			with self.assertRaises(frappe.exceptions.ValidationError):
				bulk_send_email(subject="x", message="<p>hi</p>", groups=groups, ticket_type=self._ticket_type)


class TestBulkEmailRegressions(_JobTestBase):
	def test_bug1_emailless_siblings_shared_guardian_both_ticketed(self):
		# BOTH emailless siblings share one guardian -> BOTH get a ticket + mail.
		fake = {
			"sib-1": {"name": "SIB-1", "user": "", "first_name": "A"},
			"sib-2": {"name": "SIB-2", "user": "", "first_name": "B"},
		}
		g = "guardian@x.com"
		_bid, row, tickets, student_calls, _ = self._run_job(
			[{"student": "SIB-1", "emails": [g]}, {"student": "SIB-2", "emails": [g]}],
			fake_students=fake,
		)
		self.assertEqual(len(tickets), 2, "2nd emailless sibling was dropped (BUG-1 regressed)")
		self.assertEqual(row.sent_count, 2)
		self.assertEqual(len(student_calls), 2)

	def test_bug6_cc_sent_once_and_hidden(self):
		_bid, _row, _t, student_calls, cc_calls = self._run_job(
			[{"student": None, "emails": ["a@x.com"]}, {"student": None, "emails": ["b@x.com"]}],
			cc_list=["coordinator@x.com"],
		)
		# Not attached to any per-student mail...
		for c in student_calls:
			self.assertNotIn("coordinator@x.com", c.get("cc") or [])
			self.assertNotIn("coordinator@x.com", c.get("recipients") or [])
		# ...exactly one broadcast copy to the CC list.
		self.assertEqual(len(cc_calls), 1)
		self.assertEqual(cc_calls[0].get("recipients"), ["coordinator@x.com"])

	def test_bug7_merge_value_sanitised_in_mail(self):
		from frappe.utils import sanitize_html

		fake = {"x-1": {"name": "X-1", "user": "x@y.com", "first_name": "<img src=x onerror=alert(1)>"}}
		_bid, _row, _t, student_calls, _ = self._run_job(
			[{"student": "X-1", "emails": ["x@y.com"]}],
			subject="Hi {{first_name}}",
			message=sanitize_html("<p>Hi {{first_name}}</p>"),
			fake_students=fake,
		)
		body = (student_calls[0].get("message") or "").lower()
		subj = (student_calls[0].get("subject") or "").lower()
		self.assertNotIn("onerror", body)
		self.assertNotIn("onerror", subj)

	def test_bug2_failure_recorded_with_reason(self):
		# 2nd group has an unparseable raised_by -> ticket insert fails -> recorded, not lost.
		_bid, row, _t, _sc, _cc = self._run_job(
			[
				{"student": None, "emails": ["good@x.com"]},
				{"student": None, "emails": ["this is not an email"]},
			]
		)
		self.assertEqual(row.failed_count, 1)
		self.assertEqual(row.status, "Completed with Errors")
		failed = json.loads(row.failed_rows or "[]")
		self.assertTrue(failed and failed[0].get("reason"))

	def test_rerun_same_batch_is_idempotent(self):
		# Running the SAME batch twice must not duplicate (worker-restart safety).
		fake = {"z-1": {"name": "Z-1", "user": "z@x.com", "first_name": "Z"}}
		batch_id = frappe.generate_hash(length=12)
		frappe.get_doc(
			{
				"doctype": "Unity Bulk Email Batch",
				"batch_id": batch_id,
				"status": "Queued",
				"sender": "Administrator",
				"subject": "s",
				"ticket_type": self._ticket_type,
				"total_count": 1,
				"failed_rows": "[]",
				"processed_keys": "[]",
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		self._batches.append(batch_id)
		groups = [{"student": "Z-1", "emails": ["z@x.com"]}]
		orig = ext._students_by_name
		try:
			ext._students_by_name = lambda ids: fake
			with patch("frappe.sendmail"):
				for _ in range(2):
					ext._bulk_send_email_job(
						subject="s",
						message="<p>hi</p>",
						groups=groups,
						cc_list=[],
						sender="Administrator",
						file_names=[],
						ticket_type=self._ticket_type,
						now=False,
						batch_id=batch_id,
						batch_name=batch_id,
					)
		finally:
			ext._students_by_name = orig
		tickets = frappe.get_all("HD Ticket", filters={"custom_bulk_batch_id": batch_id}, pluck="name")
		for t in tickets:
			self._tickets.append(t)
		self.assertEqual(len(tickets), 1, "re-run duplicated a ticket")


class TestBulkDedup(FrappeTestCase):
	"""BUG-4: an identical second submission is refused; confirm_resend overrides."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls._ticket_type = _ensure_ticket_type("Question")
		cls._batches = []

	@classmethod
	def tearDownClass(cls):
		# Direct row delete (see _JobTestBase.tearDownClass note).
		for bid in set(cls._batches):
			frappe.db.delete("Unity Bulk Email Batch", {"name": bid})
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		# A unique recipient per test => a unique fingerprint, so a leftover batch from a
		# prior run can never make the FIRST submit look like a duplicate.
		self._email = f"dedup.{frappe.generate_hash(length=8)}@x.com"

	def _submit(self, **kw):
		groups = json.dumps([{"student": None, "emails": [self._email]}])
		defaults = dict(subject="Dedup", message="<p>hi</p>", groups=groups, ticket_type=self._ticket_type)
		defaults.update(kw)
		with (
			patch("frappe.enqueue"),
			_outgoing_patch(),
			patch.object(ext, "_require_unity_access", return_value={"can_view_all_tickets": True}),
		):
			result = bulk_send_email(**defaults)
		if result.get("batch_id"):
			self._batches.append(result["batch_id"])
		return result

	def test_duplicate_submission_is_blocked(self):
		first = self._submit()
		self.assertTrue(first.get("ok"))
		self.assertTrue(first.get("batch_id"))
		second = self._submit()
		self.assertTrue(second.get("duplicate"))

	def test_confirm_resend_overrides_guard(self):
		self._submit()
		again = self._submit(confirm_resend="1")
		self.assertTrue(again.get("ok"))
		self.assertNotIn("duplicate", again or {})


def _delete_if_exists(doctype, name):
	if frappe.db.exists(doctype, name):
		frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)


class TestGetStudentGuardianEmails(FrappeTestCase):
	"""Guardian-lookup endpoint that backs the bulk-email guardian auto-fill."""

	_PREFIX = "BulkEmailGuardianTest"

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
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
				{"doctype": "Guardian", "guardian_name": f"{cls._PREFIX} G{i}", "email_address": email}
			).insert(ignore_permissions=True)
			cls._guardians.append(g.name)
		g_no_email = frappe.get_doc(
			{"doctype": "Guardian", "guardian_name": f"{cls._PREFIX} NoEmail"}
		).insert(ignore_permissions=True)
		cls._guardians.append(g_no_email.name)

		cls._students = []

		def _make_student(local, guardian_names):
			email = f"{local}.{cls._PREFIX.lower()}@x.in".lower()
			doc = frappe.get_doc(
				{
					"doctype": "Student",
					"first_name": f"{cls._PREFIX}-{local}",
					"guardians": [{"guardian": gn} for gn in guardian_names],
				}
			).insert(ignore_permissions=True)
			frappe.db.set_value("Student", doc.name, "user", email, update_modified=False)
			cls._students.append((doc.name, email))
			return doc.name, email

		cls._student_a_name, cls._student_a_email = _make_student("sA", [cls._guardians[0], cls._guardians[1]])
		cls._student_b_name, cls._student_b_email = _make_student("sB", [cls._guardians[0]])
		cls._student_c_name, cls._student_c_email = _make_student("sC", [cls._guardians[3]])
		cls._student_d_name, cls._student_d_email = _make_student("sD", [cls._guardians[3], cls._guardians[2]])
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		# Direct row deletes (delete_doc's dynamic-link scan 1205-locks vs the workers).
		for name, _email in cls._students:
			frappe.db.delete("Student Guardian", {"parent": name})
			frappe.db.delete("Student", {"name": name})
		for gname in cls._guardians:
			frappe.db.delete("Guardian", {"name": gname})
		frappe.db.commit()
		for p in getattr(cls, "_student_patches", []):
			try:
				p.stop()
			except Exception:
				pass
		super().tearDownClass()

	def test_single_student_returns_guardian_emails(self):
		result = get_student_guardian_emails(json.dumps([self._student_a_email]))
		self.assertIn(self._student_a_email, result["mapping"])
		self.assertEqual(
			set(result["mapping"][self._student_a_email]),
			{f"g1.{self._PREFIX.lower()}@x.in", f"g2.{self._PREFIX.lower()}@x.in"},
		)

	def test_shared_guardian_appears_under_both_students(self):
		result = get_student_guardian_emails(
			json.dumps([self._student_a_email, self._student_b_email])
		)
		shared = f"g1.{self._PREFIX.lower()}@x.in"
		self.assertIn(shared, result["mapping"][self._student_a_email])
		self.assertIn(shared, result["mapping"][self._student_b_email])

	def test_guardian_without_email_skipped(self):
		result = get_student_guardian_emails(json.dumps([self._student_c_email]))
		self.assertNotIn(self._student_c_email, result["mapping"])

	def test_permission_required(self):
		original = frappe.session.user
		try:
			frappe.set_user("Guest")
			with self.assertRaises(frappe.PermissionError):
				get_student_guardian_emails(json.dumps([self._student_a_email]))
		finally:
			frappe.set_user(original)
