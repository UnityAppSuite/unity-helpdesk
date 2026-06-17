"""Frappe Email Template (core doctype) access for the Unity bulk-email composer.

Bulk email uses Frappe **Email Templates** as the primary, Jinja-aware body source:
the composer lists templates and loads a chosen template's RAW subject + body into
the editor. The Jinja is rendered per recipient at SEND time (see
unity_helpdesk_ext._bulk_send_email_job / _safe_render), never here — so this
returns the template text exactly as authored. HD Canned Responses remain the
secondary, static-insert option via helpdesk.api.reply_templates.

Both endpoints are whitelisted and gated on the same capability as bulk_send_email
(can_view_all_tickets). frappe.get_all / frappe.get_doc are used (not get_list) so a
helpdesk admin without an explicit Email Template read grant can still use them —
our own capability check is the access control.
"""
import frappe
from frappe import _
from frappe.utils import cstr

from helpdesk.api.unity_helpdesk import _require_unity_access

EMAIL_TEMPLATE_DOCTYPE = "Email Template"
# Category support is provided by the edu_quality app (the "Email Template Category"
# doctype + an optional `email_template_category` Link field on core Email Template).
# Everything here is guarded with exists()/has_column() so the helpdesk degrades to a
# flat, category-less template list when edu_quality isn't installed/migrated.
EMAIL_TEMPLATE_CATEGORY_DOCTYPE = "Email Template Category"
EMAIL_TEMPLATE_CATEGORY_FIELD = "email_template_category"
LIST_DEFAULT_LIMIT = 50


def _require_bulk_email_access():
    capabilities = _require_unity_access()
    if not capabilities.get("can_view_all_tickets"):
        frappe.throw(
            _("You are not allowed to use email templates here"), frappe.PermissionError
        )
    return capabilities


def _has_category_field():
    return frappe.db.has_column(EMAIL_TEMPLATE_DOCTYPE, EMAIL_TEMPLATE_CATEGORY_FIELD)


@frappe.whitelist()
def list_email_template_categories():
    """List Email Template Categories for the composer's category dropdown.

    Returns [] when the edu_quality "Email Template Category" doctype is absent, so
    the picker simply shows no category filter on sites without it."""
    _require_bulk_email_access()
    if not frappe.db.exists("DocType", EMAIL_TEMPLATE_CATEGORY_DOCTYPE):
        return []
    try:
        return frappe.get_all(
            EMAIL_TEMPLATE_CATEGORY_DOCTYPE,
            fields=["name", "category_name"],
            order_by="category_name asc",
            limit=200,
        )
    except Exception:
        return []


@frappe.whitelist()
def list_email_templates(search=None, limit=LIST_DEFAULT_LIMIT, category=None):
    """List Frappe Email Templates (name + subject [+ category]) for the picker,
    optionally filtered to a single Email Template Category."""
    _require_bulk_email_access()
    try:
        limit = max(1, min(int(limit or LIST_DEFAULT_LIMIT), 200))
    except (TypeError, ValueError):
        limit = LIST_DEFAULT_LIMIT

    has_category = _has_category_field()
    fields = ["name", "subject"]
    if has_category:
        fields.append(EMAIL_TEMPLATE_CATEGORY_FIELD)

    filters = {}
    category = cstr(category or "").strip()
    if category and has_category:
        filters[EMAIL_TEMPLATE_CATEGORY_FIELD] = category

    or_filters = None
    search = cstr(search or "").strip()
    if search:
        or_filters = [
            [EMAIL_TEMPLATE_DOCTYPE, "name", "like", f"%{search}%"],
            [EMAIL_TEMPLATE_DOCTYPE, "subject", "like", f"%{search}%"],
        ]
    return frappe.get_all(
        EMAIL_TEMPLATE_DOCTYPE,
        fields=fields,
        filters=filters or None,
        or_filters=or_filters,
        order_by="modified desc",
        limit=limit,
    )


@frappe.whitelist()
def get_email_template_content(name):
    """Return a template's RAW subject + body + use_html. Jinja is NOT rendered
    here — it is rendered per recipient at send time."""
    _require_bulk_email_access()
    name = cstr(name or "").strip()
    if not name:
        frappe.throw(_("Template name is required"))

    tpl = frappe.get_doc(EMAIL_TEMPLATE_DOCTYPE, name)
    use_html = bool(tpl.get("use_html"))
    body = cstr((tpl.get("response_html") if use_html else tpl.get("response")) or "")
    return {
        "name": tpl.name,
        "subject": cstr(tpl.get("subject") or ""),
        "body": body,
        "use_html": use_html,
    }
