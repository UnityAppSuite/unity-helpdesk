# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt
"""Agent reply recipients (To / CC / BCC): resolution, validation, Communication
fields, real delayed Email Queue rows + MIME headers, and the missing-sender fallback.

Rollback-only: mail is only ever *queued* (delayed, never flushed or sent)."""

import email
import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.api import unity_helpdesk_ext as ext
from helpdesk.helpdesk.doctype.hd_ticket.hd_ticket import (
	HDTicket,
	_resolve_reply_recipients,
)

RAISER = "reply-raiser@example.test"
AGENT = "reply-agent@example.test"
OUTSIDER = "reply-outsider@example.test"
AUDIT = "audit-copy@example.test"
SENDER = frappe._dict(name=None, email_id="support-test@example.test")


def _ensure_agent(email_id):
	if frappe.db.exists("HD Agent", email_id):
		frappe.delete_doc("HD Agent", email_id, force=True, ignore_permissions=True)
	if frappe.db.exists("User", email_id):
		frappe.delete_doc("User", email_id, force=True, ignore_permissions=True)
	user = frappe.get_doc(
		{"doctype": "User", "email": email_id, "first_name": "Reply", "send_welcome_email": 0, "enabled": 1}
	)
	user.append("roles", {"role": "Agent"})
	user.insert(ignore_permissions=True)
	# HD Ticket's permission_query needs an HD Agent row for get_list; db_insert skips
	# the controller's support-rotation side effects.
	frappe.get_doc(
		{"doctype": "HD Agent", "name": email_id, "user": email_id, "agent_name": email_id, "is_active": 1}
	).db_insert()


