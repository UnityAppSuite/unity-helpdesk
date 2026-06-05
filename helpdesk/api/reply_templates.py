"""Reply Template API for the Unity Helpdesk picker.

Templates are stored as `HD Canned Response` (existing doctype, extended with
custom fields category / language / subject_template / is_active via
`helpdesk.patches.unity_canned_response_extension`). The picker uses three
endpoints:

- get_reply_template_categories — fills the category dropdown
- list_reply_templates — filters by category/language/search
- render_reply_template — substitutes Jinja and returns html ready to insert

All endpoints are gated behind `_require_unity_access`. `render_reply_template`
additionally requires ticket access when a ticket_name is provided.
"""
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, cstr, sanitize_html, strip_html

from helpdesk.api.unity_helpdesk import (
	_require_ticket_access,
	_require_unity_access,
)


TEMPLATE_DOCTYPE = "HD Saved Reply"
CATEGORY_DOCTYPE = "HD Canned Response Category"

LIST_DEFAULT_LIMIT = 50
LIST_MAX_LIMIT = 200
BODY_PREVIEW_CHARS = 120


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_reply_template_categories(include_inactive=False):
	"""List categories. By default returns only active ones (used by the agent
	picker). The Settings page passes include_inactive=True so admins can manage
	disabled rows too."""
	_require_unity_access()
	if not frappe.db.exists("DocType", CATEGORY_DOCTYPE):
		return []
	filters = {} if cint(include_inactive) else {"is_active": 1}
	return frappe.get_all(
		CATEGORY_DOCTYPE,
		filters=filters,
		fields=["name", "title", "color", "description", "is_active"],
		order_by="title asc",
	)


# ---------------------------------------------------------------------------
# Template list
# ---------------------------------------------------------------------------


@frappe.whitelist()
def list_reply_templates(category=None, search=None, language=None, limit=LIST_DEFAULT_LIMIT, include_inactive=False):
	_require_unity_access()

	limit = max(1, min(cint(limit) or LIST_DEFAULT_LIMIT, LIST_MAX_LIMIT))

	filters: dict = {}
	# Default to active-only for the agent picker. Admin Settings passes
	# include_inactive=1 so disabled templates remain manageable.
	if not cint(include_inactive) and frappe.db.has_column(TEMPLATE_DOCTYPE, "is_active"):
		filters["is_active"] = 1
	if category and frappe.db.has_column(TEMPLATE_DOCTYPE, "category"):
		filters["category"] = category
	if language and frappe.db.has_column(TEMPLATE_DOCTYPE, "language"):
		filters["language"] = language

	or_filters = None
	q = cstr(search or "").strip()
	if q:
		pattern = f"%{q}%"
		or_filters = [
			[TEMPLATE_DOCTYPE, "title", "like", pattern],
			[TEMPLATE_DOCTYPE, "message", "like", pattern],
		]

	# Build the field list dynamically so the API still works on un-migrated sites.
	fields = ["name", "title", "message", "modified"]
	for field in ("category", "language", "subject_template", "is_active"):
		if frappe.db.has_column(TEMPLATE_DOCTYPE, field):
			fields.append(field)

	rows = frappe.get_list(
		TEMPLATE_DOCTYPE,
		fields=fields,
		filters=filters,
		or_filters=or_filters,
		order_by="modified desc",
		page_length=limit,
	)

	# Build a small preview server-side and drop the heavy message field from the response.
	for row in rows:
		preview = strip_html(cstr(row.get("message") or "")).strip()
		row["body_preview"] = preview[:BODY_PREVIEW_CHARS]
		row.pop("message", None)

	return rows


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


@frappe.whitelist()
def render_reply_template(name, ticket_name=None):
	"""Return a saved canned response. Canned = static text — no Jinja
	substitution. The body and subject are returned exactly as the admin
	saved them, sanitized to strip any unsafe HTML. The `ticket_name`
	argument is still accepted so the SPA can do a permission check (an
	agent can only render a template against a ticket they can read), but
	the ticket's fields are NOT interpolated into the body.
	"""
	capabilities = _require_unity_access()
	if ticket_name:
		_require_ticket_access(ticket_name, capabilities)

	if not name:
		frappe.throw(_("Template name is required"))

	template = frappe.get_doc(TEMPLATE_DOCTYPE, name)

	if frappe.db.has_column(TEMPLATE_DOCTYPE, "is_active") and not template.get("is_active"):
		frappe.throw(_("This template is no longer active"), frappe.PermissionError)

	subject = cstr(template.get("subject_template") or "").strip()
	body_raw = cstr(template.get("message") or "")
	body = sanitize_html(body_raw) if body_raw else ""

	return {
		"name": template.name,
		"title": template.get("title"),
		"subject": subject,
		"body": body,
		"warnings": [],
	}


# ---------------------------------------------------------------------------
# Category CRUD (admin)
# ---------------------------------------------------------------------------


def _require_manage_templates():
	"""Gate admin-only template management. Helpdesk Admin / Super Admin /
	System Manager can manage; plain Agents cannot."""
	capabilities = _require_unity_access()
	if not (capabilities.can_manage_unity_settings or capabilities.can_manage_agents):
		frappe.throw(
			_("You are not allowed to manage reply templates"), frappe.PermissionError
		)
	return capabilities


