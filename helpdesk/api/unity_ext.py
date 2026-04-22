"""
unity_ext.py — Extension of the Unity Helpdesk API.

This module exists as a separate file so that Frappe loads it fresh on every
bench start, avoiding the in-memory cache issue that prevents newly-added
functions in unity.py from being discoverable.

Functions here import helpers from the already-cached unity.py module (those
helpers were present from the first load and are always available).
"""

import frappe
from frappe import _
from frappe.desk.form.assign_to import clear as clear_all_assignments

# These helpers are in the cached unity.py — they were there from the start.
from helpdesk.api.unity import (
    _decorate_ticket,
    _has_field,
    _parse_json,
    _ticket_fields,
    FINAL_STATUSES,
    STATUS_OPTIONS,
    TICKET_DOCTYPE,
)
from helpdesk.api.ticket import assign_ticket_to_agent


def _log_hold_reason(ticket_name, hold_reason):
    if not hold_reason:
        return
    frappe.get_doc(
        {
            "doctype": "HD Ticket Comment",
            "commented_by": frappe.session.user,
            "content": f"Hold Reason: {hold_reason}",
            "is_pinned": 0,
            "reference_ticket": ticket_name,
        }
    ).insert(ignore_permissions=True)


# ---------------------------------------------------------------------------
# Ticket detail
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_ticket_detail(name):
    rows = frappe.get_list(
        TICKET_DOCTYPE,
        fields=_ticket_fields(),
        filters={"name": name},
        page_length=1,
    )
    if not rows:
        frappe.throw(_("Ticket not found"), frappe.DoesNotExistError)

    decorated = _decorate_ticket(rows[0])

    # Activity / history
    history = frappe.get_all(
        "HD Ticket Activity",
        fields=["name", "action", "owner", "creation"],
        filters={"ticket": name},
        order_by="creation asc",
        page_length=200,
    )
    decorated.history = history

    # Email thread — all Communications linked to this ticket
    communications = frappe.get_all(
        "Communication",
        fields=["name", "subject", "content", "sender", "sent_or_received", "creation", "cc", "bcc"],
        filters={"reference_doctype": TICKET_DOCTYPE, "reference_name": name},
        order_by="creation asc",
        page_length=500,
    )
    decorated.communications = communications

    return decorated


# ---------------------------------------------------------------------------
# Create ticket
# ---------------------------------------------------------------------------

@frappe.whitelist()
def create_ticket(subject, raised_by, message, priority=None, ticket_type=None, assignee=None):
    if not subject:
        frappe.throw(_("Please enter a subject"))
    if not raised_by:
        frappe.throw(_("Please enter customer email"))
    if not message:
        frappe.throw(_("Please enter email message"))

    doc = frappe.get_doc(
        {
            "doctype": TICKET_DOCTYPE,
            "subject": subject,
            "raised_by": raised_by,
            "description": message,
            "priority": priority or None,
            "ticket_type": ticket_type or None,
        }
    ).insert()

    if assignee:
        try:
            assign_ticket_to_agent(doc.name, assignee)
            doc.reload()
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Unity Helpdesk create_ticket assign_agent")

    if priority is not None:
        frappe.db.set_value(
            TICKET_DOCTYPE,
            doc.name,
            "priority",
            priority or None,
            update_modified=False,
        )
    if ticket_type is not None:
        frappe.db.set_value(
            TICKET_DOCTYPE,
            doc.name,
            "ticket_type",
            ticket_type or None,
            update_modified=False,
        )

    try:
        doc.reply_via_agent(message=message)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Unity Helpdesk create_ticket reply_via_agent")

    try:
        rows = frappe.get_list(
            TICKET_DOCTYPE,
            fields=_ticket_fields(),
            filters={"name": doc.name},
            page_length=1,
        )
        return _decorate_ticket(rows[0]) if rows else {"name": doc.name}
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Unity Helpdesk create_ticket response")
        return {"name": doc.name, "subject": doc.subject, "raised_by": doc.raised_by}


# ---------------------------------------------------------------------------
# Update ticket
# ---------------------------------------------------------------------------

