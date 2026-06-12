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
LIST_DEFAULT_LIMIT = 50


def _require_bulk_email_access():
    capabilities = _require_unity_access()
    if not capabilities.get("can_view_all_tickets"):
        frappe.throw(
            _("You are not allowed to use email templates here"), frappe.PermissionError
        )
    return capabilities


@frappe.whitelist()
def list_email_templates(search=None, limit=LIST_DEFAULT_LIMIT):
    """List Frappe Email Templates (name + subject) for the composer picker."""
    _require_bulk_email_access()
    try:
        limit = max(1, min(int(limit or LIST_DEFAULT_LIMIT), 200))
    except (TypeError, ValueError):
        limit = LIST_DEFAULT_LIMIT

    or_filters = None
    search = cstr(search or "").strip()
    if search:
        or_filters = [
            [EMAIL_TEMPLATE_DOCTYPE, "name", "like", f"%{search}%"],
            [EMAIL_TEMPLATE_DOCTYPE, "subject", "like", f"%{search}%"],
        ]
    return frappe.get_all(
        EMAIL_TEMPLATE_DOCTYPE,
        fields=["name", "subject"],
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