@frappe.whitelist()
def create_reply_template_category(title, color=None, description=None):
	_require_manage_templates()
	title = cstr(title or "").strip()
	if not title:
		frappe.throw(_("Title is required"))
	if frappe.db.exists(CATEGORY_DOCTYPE, title):
		frappe.throw(_("A category named '{0}' already exists").format(title))
	doc = frappe.get_doc(
		{
			"doctype": CATEGORY_DOCTYPE,
			"title": title,
			"color": cstr(color or "").strip() or None,
			"description": cstr(description or "").strip() or None,
			"is_active": 1,
		}
	).insert()
	return _category_response(doc)


@frappe.whitelist()
def update_reply_template_category(name, title=None, color=None, description=None, is_active=None):
	_require_manage_templates()
	if not name:
		frappe.throw(_("Category name is required"))
	# Title is the autoname — rename via frappe.rename_doc so child rows that
	# Link to this category (HD Canned Response.category) are updated too.
	if title is not None:
		new_title = cstr(title).strip()
		if not new_title:
			frappe.throw(_("Title cannot be empty"))
		if new_title != name:
			if frappe.db.exists(CATEGORY_DOCTYPE, new_title):
				frappe.throw(_("A category named '{0}' already exists").format(new_title))
			frappe.rename_doc(CATEGORY_DOCTYPE, name, new_title, force=True)
			name = new_title
	doc = frappe.get_doc(CATEGORY_DOCTYPE, name)
	if color is not None:
		doc.color = cstr(color or "").strip() or None
	if description is not None:
		doc.description = cstr(description or "").strip() or None
	if is_active is not None:
		doc.is_active = 1 if cint(is_active) else 0
	doc.save()
	return _category_response(doc)


@frappe.whitelist()
def delete_reply_template_category(name):
	_require_manage_templates()
	if not name:
		frappe.throw(_("Category name is required"))
	# Refuse to delete categories still in use — surface a clear error to the admin.
	usage = frappe.db.count(TEMPLATE_DOCTYPE, {"category": name})
	if usage:
		frappe.throw(
			_("Cannot delete category '{0}' — {1} template(s) still use it").format(name, usage)
		)
	frappe.delete_doc(CATEGORY_DOCTYPE, name)
	return {"ok": True, "name": name}


def _category_response(doc):
	return {
		"name": doc.name,
		"title": doc.title,
		"color": doc.color,
		"description": doc.description,
		"is_active": cint(doc.is_active),
	}


# ---------------------------------------------------------------------------
# Template CRUD (admin)
# ---------------------------------------------------------------------------


@frappe.whitelist()
def create_reply_template(title, category, message, language="English", subject_template=None):
	_require_manage_templates()
	title = cstr(title or "").strip()
	if not title:
		frappe.throw(_("Title is required"))
	if frappe.db.exists(TEMPLATE_DOCTYPE, title):
		frappe.throw(_("A template named '{0}' already exists").format(title))
	if not category or not frappe.db.exists(CATEGORY_DOCTYPE, category):
		frappe.throw(_("A valid category is required"))
	if not cstr(message or "").strip():
		frappe.throw(_("Message body is required"))
	doc = frappe.get_doc(
		{
			"doctype": TEMPLATE_DOCTYPE,
			"title": title,
			"category": category,
			"language": cstr(language or "English").strip() or "English",
			"subject_template": cstr(subject_template or "").strip() or None,
			"message": message,
			"is_active": 1,
		}
	).insert()
	return _template_response(doc)


@frappe.whitelist()
def update_reply_template(name, title=None, category=None, message=None, language=None, subject_template=None, is_active=None):
	_require_manage_templates()
	if not name:
		frappe.throw(_("Template name is required"))
	if title is not None:
		new_title = cstr(title).strip()
		if not new_title:
			frappe.throw(_("Title cannot be empty"))
		if new_title != name:
			if frappe.db.exists(TEMPLATE_DOCTYPE, new_title):
				frappe.throw(_("A template named '{0}' already exists").format(new_title))
			frappe.rename_doc(TEMPLATE_DOCTYPE, name, new_title, force=True)
			name = new_title
	doc = frappe.get_doc(TEMPLATE_DOCTYPE, name)
	if category is not None:
		if not frappe.db.exists(CATEGORY_DOCTYPE, category):
			frappe.throw(_("A valid category is required"))
		doc.category = category
	if message is not None:
		if not cstr(message).strip():
			frappe.throw(_("Message body cannot be empty"))
		doc.message = message
	if language is not None:
		doc.language = cstr(language or "English").strip() or "English"
	if subject_template is not None:
		doc.subject_template = cstr(subject_template or "").strip() or None
	if is_active is not None:
		doc.is_active = 1 if cint(is_active) else 0
	doc.save()
	return _template_response(doc)


@frappe.whitelist()
def delete_reply_template(name):
	_require_manage_templates()
	if not name:
		frappe.throw(_("Template name is required"))
	frappe.delete_doc(TEMPLATE_DOCTYPE, name)
	return {"ok": True, "name": name}


def _template_response(doc):
	return {
		"name": doc.name,
		"title": doc.title,
		"category": doc.category,
		"language": doc.get("language") or "English",
		"subject_template": doc.get("subject_template") or "",
		"message": doc.message,
		"is_active": cint(doc.get("is_active") or 0),
	}
