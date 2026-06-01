"""Strip the legacy "📢 Bulk Email / 📧 N recipient (click to expand)" label from
existing bulk-email audit tickets, then re-derive their message-search fields so the
list mail-body + detail view read like a normal email.

New bulk emails no longer carry the label (it was removed from
helpdesk.api.unity_helpdesk_ext._bulk_email_audit_html). This patch cleans the tickets
created before that change. For a bulk ticket the indexed mail-body (custom_primary_
message_text) is derived from `description` (there's no inbound Communication), so we
clean `description` and rebuild the search fields via update_ticket_message_search_index.

Idempotent: only tickets whose description still contains the legacy "📢 Bulk Email"
marker are touched; re-running is a no-op. Harmless on fresh installs (no bulk tickets).
"""
import re
import time

import frappe

from helpdesk.api.unity_helpdesk import update_ticket_message_search_index

_LEGACY_MARKER = "📢 Bulk Email"
# The exact markup _bulk_email_audit_html used to emit.
_HEADER_RE = re.compile(r"<p>\s*<strong>\s*📢\s*Bulk Email\s*</strong>\s*</p>")
_SUMMARY_RE = re.compile(r"📧\s*(\d+)\s*recipients?\s*\(click to expand\)")
_CHUNK = 200


def _clean(description):
    cleaned = _HEADER_RE.sub("", description)
    cleaned = _SUMMARY_RE.sub(lambda m: f"Recipients ({m.group(1)})", cleaned)
    return cleaned


def execute():
    start = time.monotonic()
    if not frappe.db.exists("DocType", "HD Ticket"):
        return
    if not frappe.db.has_column("HD Ticket", "custom_is_bulk_email"):
        return

    names = [
        row.name
        for row in frappe.get_all(
            "HD Ticket",
            filters={
                "custom_is_bulk_email": 1,
                "description": ["like", f"%{_LEGACY_MARKER}%"],
            },
            fields=["name"],
            page_length=0,
        )
    ]

    updated = 0
    for i in range(0, len(names), _CHUNK):
        for name in names[i : i + _CHUNK]:
            try:
                desc = frappe.db.get_value("HD Ticket", name, "description") or ""
                if _LEGACY_MARKER not in desc:
                    continue
                cleaned = _clean(desc)
                if cleaned != desc:
                    frappe.db.set_value(
                        "HD Ticket", name, "description", cleaned, update_modified=False
                    )
                    frappe.clear_document_cache("HD Ticket", name)
                # Re-derive custom_primary_message_text / custom_search_message_body
                # from the cleaned description.
                update_ticket_message_search_index(name)
                updated += 1
            except Exception:
                frappe.log_error(
                    title=f"unity_strip_bulk_email_label: {name}",
                    message=frappe.get_traceback(),
                )
        frappe.db.commit()

    frappe.logger().info(
        f"[unity-patch] unity_strip_bulk_email_label cleaned {updated}/{len(names)} "
        f"ticket(s) in {time.monotonic() - start:.2f}s"
    )