class _ReplyBase(FrappeTestCase):
	audit = ()

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_agent(AGENT)
		_ensure_agent(OUTSIDER)

	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		frappe.set_user("Administrator")
		frappe.db.set_single_value("HD Settings", "instantly_send_email", 0)
		frappe.db.set_single_value("HD Settings", "skip_email_workflow", 0)
		# Assignment-rule notifications would mail through the muted site's dummy
		# Email Account (no email_id) and crash; irrelevant here.
		notify = patch("frappe.desk.form.assign_to.notify_assignment")
		notify.start()
		self.addCleanup(notify.stop)
		self.ticket = frappe.get_doc(
			{
				"doctype": "HD Ticket",
				"subject": "reply recipients",
				"raised_by": RAISER,
				"description": "customer text",
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value("HD Ticket", self.ticket.name, "_assign", json.dumps([AGENT]), update_modified=False)
		self.ticket = frappe.get_doc("HD Ticket", self.ticket.name)
		for p in (
			patch.object(HDTicket, "sender_email", return_value=SENDER),
			patch("helpdesk.api.unity_helpdesk._default_bulk_recipients", side_effect=lambda: list(self.audit)),
		):
			p.start()
			self.addCleanup(p.stop)

	def _comms(self):
		return frappe.get_all(
			"Communication",
			filters={"reference_doctype": "HD Ticket", "reference_name": self.ticket.name, "sent_or_received": "Sent"},
			fields=["name", "recipients", "cc", "bcc", "content", "email_account"],
			order_by="creation asc",
		)

	def _queue(self, communication):
		rows = frappe.get_all("Email Queue", filters={"communication": communication}, pluck="name")
		self.assertEqual(len(rows), 1, "expected exactly one queued mail")
		doc = frappe.get_doc("Email Queue", rows[0])
		msg = email.message_from_string(doc.message)
		return doc, msg, sorted(r.recipient for r in doc.recipients)


class TestResolver(_ReplyBase):
	def resolve(self, **kw):
		return _resolve_reply_recipients(self.ticket, **kw)

	def test_omitted_to_keeps_legacy_default(self):
		self.assertEqual(self.resolve(), {"recipients": RAISER, "cc": None, "bcc": None})

	def test_omitted_to_with_audit_keeps_legacy_bcc_injection(self):
		self.audit = (AUDIT,)
		self.assertEqual(self.resolve(bcc="x@example.test")["bcc"], f"x@example.test, {AUDIT}")

	def test_separators_list_and_json_forms(self):
		a = self.resolve(recipients="a@example.test; b@example.test\nc@example.test, d@example.test")
		self.assertEqual(a["recipients"], "a@example.test, b@example.test, c@example.test, d@example.test")
		b = self.resolve(recipients=["a@example.test", "b@example.test, c@example.test"])
		self.assertEqual(b["recipients"], "a@example.test, b@example.test, c@example.test")
		c = self.resolve(recipients=json.dumps(["a@example.test"]), cc=json.dumps(["b@example.test"]))
		self.assertEqual((c["recipients"], c["cc"]), ("a@example.test", "b@example.test"))

	def test_dedupe_is_case_insensitive_and_first_category_wins(self):
		self.audit = ("AUDIT@example.test", "bcc@example.test")
		out = self.resolve(
			recipients="A@example.test, a@EXAMPLE.test",
			cc="a@example.test, C@example.test",
			bcc="c@example.test, bcc@example.test",
		)
		self.assertEqual(out["recipients"], "A@example.test")
		self.assertEqual(out["cc"], "C@example.test")
		self.assertEqual(out["bcc"], "bcc@example.test, AUDIT@example.test")

	def test_audit_not_duplicated_when_already_visible(self):
		self.audit = (AUDIT,)
		out = self.resolve(recipients=AUDIT)
		self.assertEqual(out["bcc"], "")

	def test_empty_to_rejected(self):
		for empty in ("", "  ", [], "[]", ";,\n"):
			with self.assertRaises(frappe.ValidationError, msg=repr(empty)):
				self.resolve(recipients=empty)

	def test_malformed_inputs_rejected(self):
		bad = [
			("not-an-address", None),
			("a@example.test, nope", None),
			(["a@example.test", 5], None),
			("[\"a@example.test\"", None),
			({"a": 1}, None),
			("a@example.test", "b@example.test\r\nBcc: evil@example.test"),
			(["a@example.test\nBcc: evil@example.test"], None),
			("a@example.test", ["ok@example.test", None]),
		]
		for to, cc in bad:
			with self.assertRaises(frappe.ValidationError, msg=repr((to, cc))):
				self.resolve(recipients=to, cc=cc)

	def test_invalid_cc_error_names_field(self):
		with self.assertRaises(frappe.InvalidEmailAddressError) as ctx:
			self.resolve(recipients="a@example.test", cc="broken")
		self.assertIn("CC", str(ctx.exception))

	def test_cc_bcc_inherit_vs_clear(self):
		frappe.get_doc(
			{
				"doctype": "Communication",
				"communication_type": "Communication",
				"sent_or_received": "Sent",
				"subject": "earlier",
				"content": "earlier",
				"recipients": RAISER,
				"cc": "old-cc@example.test",
				"bcc": "old-bcc@example.test",
				"reference_doctype": "HD Ticket",
				"reference_name": self.ticket.name,
			}
		).insert(ignore_permissions=True)
		inherited = self.resolve(recipients="a@example.test")
		self.assertEqual((inherited["cc"], inherited["bcc"]), ("old-cc@example.test", "old-bcc@example.test"))
		cleared = self.resolve(recipients="a@example.test", cc=[], bcc="")
		self.assertEqual((cleared["cc"], cleared["bcc"]), ("", ""))
		self.audit = (AUDIT,)
		self.assertEqual(self.resolve(recipients="a@example.test", bcc=[])["bcc"], AUDIT)

	def test_resolution_is_idempotent(self):
		first = self.resolve(recipients="a@example.test", cc="b@example.test", bcc="c@example.test")
		second = self.resolve(recipients=first["recipients"], cc=first["cc"], bcc=first["bcc"])
		self.assertEqual(first, second)


class TestReplyEndpoint(_ReplyBase):
	def test_explicit_recipients_reach_communication_and_queue(self):
		self.audit = (AUDIT,)
		frappe.set_user(AGENT)
		res = ext.reply(
			self.ticket.name,
			"<p>hello</p>",
			recipients="parent-new@example.test, guardian@example.test",
			cc="principal@example.test; viceprincipal@example.test",
			bcc="counsellor@example.test, wellbeing@example.test",
		)
		self.assertTrue(res["ok"])
		self.assertNotIn("warning", res)
		(comm,) = self._comms()
		self.assertEqual(comm.recipients, "parent-new@example.test, guardian@example.test")
		self.assertEqual(comm.cc, "principal@example.test, viceprincipal@example.test")
		self.assertEqual(comm.bcc, f"counsellor@example.test, wellbeing@example.test, {AUDIT}")
		self.assertNotIn(RAISER, comm.recipients)
		# raiser/customer context unchanged
		self.assertEqual(frappe.db.get_value("HD Ticket", self.ticket.name, "raised_by"), RAISER)

		queue, msg, envelope = self._queue(comm.name)
		self.assertEqual(
			envelope,
			sorted(
				[
					"parent-new@example.test",
					"guardian@example.test",
					"principal@example.test",
					"viceprincipal@example.test",
					"counsellor@example.test",
					"wellbeing@example.test",
					AUDIT,
				]
			),
		)
		self.assertEqual(queue.status, "Not Sent")
		# Frappe's queue builder de-duplicates through a set, so header order is not stable.
		self.assertEqual(
			sorted(a.strip() for a in msg["To"].split(",")),
			["guardian@example.test", "parent-new@example.test"],
		)
		self.assertEqual(
			sorted(a.strip() for a in msg["CC"].split(",")),
			["principal@example.test", "viceprincipal@example.test"],
		)
		self.assertIsNone(msg["Bcc"])
		for hidden in ("counsellor@example.test", "wellbeing@example.test", AUDIT):
			self.assertNotIn(hidden, msg["To"] + msg["CC"])
		self.assertEqual(res["communication"]["recipients"], comm.recipients)

	def test_omitted_recipients_legacy_default(self):
		ext.reply(self.ticket.name, "<p>hi</p>")
		(comm,) = self._comms()
		self.assertEqual(comm.recipients, RAISER)
		_q, msg, envelope = self._queue(comm.name)
		self.assertEqual(envelope, [RAISER])

	def test_cleared_cc_bcc_not_inherited_but_audit_still_applied(self):
		ext.reply(self.ticket.name, "<p>one</p>", recipients="a@example.test", cc="k@example.test", bcc="b@example.test")
		self.audit = (AUDIT,)
		ext.reply(self.ticket.name, "<p>two</p>", recipients="a@example.test", cc=[], bcc=[])
		second = self._comms()[-1]
		self.assertEqual((second.cc or "", second.bcc), ("", AUDIT))
		_q, msg, envelope = self._queue(second.name)
		self.assertEqual(envelope, sorted(["a@example.test", AUDIT]))
		self.assertIsNone(msg["CC"])

	def test_invalid_input_writes_nothing(self):
		before_comms = frappe.db.count("Communication", {"reference_name": self.ticket.name})
		before_queue = frappe.db.count("Email Queue")
		for kw in (
			{"recipients": ""},
			{"recipients": "a@example.test", "cc": "bad"},
			{"recipients": "a@example.test, bad"},
			{"recipients": "a@example.test", "bcc": ["x@example.test", 3]},
		):
			with self.assertRaises(frappe.ValidationError, msg=repr(kw)):
				ext.reply(self.ticket.name, "<p>x</p>", **kw)
		self.assertEqual(frappe.db.count("Communication", {"reference_name": self.ticket.name}), before_comms)
		self.assertEqual(frappe.db.count("Email Queue"), before_queue)

	def test_ticket_access_denied_for_unassigned_agent(self):
		frappe.set_user(OUTSIDER)
		with self.assertRaises(frappe.PermissionError):
			ext.reply(self.ticket.name, "<p>x</p>", recipients="a@example.test")
		self.assertEqual(self._comms(), [])

	def test_attachment_is_linked_and_template_personalised(self):
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "reply-note.txt",
				"content": "attachment body",
				"attached_to_doctype": "HD Ticket",
				"attached_to_name": self.ticket.name,
			}
		).insert(ignore_permissions=True)
		with patch.object(ext, "_merge_context_for_email", return_value={"first_name": "Asha"}):
			ext.reply(
				self.ticket.name,
				"<p>Hello {{ first_name }}</p>",
				recipients="a@example.test",
				attachments=[file_doc.name],
			)
		(comm,) = self._comms()
		self.assertIn("Hello Asha", comm.content)
		file_doc.reload()
		self.assertEqual((file_doc.attached_to_doctype, file_doc.attached_to_name), ("Communication", comm.name))
		queue, _msg, _env = self._queue(comm.name)
		self.assertIn("reply-note.txt", queue.attachments)

	def test_missing_sender_falls_back_to_exactly_one_unsent_communication(self):
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "fallback.txt",
				"content": "fb",
				"attached_to_doctype": "HD Ticket",
				"attached_to_name": self.ticket.name,
			}
		).insert(ignore_permissions=True)
		self.audit = (AUDIT,)
		before_queue = frappe.db.count("Email Queue")
		with patch.object(HDTicket, "sender_email", return_value=None):
			res = ext.reply(
				self.ticket.name,
				"<p>offline</p>",
				recipients="a@example.test",
				cc="c@example.test",
				bcc="b@example.test",
				attachments=[file_doc.name],
			)
		self.assertIs(res["email_sent"], False)
		self.assertIn("email was not sent", res["warning"])
		(comm,) = self._comms()
		self.assertEqual(
			(comm.recipients, comm.cc, comm.bcc),
			("a@example.test", "c@example.test", f"b@example.test, {AUDIT}"),
		)
		self.assertEqual(frappe.db.count("Email Queue"), before_queue)
		file_doc.reload()
		self.assertEqual(file_doc.attached_to_name, comm.name)

	def test_other_send_failures_are_not_swallowed(self):
		with patch("frappe.sendmail", side_effect=frappe.ValidationError("sendmail exploded")):
			with self.assertRaises(frappe.ValidationError):
				ext.reply(self.ticket.name, "<p>x</p>", recipients="a@example.test")
		with patch.object(HDTicket, "sender_email", side_effect=frappe.ValidationError("no sender email found")):
			with self.assertRaises(frappe.ValidationError):
				ext.reply(self.ticket.name, "<p>x</p>", recipients="a@example.test")

	def test_detail_exposes_default_recipients(self):
		frappe.set_user(AGENT)
		self.audit = (AUDIT,)
		detail = ext.get_ticket_detail(self.ticket.name)
		self.assertEqual(
			detail.reply_recipients, {"recipients": RAISER, "cc": "", "bcc": ""}
		)