@frappe.whitelist()
def update_ticket(
    name,
    assignee=None,
    status=None,
    priority=None,
    ticket_type=None,
    is_on_hold=None,
    hold_from=None,
    hold_to=None,
    hold_reason=None,
):
    ticket = frappe.get_doc(TICKET_DOCTYPE, name)
    explicit_priority = priority if priority is not None else None
    explicit_ticket_type = ticket_type if ticket_type is not None else None
    on_hold_selected = status == "On Hold"
    previous_hold_reason = ticket.get("custom_hold_reason") if _has_field(TICKET_DOCTYPE, "custom_hold_reason") else None

    if assignee is not None:
        if assignee:
            try:
                assign_ticket_to_agent(name, assignee)
            except Exception:
                frappe.log_error(frappe.get_traceback(), "Unity Helpdesk update_ticket assign_agent")
        else:
            clear_all_assignments(TICKET_DOCTYPE, name)

    if status:
        if on_hold_selected:
            if _has_field(TICKET_DOCTYPE, "custom_is_on_hold"):
                ticket.custom_is_on_hold = 1
            if ticket.status in FINAL_STATUSES or not ticket.status:
                ticket.status = "Open"
        elif status not in STATUS_OPTIONS:
            frappe.throw(_("Invalid ticket status"))
        else:
            ticket.status = status
            if _has_field(TICKET_DOCTYPE, "custom_is_on_hold"):
                ticket.custom_is_on_hold = 0

    if priority:
        ticket.priority = priority

    if ticket_type is not None:
        ticket.ticket_type = ticket_type or None

    if is_on_hold is not None and _has_field(TICKET_DOCTYPE, "custom_is_on_hold"):
        ticket.custom_is_on_hold = int(bool(int(is_on_hold)))
    if hold_from is not None and _has_field(TICKET_DOCTYPE, "custom_hold_from"):
        ticket.custom_hold_from = hold_from
    if hold_to is not None and _has_field(TICKET_DOCTYPE, "custom_hold_to"):
        ticket.custom_hold_to = hold_to
    if hold_reason is not None and _has_field(TICKET_DOCTYPE, "custom_hold_reason"):
        ticket.custom_hold_reason = hold_reason
    if ticket.status in FINAL_STATUSES and _has_field(TICKET_DOCTYPE, "custom_is_on_hold"):
        ticket.custom_is_on_hold = 0

    ticket.save()

    if explicit_priority is not None:
        frappe.db.set_value(
            TICKET_DOCTYPE,
            name,
            "priority",
            explicit_priority or None,
            update_modified=False,
        )
    if explicit_ticket_type is not None:
        frappe.db.set_value(
            TICKET_DOCTYPE,
            name,
            "ticket_type",
            explicit_ticket_type or None,
            update_modified=False,
        )
    if hold_reason is not None and hold_reason != (previous_hold_reason or ""):
        _log_hold_reason(name, hold_reason)

    rows = frappe.get_list(
        TICKET_DOCTYPE,
        fields=_ticket_fields(),
        filters={"name": name},
        page_length=1,
    )
    return _decorate_ticket(rows[0]) if rows else {}


# ---------------------------------------------------------------------------
# Reply
# ---------------------------------------------------------------------------

def _create_communication_direct(ticket, message, cc=None, bcc=None):
    """Create a Communication record without sending email (fallback when no email account set up)."""
    frappe.get_doc({
        "doctype": "Communication",
        "communication_type": "Communication",
        "communication_medium": "",
        "sent_or_received": "Sent",
        "email_status": "Open",
        "subject": f"Re: {ticket.subject} (#{ticket.name})",
        "sender": frappe.session.user,
        "recipients": ticket.raised_by,
        "content": message,
        "status": "Linked",
        "reference_doctype": TICKET_DOCTYPE,
        "reference_name": ticket.name,
        "cc": cc or "",
        "bcc": bcc or "",
    }).insert(ignore_permissions=True)


@frappe.whitelist()
def reply(name, message, cc=None, bcc=None, attachments=None):
    if not message:
        frappe.throw(_("Please enter a reply"))
    ticket = frappe.get_doc(TICKET_DOCTYPE, name)
    try:
        ticket.reply_via_agent(
            message=message,
            cc=cc,
            bcc=bcc,
            attachments=_parse_json(attachments, []),
        )
    except frappe.ValidationError as exc:
        err = str(exc)
        if "sender email" in err.lower() or "no sender" in err.lower() or "sendmail" in err.lower():
            # No outgoing email account configured — save communication record only
            _create_communication_direct(ticket, message, cc, bcc)
        else:
            raise
    except Exception as exc:
        err = str(exc)
        if "sender email" in err.lower() or "no sender" in err.lower():
            _create_communication_direct(ticket, message, cc, bcc)
        else:
            raise
    return {"ok": True}


# ---------------------------------------------------------------------------
# Internal comment (note — not sent to customer)
# ---------------------------------------------------------------------------

@frappe.whitelist()
def add_comment(name, content):
    if not content:
        frappe.throw(_("Please enter a comment"))
    frappe.get_doc({
        "doctype": "HD Ticket Comment",
        "commented_by": frappe.session.user,
        "content": content,
        "is_pinned": False,
        "reference_ticket": name,
    }).insert(ignore_permissions=True)
    return {"ok": True}
