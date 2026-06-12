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
    _default_bulk_recipients,
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
    # student_context used to be computed synchronously here, but its
    # ~10+ frappe.get_all calls against Education-app DocTypes pushed the
    # combined response over the SPA's 20s timeout, causing the ticket
    # detail page to render as a permanent skeleton. The SPA now fires
    # helpdesk.api.unity_helpdesk.get_student_context in parallel and
    # fills the panel in when it lands; this endpoint returns as soon as
    # the thread+history are ready.

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
    if not ticket_type:
        frappe.throw(_("Please select a ticket type"))
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
    # Return the just-created Communication so the SPA can append it to the
    # thread optimistically, without a blocking full ticket reload.
    return {"ok": True, "communication": _latest_communication_payload(name)}


def _latest_communication_payload(ticket_name):
    """Fetch the most recent Communication for a ticket as a thread-ready dict.

    Shape matches what the Unity thread renderer expects (see
    TicketDetailView.vue communications handling): name, content, sender,
    sent_or_received, creation, communication_date.
    """
    rows = frappe.get_all(
        "Communication",
        filters={
            "reference_doctype": TICKET_DOCTYPE,
            "reference_name": ticket_name,
        },
        fields=[
            "name",
            "subject",
            "content",
            "sender",
            "recipients",
            "cc",
            "bcc",
            "sent_or_received",
            "creation",
            "communication_date",
            "has_attachment",
            "delivery_status",
        ],
        order_by="creation desc",
        limit=1,
    )
    if not rows:
        return None
    row = rows[0]
    return {
        "name": row.get("name"),
        "subject": row.get("subject"),
        "content": row.get("content"),
        "sender": row.get("sender"),
        "recipients": row.get("recipients"),
        "cc": row.get("cc"),
        "bcc": row.get("bcc"),
        "sent_or_received": row.get("sent_or_received"),
        "creation": str(row.get("creation")) if row.get("creation") else None,
        "communication_date": str(row.get("communication_date"))
        if row.get("communication_date")
        else None,
        "has_attachment": row.get("has_attachment"),
        "delivery_status": row.get("delivery_status"),
    }


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


RECIPIENT_HARD_CAP = 1000
TOTAL_ADDRESS_HARD_CAP = 1500


def _safe_render(text, context):
    """Render a Jinja string against `context` for bulk-email mail-merge.

    Renders through Frappe's SANDBOXED jinja environment (SSTI-safe) but overlays
    ``jinja2.ChainableUndefined`` so a missing name OR a missing attribute/key
    (``{{first_name}}`` or ``{{a.b.c}}``) renders BLANK instead of raising — the
    "render blank, still send" rule. Plain text with no Jinja is returned as-is;
    any unexpected failure falls back to the unrendered text so a bad template can
    never drop a recipient.
    """
    text = cstr(text or "")
    if "{{" not in text and "{%" not in text:
        return text
    try:
        from jinja2 import ChainableUndefined

        from frappe.utils.jinja import get_jenv

        jenv = get_jenv().overlay(undefined=ChainableUndefined)
        return jenv.from_string(text).render(context or {})
    except Exception:
        return text


def _students_by_email(emails):
    """Map lowercased email -> Student field dict for a batch of recipient emails
    (matched on student_email_id OR user) in ONE query, so per-recipient merge
    context can include Student fields (walsh-admin style ``{**student, **row}``).
    Returns {} for sites without the education app; best-effort, never raises."""
    normalized = {e for e in (cstr(x or "").strip().lower() for x in (emails or [])) if e}
    if not normalized or not frappe.db.exists("DocType", "Student"):
        return {}
    out = {}
    try:
        rows = frappe.get_all(
            "Student",
            or_filters={
                "student_email_id": ["in", list(normalized)],
                "user": ["in", list(normalized)],
            },
            fields=["*"],
        )
    except Exception:
        return {}
    for row in rows:
        for key in ("student_email_id", "user"):
            value = cstr(row.get(key) or "").strip().lower()
            if value in normalized and value not in out:
                out[value] = row
    return out


