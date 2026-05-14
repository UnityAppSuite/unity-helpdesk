"""
unity_helpdesk_ext.py — Extension of the Unity Helpdesk API.

This module exists as a separate file so that Frappe loads it fresh on every
bench start, avoiding the in-memory cache issue that prevents newly-added
functions in unity.py from being discoverable.

Functions here import helpers from the already-cached unity_helpdesk.py module (those
helpers were present from the first load and are always available).
"""

import time

import frappe
from frappe import _
from frappe.desk.form.assign_to import clear as clear_all_assignments
from frappe.utils import cstr

# These helpers are in the cached unity_helpdesk.py — they were there from the start.
from helpdesk.api.unity_helpdesk import (
    _decorate_ticket,
    _has_field,
    _log_hold_reason,
    _parse_json,
    _require_unity_access,
    _require_ticket_access,
    _ticket_fields,
    get_student_context_for_ticket,
    update_ticket_message_search_index,
    FINAL_STATUSES,
    STATUS_OPTIONS,
    TICKET_DOCTYPE,
)
from helpdesk.api.ticket import assign_ticket_to_agent
from helpdesk.helpdesk.doctype.hd_ticket.api import get_ticket_thread_components


def _user_map(usernames):
    usernames = [user for user in usernames if user]
    if not usernames:
        return {}
    return {
        row.name: row
        for row in frappe.get_all(
            "User",
            fields=["name", "full_name", "email", "user_image"],
            filters={"name": ["in", list(set(usernames))]},
            page_length=max(len(set(usernames)), 1),
        )
    }


def _assignment_history(ticket_name):
    rows = frappe.get_all(
        "ToDo",
        fields=[
            "name",
            "allocated_to",
            "assigned_by",
            "assigned_by_full_name",
            "status",
            "creation",
            "modified",
        ],
        filters={"reference_type": TICKET_DOCTYPE, "reference_name": ticket_name},
        order_by="creation desc",
        page_length=500,
    )
    users = _user_map(
        [row.allocated_to for row in rows] + [row.assigned_by for row in rows]
    )

    history = []
    for row in rows:
        assigned_to = users.get(row.allocated_to) or {}
        assigned_by = users.get(row.assigned_by) or {}
        history.append(
            {
                "name": row.name,
                "allocated_to": row.allocated_to,
                "allocated_to_full_name": assigned_to.get("full_name")
                or row.allocated_to,
                "assigned_by": row.assigned_by,
                "assigned_by_full_name": row.assigned_by_full_name
                or assigned_by.get("full_name")
                or row.assigned_by,
                "status": row.status,
                "creation": row.creation,
                "modified": row.modified,
                "assigned_at": row.creation,
            }
        )
    return history


def _attach_files_to_communication(file_names, communication_name):
    for file_name in _parse_json(file_names, []) or []:
        file_name = cstr(file_name).strip()
        if not file_name or not frappe.db.exists("File", file_name):
            continue
        file_doc = frappe.get_doc("File", file_name)
        file_doc.attached_to_doctype = "Communication"
        file_doc.attached_to_name = communication_name
        file_doc.save(ignore_permissions=True)


def _is_missing_sender_error(exc):
    message = cstr(exc).lower()
    return (
        "sender email" in message
        or "no sender" in message
        or "sendmail" in message
        or "outgoing email" in message
    )



# ---------------------------------------------------------------------------
# Ticket detail
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_ticket_detail(name):
    capabilities = _require_unity_access()
    _require_ticket_access(name, capabilities)
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
        page_length=100,
    )
    decorated.history = history
    decorated.assignment_history = _assignment_history(name)

    thread_components = get_ticket_thread_components(name)
    decorated.communications = thread_components.communications
    decorated.comments = thread_components.comments
    decorated.thread = thread_components.thread
    decorated.student_context = get_student_context_for_ticket(
        ticket_name=name,
        raised_by=decorated.get("raised_by"),
    )

    return decorated


# ---------------------------------------------------------------------------
# Create ticket
# ---------------------------------------------------------------------------

