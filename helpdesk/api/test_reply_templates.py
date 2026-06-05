# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt
"""Tests for reply-template endpoints (HD Canned Response picker)."""

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.api.reply_templates import (
	LIST_MAX_LIMIT,
	create_reply_template,
	create_reply_template_category,
	delete_reply_template,
	delete_reply_template_category,
	get_reply_template_categories,
	list_reply_templates,
	render_reply_template,
	update_reply_template,
	update_reply_template_category,
)


CAT_DOCTYPE = "HD Canned Response Category"
TPL_DOCTYPE = "HD Saved Reply"


def _delete_if_exists(doctype, name):
	if frappe.db.exists(doctype, name):
		frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)


def _ensure_category(title, is_active=1):
	if frappe.db.exists(CAT_DOCTYPE, title):
		doc = frappe.get_doc(CAT_DOCTYPE, title)
		doc.is_active = is_active
		doc.save(ignore_permissions=True)
		return doc
	return frappe.get_doc(
		{"doctype": CAT_DOCTYPE, "title": title, "is_active": is_active}
	).insert(ignore_permissions=True)


def _ensure_template(title, category, message, *, language="English", subject_template="", is_active=1):
	if frappe.db.exists(TPL_DOCTYPE, title):
		_delete_if_exists(TPL_DOCTYPE, title)
	return frappe.get_doc(
		{
			"doctype": TPL_DOCTYPE,
			"title": title,
			"message": message,
			"category": category,
			"language": language,
			"subject_template": subject_template,
			"is_active": is_active,
		}
	).insert(ignore_permissions=True)