def _students_by_name(names):
    """Map lowercased Student.name -> a small student field dict (one query) for
    resolving a CSV student-ID column to a recipient email + display name. The
    name PK match is collation-insensitive, so "wacb39" finds "WACB39". Returns {}
    on sites without the education app; best-effort, never raises."""
    normalized = {n for n in (cstr(x or "").strip() for x in (names or [])) if n}
    if not normalized or not frappe.db.exists("DocType", "Student"):
        return {}
    out = {}
    try:
        rows = frappe.get_all(
            "Student",
            filters={"name": ["in", list(normalized)]},
            fields=[
                "name",
                "student_email_id",
                "user",
                "first_name",
                "last_name",
                "student_name",
            ],
        )
    except Exception:
        return {}
    for row in rows:
        out[cstr(row.get("name")).strip().lower()] = row
    return out


@frappe.whitelist()
def bulk_send_email(
    subject,
    message,
    recipients,
    cc=None,
    bcc=None,
    attachments=None,
    ticket_type=None,
    merge_data=None,
):
    capabilities = _require_unity_access()
    if not capabilities.get("can_view_all_tickets"):
        frappe.throw(
            _("You are not allowed to send bulk emails"),
            frappe.PermissionError,
        )

    subject = cstr(subject or "").strip()
    raw_message = cstr(message or "").strip()
    ticket_type = cstr(ticket_type or "").strip()
    if not subject:
        frappe.throw(_("Subject is required"))
    if not raw_message:
        frappe.throw(_("Message is required"))
    if not ticket_type:
        frappe.throw(_("Ticket Type is required"))
    if not frappe.db.exists("HD Ticket Type", ticket_type):
        frappe.throw(_("Invalid Ticket Type: {0}").format(ticket_type))

    # Strip script tags, on* handlers, javascript: URLs, etc. before storing or sending.
    from frappe.utils import sanitize_html
    message = sanitize_html(raw_message)
    if not cstr(message).strip():
        frappe.throw(_("Message is required"))

    # Fail fast if there's no outgoing email account — sendmail(delayed=True) would
    # otherwise queue silently and surface the misconfiguration only in a worker.
    from frappe.email.doctype.email_account.email_account import EmailAccount
    if not EmailAccount.find_default_outgoing():
        frappe.throw(
            _("No default outgoing Email Account is configured. Please configure one before sending bulk email."),
            frappe.OutgoingEmailError,
        )

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

    cc_list, invalid_cc_count = _split_email_list_with_counts(cc)
    bcc_list, invalid_bcc_count = _split_email_list_with_counts(bcc)

    # The default recipient (the "recipients" field, mirrored from HD Settings) is
    # OPTIONAL — it is not mandatory to configure one. As long as there is at least
    # one BCC student the email can be sent; when no default is configured the mail
    # is sent from the sender's own account instead (see _bulk_send_email_job).
    if not valid_emails and not bcc_list:
        frappe.throw(_("Add at least one recipient (student) before sending"))

    if len(valid_emails) > RECIPIENT_HARD_CAP or len(bcc_list) > RECIPIENT_HARD_CAP:
        frappe.throw(_("Bulk email recipients exceed the {0} address limit").format(RECIPIENT_HARD_CAP))

    if len(valid_emails) + len(cc_list) + len(bcc_list) > TOTAL_ADDRESS_HARD_CAP:
        frappe.throw(
            _("Total addresses (recipients + cc + bcc) exceed the {0} limit").format(TOTAL_ADDRESS_HARD_CAP)
        )

    # Resolve attachment File names cheaply in-request (single IN(...) query),
    # then hand all the heavy work (audit ticket insert + Communication + the
    # actual send) to a background job so this request returns immediately.
    attachment_list = [n for n in (_parse_json(attachments, []) or []) if n]
    if attachment_list:
        existing_files = set(
            frappe.get_all(
                "File",
                filters={"name": ["in", attachment_list]},
                pluck="name",
            )
        )
        file_names = [name for name in attachment_list if name in existing_files]
    else:
        file_names = []

    # Per-recipient mail-merge context: a JSON map {email_lower: {col: val, ...}}
    # from the imported CSV. Optional — absent for a plain (non-personalized) blast.
    merge_map = {}
    raw_merge = _parse_json(merge_data, {}) or {}
    if isinstance(raw_merge, dict):
        for key, row in raw_merge.items():
            email_key = cstr(key or "").strip().lower()
            if email_key and isinstance(row, dict):
                merge_map[email_key] = {cstr(col).strip(): row[col] for col in row}

    frappe.enqueue(
        "helpdesk.api.unity_helpdesk_ext._bulk_send_email_job",
        queue="long",
        subject=subject,
        message=message,
        recipients=valid_emails,
        cc_list=cc_list,
        bcc_list=bcc_list,
        sender=frappe.session.user,
        file_names=file_names,
        ticket_type=ticket_type,
        merge_data=merge_map,
    )

    # "count" is the real audience the email will reach: default recipients (TO)
    # plus every student (BCC). The send happens in a background job, so there is
    # no ticket to return here — the SPA shows a queued-success message instead.
    return {
        "ok": True,
        "queued": True,
        "count": len(valid_emails) + len(bcc_list),
        "invalid_count": invalid_count,
        "invalid_cc_count": invalid_cc_count,
        "invalid_bcc_count": invalid_bcc_count,
    }