@frappe.whitelist()
def create_ticket(
    subject,
    raised_by,
    message,
    priority=None,
    ticket_type=None,
    assignee=None,
    attachments=None,
):
    _require_unity_access()
    if not subject:
        frappe.throw(_("Please enter a subject"))
    if not raised_by:
        frappe.throw(_("Please enter customer email"))
    if not message:
        frappe.throw(_("Please enter email message"))

    payload = {
        "doctype": TICKET_DOCTYPE,
        "subject": subject,
        "raised_by": raised_by,
        "description": message,
        "priority": priority or None,
        "ticket_type": ticket_type or None,
    }
    # Mark tickets originating from the Unity Helpdesk SPA so the list can tint
    # them green. Field is created by patches.unity_helpdesk_portal_origin_fields.
    if frappe.db.has_column(TICKET_DOCTYPE, "custom_via_unity_portal"):
        payload["custom_via_unity_portal"] = 1
    doc = frappe.get_doc(payload).insert()

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

    email_sent = False
    warning = ""
    try:
        doc.reply_via_agent(
            message=message,
            attachments=_parse_json(attachments, []),
        )
        email_sent = True
    except Exception as exc:
        if _is_missing_sender_error(exc):
            _create_communication_direct(doc, message, attachments=attachments)
            warning = _("Ticket created and reply saved, but no outgoing email account is configured.")
        else:
            warning = _("Ticket created, but the email could not be sent: {0}").format(exc)
            frappe.log_error(frappe.get_traceback(), "Unity Helpdesk create_ticket reply_via_agent")

    try:
        rows = frappe.get_list(
            TICKET_DOCTYPE,
            fields=_ticket_fields(),
            filters={"name": doc.name},
            page_length=1,
        )
        ticket = _decorate_ticket(rows[0]) if rows else {"name": doc.name}
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Unity Helpdesk create_ticket response")
        ticket = {"name": doc.name, "subject": doc.subject, "raised_by": doc.raised_by}

    return {
        "ticket": ticket,
        "email_sent": email_sent,
        "warning": warning,
    }


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
    capabilities = _require_unity_access()
    _require_ticket_access(name, capabilities)
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

def _create_communication_direct(ticket, message, cc=None, bcc=None, attachments=None):
    """Create a Communication record without sending email (fallback when no email account set up).

    Sets a synthetic message_id so that customer email replies can be threaded back to this
    ticket by Frappe's email processor via the In-Reply-To header.
    """
    synthetic_msg_id = f"<{ticket.name}.{int(time.time())}@helpdesk>"
    communication = frappe.get_doc({
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
        "message_id": synthetic_msg_id,
    }).insert(ignore_permissions=True)
    _attach_files_to_communication(attachments, communication.name)
    update_ticket_message_search_index(ticket.name, ticket_doc=ticket)
    return communication


@frappe.whitelist()
def reply(name, message, cc=None, bcc=None, attachments=None):
    capabilities = _require_unity_access()
    _require_ticket_access(name, capabilities)
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
        if _is_missing_sender_error(exc):
            # No outgoing email account configured — save communication record only
            _create_communication_direct(ticket, message, cc, bcc, attachments)
        else:
            raise
    except Exception as exc:
        if _is_missing_sender_error(exc):
            _create_communication_direct(ticket, message, cc, bcc, attachments)
        else:
            raise
    else:
        update_ticket_message_search_index(name, ticket_doc=ticket)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Internal comment (note — not sent to customer)
# ---------------------------------------------------------------------------

@frappe.whitelist()
def add_comment(name, content):
    capabilities = _require_unity_access()
    _require_ticket_access(name, capabilities)
    if not content:
        frappe.throw(_("Please enter a comment"))
    comment = frappe.get_doc({
        "doctype": "HD Ticket Comment",
        "commented_by": frappe.session.user,
        "content": content,
        "is_pinned": False,
        "reference_ticket": name,
    }).insert(ignore_permissions=True)
    update_ticket_message_search_index(name)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Bulk email — BCC-style mass send with a single audit-trail ticket
# ---------------------------------------------------------------------------

import csv
from io import StringIO

from frappe.utils import validate_email_address