class TestCategories(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_category("Refunds", is_active=1)
		_ensure_category("InactiveCat", is_active=0)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		for name in ("Refunds", "InactiveCat"):
			_delete_if_exists(CAT_DOCTYPE, name)
		frappe.db.commit()
		super().tearDownClass()

	def test_returns_active_categories_only(self):
		rows = get_reply_template_categories()
		titles = [row["title"] for row in rows]
		self.assertIn("Refunds", titles)
		self.assertNotIn("InactiveCat", titles)

	def test_sorted_by_title_asc(self):
		_ensure_category("Aaa", is_active=1)
		try:
			rows = get_reply_template_categories()
			titles = [r["title"] for r in rows]
			# Aaa appears before Refunds when sorted ascending
			self.assertLess(titles.index("Aaa"), titles.index("Refunds"))
		finally:
			_delete_if_exists(CAT_DOCTYPE, "Aaa")
			frappe.db.commit()


class TestListTemplates(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_category("ListCat", is_active=1)
		_ensure_category("ListCatB", is_active=1)
		_ensure_template(
			"ListTpl-Refund-Note",
			"ListCat",
			"<p>Hello, your refund is being processed.</p>",
		)
		_ensure_template(
			"ListTpl-Admission-Note",
			"ListCat",
			"<p>Your admission is confirmed.</p>",
		)
		_ensure_template(
			"ListTpl-OtherCat",
			"ListCatB",
			"<p>Other category content.</p>",
		)
		_ensure_template(
			"ListTpl-Inactive",
			"ListCat",
			"<p>Disabled template.</p>",
			is_active=0,
		)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		for name in (
			"ListTpl-Refund-Note",
			"ListTpl-Admission-Note",
			"ListTpl-OtherCat",
			"ListTpl-Inactive",
		):
			_delete_if_exists(TPL_DOCTYPE, name)
		for name in ("ListCat", "ListCatB"):
			_delete_if_exists(CAT_DOCTYPE, name)
		frappe.db.commit()
		super().tearDownClass()

	def test_filters_by_category(self):
		rows = list_reply_templates(category="ListCat")
		titles = {row["title"] for row in rows}
		self.assertIn("ListTpl-Refund-Note", titles)
		self.assertIn("ListTpl-Admission-Note", titles)
		self.assertNotIn("ListTpl-OtherCat", titles)
		self.assertNotIn("ListTpl-Inactive", titles)

	def test_inactive_templates_hidden(self):
		rows = list_reply_templates()
		titles = {row["title"] for row in rows}
		self.assertNotIn("ListTpl-Inactive", titles)

	def test_filters_by_search_title(self):
		rows = list_reply_templates(search="Refund-Note")
		titles = {row["title"] for row in rows}
		self.assertIn("ListTpl-Refund-Note", titles)
		self.assertNotIn("ListTpl-Admission-Note", titles)

	def test_filters_by_search_body(self):
		# Search hits the message body via LIKE
		rows = list_reply_templates(search="admission is confirmed")
		titles = {row["title"] for row in rows}
		self.assertIn("ListTpl-Admission-Note", titles)
		self.assertNotIn("ListTpl-Refund-Note", titles)

	def test_limit_clamped_to_max(self):
		rows = list_reply_templates(limit=99999)
		# We can't assert exact size — depends on existing data — but the call must succeed
		# and not return more than LIST_MAX_LIMIT.
		self.assertLessEqual(len(rows), LIST_MAX_LIMIT)

	def test_response_shape(self):
		rows = list_reply_templates(category="ListCat")
		self.assertTrue(rows)
		row = rows[0]
		# Required keys present
		for key in ("name", "title", "category", "modified", "body_preview"):
			self.assertIn(key, row, f"missing field: {key}")
		# Heavy `message` field should NOT be in the list response
		self.assertNotIn("message", row)
		# body_preview is stripped of HTML tags
		self.assertNotIn("<p>", row["body_preview"])


class TestRenderTemplate(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_category("RenderCat", is_active=1)
		# Canned responses are STATIC text — we just verify the body is returned
		# verbatim (after HTML sanitization) without any Jinja substitution.
		cls._static_tpl = _ensure_template(
			"RenderTpl-Static",
			"RenderCat",
			"<p>Hi there, thanks for writing in. We'll get back to you soon.</p>",
			subject_template="Static subject line",
		)
		cls._xss_tpl = _ensure_template(
			"RenderTpl-Xss",
			"RenderCat",
			'<p>safe text</p><script>alert(1)</script><img src=x onerror="alert(2)">',
		)
		cls._inactive_tpl = _ensure_template(
			"RenderTpl-Inactive",
			"RenderCat",
			"<p>nope</p>",
			is_active=0,
		)

		# A ticket so we can verify permission gating works against a real id.
		cls._ticket = frappe.get_doc(
			{
				"doctype": "HD Ticket",
				"subject": "RENDER_SUBJECT_FIXTURE",
				"raised_by": "render-test@example.com",
				"description": "test body",
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		for name in (
			"RenderTpl-Static",
			"RenderTpl-Xss",
			"RenderTpl-Inactive",
		):
			_delete_if_exists(TPL_DOCTYPE, name)
		_delete_if_exists(CAT_DOCTYPE, "RenderCat")
		if frappe.db.exists("HD Ticket", cls._ticket.name):
			frappe.delete_doc("HD Ticket", cls._ticket.name, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	def test_static_body_returned_verbatim(self):
		# No Jinja substitution — body is returned exactly as saved.
		result = render_reply_template("RenderTpl-Static", ticket_name=None)
		self.assertIn("Hi there", result["body"])
		self.assertEqual(result["subject"], "Static subject line")
		self.assertEqual(result["warnings"], [])

	def test_no_substitution_even_with_jinja_lookalike(self):
		# Curly braces in a body must be returned as-is, not interpreted.
		looksjinja = _ensure_template(
			"RenderTpl-LooksJinja",
			"RenderCat",
			"<p>Order {{ doc.raised_by }} is ready.</p>",
		)
		try:
			result = render_reply_template(looksjinja.name)
			self.assertIn("{{ doc.raised_by }}", result["body"])
		finally:
			_delete_if_exists(TPL_DOCTYPE, "RenderTpl-LooksJinja")
			frappe.db.commit()

	def test_sanitizes_html(self):
		# sanitize_html escapes <script> tags and strips onerror handlers
		result = render_reply_template("RenderTpl-Xss", ticket_name=None)
		body_lower = result["body"].lower()
		# Active <script> tag must not survive
		self.assertNotIn("<script>", body_lower)
		# Inline event handlers must be stripped
		self.assertNotIn("onerror", body_lower)
		# Inert text content is fine
		self.assertIn("safe text", body_lower)

	def test_inactive_template_throws(self):
		with self.assertRaises(frappe.PermissionError):
			render_reply_template("RenderTpl-Inactive", ticket_name=None)

	def test_render_requires_unity_access(self):
		original = frappe.session.user
		try:
			frappe.set_user("Guest")
			with self.assertRaises(frappe.PermissionError):
				render_reply_template("RenderTpl-Static", ticket_name=None)
		finally:
			frappe.set_user(original)


class TestCategoryCrud(FrappeTestCase):
	def tearDown(self):
		for name in ("CrudCat", "CrudCat-Renamed", "CrudCat-Linked"):
			_delete_if_exists(CAT_DOCTYPE, name)
		_delete_if_exists(TPL_DOCTYPE, "CrudCat-Linked-Tpl")
		frappe.db.commit()

	def test_create_category(self):
		res = create_reply_template_category(title="CrudCat", color="#ff0000", description="desc")
		self.assertEqual(res["title"], "CrudCat")
		self.assertEqual(res["color"], "#ff0000")
		self.assertTrue(frappe.db.exists(CAT_DOCTYPE, "CrudCat"))

	def test_create_empty_title_throws(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			create_reply_template_category(title="")

	def test_create_duplicate_throws(self):
		create_reply_template_category(title="CrudCat")
		with self.assertRaises(frappe.exceptions.ValidationError):
			create_reply_template_category(title="CrudCat")

	def test_update_category(self):
		create_reply_template_category(title="CrudCat")
		res = update_reply_template_category("CrudCat", title="CrudCat-Renamed", is_active=0)
		self.assertEqual(res["title"], "CrudCat-Renamed")
		self.assertEqual(res["is_active"], 0)

	def test_delete_category(self):
		create_reply_template_category(title="CrudCat")
		delete_reply_template_category("CrudCat")
		self.assertFalse(frappe.db.exists(CAT_DOCTYPE, "CrudCat"))

	def test_delete_in_use_category_throws(self):
		create_reply_template_category(title="CrudCat-Linked")
		_ensure_template("CrudCat-Linked-Tpl", "CrudCat-Linked", "<p>body</p>")
		with self.assertRaises(frappe.exceptions.ValidationError):
			delete_reply_template_category("CrudCat-Linked")


class TestTemplateCrud(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_category("CrudTplCat", is_active=1)
		_ensure_category("CrudTplCat-Other", is_active=1)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		for name in ("CrudTpl-A", "CrudTpl-Renamed"):
			_delete_if_exists(TPL_DOCTYPE, name)
		for name in ("CrudTplCat", "CrudTplCat-Other"):
			_delete_if_exists(CAT_DOCTYPE, name)
		frappe.db.commit()
		super().tearDownClass()

	def tearDown(self):
		for name in ("CrudTpl-A", "CrudTpl-Renamed"):
			_delete_if_exists(TPL_DOCTYPE, name)
		frappe.db.commit()

	def test_create_template(self):
		res = create_reply_template(
			title="CrudTpl-A",
			category="CrudTplCat",
			message="<p>{{ doc.subject }}</p>",
			subject_template="Hi {{ doc.raised_by }}",
		)
		self.assertEqual(res["title"], "CrudTpl-A")
		self.assertEqual(res["category"], "CrudTplCat")
		self.assertEqual(res["subject_template"], "Hi {{ doc.raised_by }}")
		self.assertTrue(frappe.db.exists(TPL_DOCTYPE, "CrudTpl-A"))

	def test_create_requires_valid_category(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			create_reply_template(title="CrudTpl-A", category="DoesNotExist", message="<p>x</p>")

	def test_create_requires_message(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			create_reply_template(title="CrudTpl-A", category="CrudTplCat", message="")

	def test_update_template(self):
		create_reply_template(title="CrudTpl-A", category="CrudTplCat", message="<p>x</p>")
		res = update_reply_template(
			name="CrudTpl-A",
			category="CrudTplCat-Other",
			message="<p>updated</p>",
			is_active=0,
		)
		self.assertEqual(res["category"], "CrudTplCat-Other")
		self.assertIn("updated", res["message"])
		self.assertEqual(res["is_active"], 0)

	def test_delete_template(self):
		create_reply_template(title="CrudTpl-A", category="CrudTplCat", message="<p>x</p>")
		delete_reply_template("CrudTpl-A")
		self.assertFalse(frappe.db.exists(TPL_DOCTYPE, "CrudTpl-A"))


class TestApiPermissions(FrappeTestCase):
	def test_list_requires_unity_access(self):
		original = frappe.session.user
		try:
			frappe.set_user("Guest")
			with self.assertRaises(frappe.PermissionError):
				list_reply_templates()
		finally:
			frappe.set_user(original)

	def test_categories_requires_unity_access(self):
		original = frappe.session.user
		try:
			frappe.set_user("Guest")
			with self.assertRaises(frappe.PermissionError):
				get_reply_template_categories()
		finally:
			frappe.set_user(original)