def _bulk_send_email_job(
    subject,
    message,
    recipients,
    cc_list=None,
    bcc_list=None,
    sender=None,
    file_names=None,
    ticket_type=None,
    merge_data=None,
):
    """Background worker for bulk_send_email.

    Creates the audit HD Ticket + one Communication (storing the UNRENDERED
    template), then delivers the email. When the subject/body carry Jinja
    placeholders or a CSV merge map was supplied, it renders and sends ONE email
    PER recipient (context = {**student, **csv_row, "email"}) so every copy is
    personalized AND each recipient's To shows only their own address. Otherwise it
    sends a single hidden-recipient blast. CC is always sent ONCE, separately,
    un-personalized. Runs ignore_permissions with an explicit `sender`; never
    raises — failures are logged.
    """
    try:
        recipients = recipients or []
        cc_list = cc_list or []
        bcc_list = bcc_list or []
        file_names = file_names or []
        merge_data = merge_data or {}

        audit_description = _bulk_email_audit_html(
            recipients, cc_list, bcc_list, message
        )
        payload = {
            "doctype": TICKET_DOCTYPE,
            "subject": subject,
            "raised_by": sender,
            "description": audit_description,
            "status": "Open",
            "ticket_type": ticket_type,
        }
        if _has_field(TICKET_DOCTYPE, "custom_via_unity_portal"):
            payload["custom_via_unity_portal"] = 1
        if _has_field(TICKET_DOCTYPE, "custom_is_bulk_email"):
            payload["custom_is_bulk_email"] = 1
        # Denormalised list of every recipient (TO + CC + BCC) for the
        # "Previous Tickets" history lookup.
        if _has_field(TICKET_DOCTYPE, "custom_bulk_email_recipients"):
            all_recipients = sorted(set(recipients) | set(cc_list) | set(bcc_list))
            payload["custom_bulk_email_recipients"] = ", ".join(all_recipients)
        doc = frappe.get_doc(payload).insert(ignore_permissions=True)

        # Attach by File docname via "fid". The old {"file_url": name} passed a
        # docname where frappe.sendmail expects an actual URL, so it matched no File
        # and attachments silently never sent. File.get_content() reads from disk
        # with no permission check, so private orphan files attach fine in the worker.
        sendmail_attachments = [{"fid": name} for name in file_names]

        # Resolve the recipient list. Every address — the students plus the
        # configured default/audit recipients (from HD Settings) — goes into one
        # de-duplicated list. The sender (the logged-in agent) is NEVER added as a
        # recipient — they only appear as the From address.
        targets = []
        target_seen = set()
        for addr in list(bcc_list) + list(recipients) + _default_bulk_recipients():
            key = (addr or "").lower()
            if not key or key in target_seen:
                continue
            target_seen.add(key)
            targets.append(addr)

        if not targets:
            frappe.log_error(
                "Bulk email job had no valid recipients", "Unity Helpdesk _bulk_send_email_job"
            )
            return

        # Log the bulk email as a Communication on the audit ticket, showing the
        # real audience truncated for readability.
        recipients_display = ", ".join(targets[:5])
        if len(targets) > 5:
            recipients_display += f" (+{len(targets) - 5} more)"
        try:
            from frappe.core.doctype.communication.email import make as make_comm

            make_comm(
                doctype=TICKET_DOCTYPE,
                name=doc.name,
                subject=subject,
                content=message,
                sent_or_received="Sent",
                sender=sender,
                recipients=recipients_display,
                cc=", ".join(cc_list) if cc_list else "",
                communication_medium="Email",
                send_email=False,
            )
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "Unity Helpdesk bulk_send_email: Communication creation",
            )

        # Personalize only when there's something to merge; otherwise one efficient
        # blast. BOTH paths hide co-recipients (expose_recipients=None + single-address
        # delivery) and carry the attachments.
        needs_merge = bool(merge_data) or ("{{" in subject) or ("{{" in message)
        if needs_merge:
            students = _students_by_email(targets)
            for addr in targets:
                key = (addr or "").strip().lower()
                context = {
                    **(students.get(key) or {}),
                    **(merge_data.get(key) or {}),
                    "email": addr,
                }
                frappe.sendmail(
                    recipients=[addr],
                    subject=_safe_render(subject, context),
                    message=_safe_render(message, context),
                    attachments=sendmail_attachments,
                    delayed=True,
                    reference_doctype=TICKET_DOCTYPE,
                    reference_name=doc.name,
                    expose_recipients=None,
                )
        else:
            frappe.sendmail(
                recipients=targets,
                subject=subject,
                message=message,
                attachments=sendmail_attachments,
                delayed=True,
                reference_doctype=TICKET_DOCTYPE,
                reference_name=doc.name,
                expose_recipients=None,
            )

        # CC: ONE un-personalized copy (placeholders blanked), never to an address
        # that already received it as a recipient. Kept out of the per-recipient loop
        # so CC people get a single copy, not one per student.
        cc_targets = [c for c in cc_list if (c or "").strip().lower() not in target_seen]
        if cc_targets:
            frappe.sendmail(
                recipients=cc_targets,
                subject=_safe_render(subject, {}),
                message=_safe_render(message, {}),
                attachments=sendmail_attachments,
                delayed=True,
                reference_doctype=TICKET_DOCTYPE,
                reference_name=doc.name,
                expose_recipients="header",
            )

        frappe.db.commit()
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Unity Helpdesk _bulk_send_email_job",
        )