@frappe.whitelist()
def bulk_send_email(
    subject,
    message,
    recipients,
    cc=None,
    bcc=None,
    attachments=None,
):
    capabilities = _require_unity_access()
    if not capabilities.get("can_view_all_tickets"):
        frappe.throw(
            _("You are not allowed to send bulk emails"),
            frappe.PermissionError,
        )

    subject = cstr(subject or "").strip()
    message = cstr(message or "").strip()
    if not subject:
        frappe.throw(_("Subject is required"))
    if not message:
        frappe.throw(_("Message is required"))

    parsed = _parse_json(recipients, [])
    raw_emails = []
    for item in parsed or []:
        if isinstance(item, str):
            raw_emails.append(item)
        elif isinstance(item, dict):
            raw_emails.append(item.get("email"))
    valid_emails = []
    invalid_count = 0
    seen = set()
    for value in raw_emails:
        email = cstr(value or "").strip().lower()
        if not email or email in seen:
            continue
        if not validate_email_address(email, throw=False):
            invalid_count += 1
            continue
        seen.add(email)
        valid_emails.append(email)

    if not valid_emails:
        frappe.throw(_("At least one valid email address is required"))

    cc_list = _split_email_list(cc)
    bcc_list = _split_email_list(bcc)
    # Hard cap to avoid accidental mega-sends.
    if len(valid_emails) + len(cc_list) + len(bcc_list) > 1000:
        frappe.throw(_("Bulk email recipients exceed the 1000 address limit"))

    audit_description = _bulk_email_audit_html(valid_emails, cc_list, bcc_list, message)
    payload = {
        "doctype": TICKET_DOCTYPE,
        "subject": subject,
        "raised_by": frappe.session.user,
        "description": audit_description,
        "status": "Open",
    }
    if _has_field(TICKET_DOCTYPE, "custom_via_unity_portal"):
        payload["custom_via_unity_portal"] = 1
    if _has_field(TICKET_DOCTYPE, "custom_is_bulk_email"):
        payload["custom_is_bulk_email"] = 1
    doc = frappe.get_doc(payload).insert(ignore_permissions=True)

    attachment_list = _parse_json(attachments, []) or []
    sendmail_attachments = [
        {"file_url": name}
        for name in attachment_list
        if name and frappe.db.exists("File", name)
    ]

    # Create a Communication immediately so the sent message appears in the ticket thread.
    # Use frappe's make() which handles linking/indexing correctly.
    recipients_display = ", ".join(valid_emails[:5])
    if len(valid_emails) > 5:
        recipients_display += f" (+{len(valid_emails) - 5} more)"
    try:
        from frappe.core.doctype.communication.email import make as make_comm
        make_comm(
            doctype=TICKET_DOCTYPE,
            name=doc.name,
            subject=subject,
            content=message,
            sent_or_received="Sent",
            sender=frappe.session.user,
            recipients=recipients_display,
            cc=", ".join(cc_list) if cc_list else "",
            communication_medium="Email",
            send_email=False,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Unity Helpdesk bulk_send_email: Communication creation")

    warning = ""
    queued = 0
    try:
        # BCC all real recipients so they can't see one another.
        frappe.sendmail(
            recipients=[frappe.session.user],
            cc=cc_list or None,
            bcc=valid_emails + bcc_list,
            subject=subject,
            message=message,
            attachments=sendmail_attachments,
            delayed=True,
        )
        queued = len(valid_emails)
    except Exception as exc:
        if _is_missing_sender_error(exc):
            warning = _("Audit-trail ticket created, but no outgoing email account is configured. No email was sent.")
        else:
            warning = _("Audit-trail ticket created, but the email could not be queued: {0}").format(exc)
            frappe.log_error(frappe.get_traceback(), "Unity Helpdesk bulk_send_email")

    return {
        "ok": True,
        "ticket": doc.name,
        "queued": queued,
        "invalid_count": invalid_count,
        "warning": warning,
    }


def _split_email_list(value):
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = cstr(value).replace(";", ",").split(",")
    out = []
    seen = set()
    for item in items:
        email = cstr(item or "").strip().lower()
        if not email or email in seen:
            continue
        if validate_email_address(email, throw=False):
            seen.add(email)
            out.append(email)
    return out


def _bulk_email_audit_html(recipients, cc_list, bcc_list, message):
    # Collapsible recipient list
    recipient_items = "".join(
        f"<li style='font-size:12px;color:#475569'>{frappe.utils.escape_html(e)}</li>"
        for e in recipients
    )
    recipient_block = (
        f"<details style='margin:6px 0'>"
        f"<summary style='cursor:pointer;font-weight:600;color:#3730a3'>"
        f"📧 {len(recipients)} recipient{'s' if len(recipients) != 1 else ''} (click to expand)"
        f"</summary>"
        f"<ul style='margin:6px 0 0 16px;padding:0'>{recipient_items}</ul>"
        f"</details>"
    )
    sections = [
        "<p><strong>📢 Bulk Email</strong></p>",
        recipient_block,
    ]
    if cc_list:
        sections.append(f"<p><strong>CC:</strong> {', '.join(cc_list)}</p>")
    if bcc_list:
        sections.append(f"<p><strong>Additional BCC:</strong> {', '.join(bcc_list)}</p>")
    sections.append("<hr style='margin:12px 0'>")
    sections.append("<p><strong>Message sent:</strong></p>")
    sections.append(message)
    return "".join(sections)


@frappe.whitelist(allow_guest=False)
def get_bulk_email_sample_csv():
    _require_unity_access()
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["email", "name"])
    writer.writerow(["parent1@example.com", "Parent One"])
    writer.writerow(["parent2@example.com", "Parent Two"])
    writer.writerow(["guardian3@example.com", "Guardian Three"])
    frappe.response["type"] = "csv"
    frappe.response["doctype"] = "bulk_email_sample"
    frappe.response["result"] = buffer.getvalue()