def _split_email_list(value):
    out, _invalid = _split_email_list_with_counts(value)
    return out


def _split_email_list_with_counts(value):
    """Split a comma/semicolon-separated list (or list/tuple) of emails into
    (valid_unique_lowercase, invalid_count). Invalid entries are dropped.
    Accepts JSON-encoded arrays (the SPA sends cc/bcc as JSON.stringify([...]))
    so a `'["a@x.com"]'` string isn't mis-parsed as a single CSV cell."""
    if not value:
        return [], 0
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("["):
            try:
                value = frappe.parse_json(stripped)
            except Exception:
                pass
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = cstr(value).replace(";", ",").split(",")
    out = []
    seen = set()
    invalid = 0
    for item in items:
        email = cstr(item or "").strip().lower()
        if not email:
            continue
        if email in seen:
            continue
        if validate_email_address(email, throw=False):
            seen.add(email)
            out.append(email)
        else:
            invalid += 1
    return out, invalid


def _bulk_email_audit_html(recipients, cc_list, bcc_list, message):
    # Collapsible recipient list
    recipient_items = "".join(
        f"<li style='font-size:12px;color:#475569'>{frappe.utils.escape_html(e)}</li>"
        for e in recipients
    )
    recipient_block = (
        f"<details style='margin:6px 0'>"
        f"<summary style='cursor:pointer;font-weight:600;color:#3730a3'>"
        f"Recipients ({len(recipients)})"
        f"</summary>"
        f"<ul style='margin:6px 0 0 16px;padding:0'>{recipient_items}</ul>"
        f"</details>"
    )
    # No "📢 Bulk Email" speaker header — bulk emails should read like normal mail.
    sections = [
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
    # First column is the STUDENT ID (Student.name) — the recipient email and all
    # student details ({{first_name}}, {{last_name}}, …) are looked up from it. Any
    # extra columns (e.g. amount) are also usable as {{merge}} fields, overriding
    # the looked-up student fields.
    writer.writerow(["name", "amount"])
    writer.writerow(["BFOA01", "1500"])
    writer.writerow(["WACB39", "2000"])
    frappe.response["type"] = "csv"
    frappe.response["doctype"] = "bulk_email_sample"
    frappe.response["result"] = buffer.getvalue()


# Headers (lowercased) accepted as the student-id column.
_STUDENT_ID_HEADERS = {"name", "student", "student_id", "student id", "student_name", "student.name", "id"}


@frappe.whitelist()
def parse_bulk_email_csv(content):
    """Parse an uploaded bulk-email CSV into mail-merge rows, walsh-admin style.

    The CSV carries a STUDENT-ID column (Student.name) — NOT an email. Each id is
    resolved to a Student; the recipient is the student's own email
    (student_email_id, falling back to the login `user`) and all student fields
    become merge context at send time. Any extra columns are kept as additional
    {{merge}} fields. Robust to quoted commas (csv.DictReader). Whitelisted + gated
    like the send endpoint.
    """
    capabilities = _require_unity_access()
    if not capabilities.get("can_view_all_tickets"):
        frappe.throw(_("You are not allowed to send bulk emails"), frappe.PermissionError)

    text = cstr(content or "")
    if not text.strip():
        frappe.throw(_("The CSV file is empty"))

    reader = csv.DictReader(StringIO(text))
    headers = [cstr(h or "").strip() for h in (reader.fieldnames or []) if cstr(h or "").strip()]
    if not headers:
        frappe.throw(_("The CSV has no header row"))

    name_col = next((h for h in headers if h.lower() in _STUDENT_ID_HEADERS), None)
    if not name_col:
        name_col = next((h for h in headers if "student" in h.lower()), None)
    if not name_col:
        frappe.throw(_("The CSV must have a 'name' (student ID) column"))

    # Read rows first, then batch-resolve the students in one query.
    parsed = []
    for raw in reader:
        row = {
            cstr(k or "").strip(): cstr(v or "").strip()
            for k, v in raw.items()
            if cstr(k or "").strip()
        }
        sid = cstr(row.get(name_col) or "").strip()
        if sid:
            parsed.append((sid, row))
        if len(parsed) >= RECIPIENT_HARD_CAP * 2:
            break

    students = _students_by_name([sid for sid, _ in parsed])

    rows = []
    seen = set()
    unmatched = 0
    no_email = 0
    duplicates = 0
    for sid, row in parsed:
        student = students.get(sid.lower())
        if not student:
            unmatched += 1
            continue
        email = cstr(student.get("student_email_id") or student.get("user") or "").strip().lower()
        if not email or not validate_email_address(email, throw=False):
            no_email += 1
            continue
        if email in seen:
            duplicates += 1
            continue
        seen.add(email)
        display = (
            " ".join(
                p
                for p in (cstr(student.get("first_name")), cstr(student.get("last_name")))
                if p.strip()
            ).strip()
            or cstr(student.get("student_name") or "").strip()
            or sid
        )
        rows.append({"email": email, "name": display, "data": row})
        if len(rows) >= RECIPIENT_HARD_CAP:
            break

    # Merge-field hint: common student fields (auto-resolved) + extra CSV columns.
    common = []
    if frappe.db.exists("DocType", "Student"):
        meta = frappe.get_meta("Student")
        common = [f for f in ("first_name", "last_name", "middle_name", "student_name") if meta.has_field(f)]
    extra = [h for h in headers if h.lower() != name_col.lower()]
    merge_fields = common + [e for e in extra if e not in common]

    return {
        "headers": headers,
        "name_column": name_col,
        "merge_fields": merge_fields,
        "rows": rows,
        "count": len(rows),
        "unmatched_count": unmatched,
        "no_email_count": no_email,
        "duplicate_count": duplicates,
    }
