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

    # Personalize template placeholders ({{first_name}} etc.) from the customer's
    # student record. Plain (non-template) text is returned unchanged; an external
    # or guardian-only address yields blank fields and never drops the send.
    _merge_ctx = _merge_context_for_email(raised_by)
    subject = _safe_render(subject, _merge_ctx) or subject
    message = _safe_render(message, _merge_ctx)

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

    # Persist the ticket NOW — before the (possibly slow) email send — so it shows
    # in the list immediately and releases its row locks instead of holding them
    # for the whole send. A later send failure no longer rolls back the ticket.
    frappe.db.commit()

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
        # A non-empty hold reason implies the ticket is On Hold — unless the caller
        # explicitly cleared the flag (is_on_hold == 0) in the same request. Keeps the
        # reason and the "Issues On Hold" indicator consistent (e.g. a reason typed
        # from the list must reflect as On Hold, not silently do nothing).
        if (
            cstr(hold_reason).strip()
            and (is_on_hold is None or int(bool(int(is_on_hold))))
            and _has_field(TICKET_DOCTYPE, "custom_is_on_hold")
        ):
            ticket.custom_is_on_hold = 1
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
    # The Communication.insert above fires the search-index doc hook (async refresh).
    return communication


@frappe.whitelist()
def reply(name, message, cc=None, bcc=None, attachments=None):
    capabilities = _require_unity_access()
    _require_ticket_access(name, capabilities)
    if not message:
        frappe.throw(_("Please enter a reply"))
    ticket = frappe.get_doc(TICKET_DOCTYPE, name)
    # Personalize template placeholders ({{first_name}} etc.) from the ticket's
    # customer (raised_by) student record. Plain text is returned unchanged.
    message = _safe_render(message, _merge_context_for_email(ticket.get("raised_by")))
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
    # The Communication created by reply_via_agent (or the fallback) fires the
    # search-index doc hook, which refreshes the index ASYNCHRONOUSLY — we no longer
    # rebuild it inline here (that whole-thread rebuild was seconds of reply latency).
    # Return the just-created Communication so the SPA can append it to the thread
    # optimistically, without a blocking full ticket reload.
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
    # The comment insert fires the search-index doc hook, which refreshes the index
    # asynchronously — no inline rebuild (that was the "add note" latency).
    # Return the created comment so the SPA can append it optimistically (no reload).
    return {
        "ok": True,
        "comment": {
            "name": comment.name,
            "content": comment.content,
            "commented_by": comment.commented_by,
            "creation": str(comment.creation),
            "is_pinned": comment.is_pinned,
            "user": {
                "name": comment.commented_by,
                "full_name": frappe.utils.get_fullname(comment.commented_by),
            },
        },
    }


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
    (matched on the Student's `user` email) in ONE query, so per-recipient merge
    context can include Student fields (walsh-admin style ``{**student, **row}``).
    Returns {} for sites without the education app; best-effort, never raises."""
    normalized = {e for e in (cstr(x or "").strip().lower() for x in (emails or [])) if e}
    if not normalized or not frappe.db.exists("DocType", "Student"):
        return {}
    out = {}
    try:
        rows = frappe.get_all(
            "Student",
            filters={"user": ["in", list(normalized)]},
            fields=["*"],
        )
    except Exception:
        return {}
    for row in rows:
        for key in ("user",):
            value = cstr(row.get(key) or "").strip().lower()
            if value in normalized and value not in out:
                out[value] = row
    return out


def _students_by_name(names):
    """Map lowercased Student.name -> the full student field dict (one query) for
    resolving a CSV student-ID column to a recipient email + display name + school,
    and for the per-recipient merge context (all student fields, like walsh-admin's
    student.as_dict()). The name PK match is collation-insensitive, so "wacb39"
    finds "WACB39". Returns {} on sites without the education app; never raises."""
    normalized = {n for n in (cstr(x or "").strip() for x in (names or [])) if n}
    if not normalized or not frappe.db.exists("DocType", "Student"):
        return {}
    out = {}
    try:
        rows = frappe.get_all(
            "Student",
            filters={"name": ["in", list(normalized)]},
            fields=["*"],
        )
    except Exception:
        return {}
    for row in rows:
        out[cstr(row.get("name")).strip().lower()] = row
    return out


def _guardian_emails_for_students(student_names):
    """Map Student.name -> [guardian email, …] for a batch of students
    (Student Guardian -> Guardian.email_address). Used to auto-include guardians
    for CSV imports, walsh-admin style. {} on non-education sites; never raises."""
    names = {cstr(n).strip() for n in (student_names or []) if cstr(n).strip()}
    if not names or not frappe.db.exists("DocType", "Student Guardian"):
        return {}
    out = {}
    try:
        links = frappe.get_all(
            "Student Guardian",
            filters={"parenttype": "Student", "parent": ["in", list(names)]},
            fields=["parent", "guardian"],
        )
        guardian_ids = list({l.guardian for l in links if l.guardian})
        gmap = {}
        if guardian_ids:
            for g in frappe.get_all(
                "Guardian",
                filters={"name": ["in", guardian_ids]},
                fields=["name", "email_address"],
            ):
                email = cstr(g.get("email_address") or "").strip().lower()
                if email:
                    gmap[g.name] = email
        for link in links:
            email = gmap.get(link.guardian)
            if not email:
                continue
            bucket = out.setdefault(link.parent, [])
            if email not in bucket:
                bucket.append(email)
    except Exception:
        return {}
    return out


def _school_bcc_emails(school):
    """School-level BCC for a school (walsh-admin parity): every member of the
    school's bcc_email_group + the school's own admin email_address. Lowercased,
    deduped. [] when the School doctype / fields aren't present; never raises."""
    school = cstr(school or "").strip()
    if not school or not frappe.db.exists("DocType", "School"):
        return []
    fields = [f for f in ("bcc_email_group", "email_address") if frappe.db.has_column("School", f)]
    if not fields:
        return []
    emails = set()
    try:
        vals = frappe.db.get_value("School", school, fields, as_dict=True) or {}
        admin = cstr(vals.get("email_address") or "").strip().lower()
        if admin:
            emails.add(admin)
        group = cstr(vals.get("bcc_email_group") or "").strip()
        if group and frappe.db.exists("DocType", "Email Group Member"):
            for member in frappe.get_all(
                "Email Group Member",
                filters={"email_group": group},
                fields=["email"],
            ):
                value = cstr(member.get("email") or "").strip().lower()
                if value:
                    emails.add(value)
    except Exception:
        return []
    return sorted(emails)


def _student_primary_email(student):
    """The student's own deliverable email — the Student's `user` (login) email.

    student_email_id is deliberately NOT used: it's frequently blank or points at a
    stale/wrong address, whereas `user` is the authoritative student email.
    """
    if not student:
        return ""
    return cstr(student.get("user") or "").strip().lower()


def _merge_context_for_email(email):
    """Best-effort merge context (Student field dict) for ONE recipient email — used
    to personalize {{first_name}} etc. on single-ticket / reply sends. Matches
    Student.user. Returns {} for a guardian-only or external
    address, so unknown placeholders simply render blank (the "render blank, still
    send" rule)."""
    email = cstr(email or "").strip().lower()
    if not email:
        return {}
    return _students_by_email([email]).get(email, {}) or {}


def _normalize_bulk_email_groups(groups, recipients=None, bcc=None, merge_data=None):
    """Normalize per-student send groups for bulk_send_email.

    Primary input `groups` is a JSON list of
        {"student": "<id|null>", "emails": [...], "data": {<merge row>}}
    — each becomes ONE ticket + ONE email to its (validated, globally-deduped) emails
    (the student + that student's guardians, or a single free-typed address).

    Legacy flat inputs (`recipients`/`bcc` email lists + an optional
    `merge_data` {email: {col,.., _student}} map) are converted to one group per
    email so older callers keep working under the per-student model.

    Returns (normalized_groups, invalid_count, total_recipients). Dedupe is
    PER-GROUP (within one student), NOT global — siblings who share a guardian
    each get their own personalized ticket that mails that guardian.
    """
    out = []
    invalid_count = 0
    total = 0

    def _clean(values):
        nonlocal invalid_count
        cleaned = []
        local_seen = set()
        for v in (values or []):
            email = cstr((v.get("email") if isinstance(v, dict) else v) or "").strip().lower()
            if not email or email in local_seen:
                continue
            local_seen.add(email)
            if not validate_email_address(email, throw=False):
                invalid_count += 1
                continue
            cleaned.append(email)
        return cleaned

    parsed_groups = _parse_json(groups, []) or []
    if isinstance(parsed_groups, list):
        for g in parsed_groups:
            if not isinstance(g, dict):
                continue
            emails = _clean(g.get("emails"))
            if not emails:
                continue
            data = g.get("data") if isinstance(g.get("data"), dict) else {}
            out.append(
                {
                    "student": cstr(g.get("student") or "").strip() or None,
                    "emails": emails,
                    "data": {cstr(k).strip(): data[k] for k in data if cstr(k).strip() != "_student"},
                }
            )
            total += len(emails)

    # Backward-compat: flat recipient/bcc lists -> one group per email.
    if not out and (recipients or bcc):
        merge_map = {}
        raw_merge = _parse_json(merge_data, {}) or {}
        if isinstance(raw_merge, dict):
            for key, row in raw_merge.items():
                ek = cstr(key or "").strip().lower()
                if ek and isinstance(row, dict):
                    merge_map[ek] = {cstr(c).strip(): row[c] for c in row}
        for src in (recipients, bcc):
            for item in (_parse_json(src, []) or []):
                emails = _clean([item])
                if not emails:
                    continue
                row = merge_map.get(emails[0]) or {}
                out.append(
                    {
                        "student": cstr(row.get("_student") or "").strip() or None,
                        "emails": emails,
                        "data": {k: v for k, v in row.items() if k != "_student"},
                    }
                )
                total += 1

    return out, invalid_count, total


@frappe.whitelist()
def bulk_send_email(
    subject,
    message,
    groups=None,
    cc=None,
    ticket_type=None,
    attachments=None,
    # Legacy flat-recipient params (deprecated) — converted to per-email groups
    # by _normalize_bulk_email_groups so older callers keep working.
    recipients=None,
    bcc=None,
    merge_data=None,
    school_bcc=None,
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

    # One optional, VISIBLE CC list applied to every per-student mail.
    cc_list, invalid_cc_count = _split_email_list_with_counts(cc)

    # Per-student (and per-free-email) groups: one ticket + one email each.
    normalized_groups, invalid_count, total_recipients = _normalize_bulk_email_groups(
        groups, recipients=recipients, bcc=bcc, merge_data=merge_data
    )
    if not normalized_groups:
        frappe.throw(_("Add at least one recipient (student) before sending"))

    # Hard caps bound the total send volume; over the limit we throw (rather than
    # silently send a partial batch) so the user knowingly splits it.
    if total_recipients > RECIPIENT_HARD_CAP:
        frappe.throw(_("Bulk email recipients exceed the {0} address limit").format(RECIPIENT_HARD_CAP))
    if total_recipients + len(cc_list) > TOTAL_ADDRESS_HARD_CAP:
        frappe.throw(
            _("Total addresses (recipients + cc) exceed the {0} limit").format(TOTAL_ADDRESS_HARD_CAP)
        )

    # Resolve attachment File names cheaply in-request (single IN(...) query).
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

    # Run IN-REQUEST (no background worker) — exactly like a single-ticket / chat
    # reply (reply_via_agent). Every per-student ticket + Email Queue entry is
    # created before this returns. We respect the SAME HD Settings
    # "instantly_send_email" flag the replies use: ON -> send now; OFF -> just queue.
    instant = bool(int(frappe.db.get_single_value("HD Settings", "instantly_send_email") or 0))
    result = _bulk_send_email_job(
        subject=subject,
        message=message,
        groups=normalized_groups,
        cc_list=cc_list,
        sender=frappe.session.user,
        file_names=file_names,
        ticket_type=ticket_type,
        now=instant,
    ) or {}

    tickets = result.get("tickets") or []
    return {
        "ok": True,
        "instant": instant,
        "tickets": tickets,
        # `ticket` (singular) kept for older callers — the first per-student ticket.
        "ticket": tickets[0] if tickets else None,
        "ticket_count": len(tickets),
        "student_count": result.get("student_count", 0),
        "recipient_count": total_recipients,
        "count": total_recipients,
        "sent": result.get("sent", 0),
        "queued": result.get("sent", 0),
        "invalid_count": invalid_count,
        "invalid_cc_count": invalid_cc_count,
    }


def _bulk_send_email_job(
    subject,
    message,
    groups,
    cc_list=None,
    sender=None,
    file_names=None,
    ticket_type=None,
    now=False,
):
    """Send a PER-STUDENT bulk email — runs IN-REQUEST (now=True) so it never
    depends on a background worker, exactly like a single-ticket reply.

    For each group (one student + their guardians, or one free-typed address) it:
      * renders the subject + message against that student's record + carried merge
        data (``{**student, **data}``) — so {{first_name}} etc. fill per student,
      * creates ONE HD Ticket whose description is the RENDERED MESSAGE ONLY (no
        recipient / CC / BCC preamble — the recipient set lives in
        custom_bulk_email_recipients), raised_by the student's own email when
        resolvable so the ticket groups under the student,
      * sends ONE email to that group's recipients, VISIBLE (expose_recipients
        ="header"), with the optional CC.
    No school / default BCC (admins/hods/teachers are intentionally NOT mailed).
    One bad group/address is logged, never aborts the rest. Never raises unless
    `now` (a synchronous failure must surface to the SPA).
    """
    try:
        groups = groups or []
        cc_list = cc_list or []
        file_names = file_names or []

        # Attach by File docname via "fid" (File.get_content reads from disk, so
        # private orphan files attach fine).
        sendmail_attachments = [{"fid": name} for name in file_names]

        # Resolve every student record in ONE query for the merge context.
        student_ids = [g["student"] for g in groups if g.get("student")]
        students_by_name = _students_by_name(student_ids) if student_ids else {}

        tickets = []
        sent_count = 0
        student_count = 0
        for group in groups:
            emails = group.get("emails") or []
            if not emails:
                continue
            sid = cstr(group.get("student") or "").strip()
            student = students_by_name.get(sid.lower()) if sid else None
            if student:
                student_count += 1

            context = {**(student or {}), **(group.get("data") or {})}
            rendered_subject = _safe_render(subject, context) or subject
            rendered_message = _safe_render(message, context)

            # raised_by: the student's own email when resolvable (so the
            # student-context panel populates and the ticket groups under them),
            # else the first recipient, else the agent.
            raised_by = _student_primary_email(student) or emails[0] or sender

            payload = {
                "doctype": TICKET_DOCTYPE,
                "subject": rendered_subject,
                "raised_by": raised_by,
                # Message ONLY — no recipient preamble (fixes the "Mail Body" column).
                "description": rendered_message,
                "status": "Open",
                "ticket_type": ticket_type,
            }
            if _has_field(TICKET_DOCTYPE, "custom_via_unity_portal"):
                payload["custom_via_unity_portal"] = 1
            if _has_field(TICKET_DOCTYPE, "custom_is_bulk_email"):
                payload["custom_is_bulk_email"] = 1
            # Denormalised recipient set (student + guardians [+ cc]) for the
            # "Previous Tickets" / "received by" history lookup.
            if _has_field(TICKET_DOCTYPE, "custom_bulk_email_recipients"):
                payload["custom_bulk_email_recipients"] = ", ".join(
                    sorted(set(emails) | set(cc_list))
                )

            try:
                doc = frappe.get_doc(payload).insert(ignore_permissions=True)
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    "Unity bulk per-student ticket insert failed",
                )
                continue
            tickets.append(doc.name)
            # Commit each ticket as it's created so it shows in the list immediately
            # and stops holding row locks during the (possibly slow) send.
            frappe.db.commit()

            try:
                frappe.sendmail(
                    recipients=emails,
                    cc=cc_list or None,
                    subject=rendered_subject,
                    message=rendered_message,
                    attachments=sendmail_attachments,
                    delayed=not now,
                    reference_doctype=TICKET_DOCTYPE,
                    reference_name=doc.name,
                    expose_recipients="header",
                )
                sent_count += len(emails)
            except Exception:
                # A single bad group must never abort the rest of the batch.
                frappe.log_error(
                    frappe.get_traceback(),
                    f"Unity bulk send failed for ticket {doc.name}",
                )

        frappe.db.commit()
        return {"tickets": tickets, "sent": sent_count, "student_count": student_count}
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Unity Helpdesk _bulk_send_email_job",
        )
        # A synchronous (in-request) send must surface a hard failure to the user
        # so the SPA shows an error instead of a false "sent".
        if now:
            raise


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


@frappe.whitelist(allow_guest=False)
def get_bulk_email_sample_csv():
    _require_unity_access()
    buffer = StringIO()
    writer = csv.writer(buffer)
    # walsh-admin shape: each row is a STUDENT — `id` (Student.name) + `school`.
    # The recipient email, student details ({{first_name}}, {{student_name}}, …)
    # and guardians are all looked up from the student. `school` validates the
    # batch (one school; the student must belong to it) and BCCs the school's email
    # group + admin. Any extra columns (e.g. amount) are usable as {{merge}} fields.
    writer.writerow(["id", "school"])
    writer.writerow(["WAAA01", "<school name>"])
    writer.writerow(["WACB39", "<school name>"])
    frappe.response["type"] = "csv"
    frappe.response["doctype"] = "bulk_email_sample"
    frappe.response["result"] = buffer.getvalue()


# Headers (lowercased) accepted as the student-id / school columns.
_STUDENT_ID_HEADERS = {"id", "name", "student", "student_id", "student id", "student_name", "student.name"}
_SCHOOL_HEADERS = {"school", "school_id", "school name", "school_name", "school.name"}


def _student_display_name(student, fallback=""):
    name = " ".join(
        p
        for p in (cstr(student.get("first_name")), cstr(student.get("last_name")))
        if p.strip()
    ).strip()
    return name or cstr(student.get("student_name") or "").strip() or fallback


@frappe.whitelist()
def parse_bulk_email_csv(content):
    """Parse a bulk-email CSV (walsh-admin style): an `id` (Student.name) column +
    a `school` column.

    Each id resolves a Student -> recipient (the Student's `user` email) and all
    student fields become merge context at send time. **Guardians
    are auto-included** — each guardian is added as a recipient that inherits its
    student's merge context (via a carried ``_student`` key). `school` enforces
    one-school-per-CSV and that every student belongs to it, and yields the school
    BCC (bcc_email_group members + School.email_address). Extra columns become
    additional {{merge}} fields. Whitelisted + gated like the send endpoint.
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

    id_col = next((h for h in headers if h.lower() in _STUDENT_ID_HEADERS), None)
    if not id_col:
        id_col = next((h for h in headers if "student" in h.lower()), None)
    if not id_col:
        frappe.throw(_("The CSV must have an 'id' (student) column"))
    school_col = next((h for h in headers if h.lower() in _SCHOOL_HEADERS), None)

    parsed = []
    for raw in reader:
        row = {
            cstr(k or "").strip(): cstr(v or "").strip()
            for k, v in raw.items()
            if cstr(k or "").strip()
        }
        sid = cstr(row.get(id_col) or "").strip()
        if sid:
            parsed.append((sid, row))
        if len(parsed) >= RECIPIENT_HARD_CAP * 2:
            break
    if not parsed:
        frappe.throw(_("No student IDs found in the CSV"))

    # One-school-per-CSV validation (walsh-admin validate_school).
    csv_school = ""
    if school_col:
        schools = {
            cstr(row.get(school_col) or "").strip()
            for _, row in parsed
            if cstr(row.get(school_col) or "").strip()
        }
        if len(schools) > 1:
            frappe.throw(
                _("The CSV mixes multiple schools ({0}). Send one school at a time.").format(
                    ", ".join(sorted(schools))
                )
            )
        csv_school = next(iter(schools), "")

    student_ids = [sid for sid, _ in parsed]
    students = _students_by_name(student_ids)
    guardians = _guardian_emails_for_students(student_ids)

    rows = []
    processed_students = set()
    unmatched = 0
    school_mismatch = 0
    no_email = 0
    duplicates = 0
    guardian_added = 0
    student_added = 0
    for sid, row in parsed:
        student = students.get(sid.lower())
        if not student:
            unmatched += 1
            continue
        # Each student must belong to the declared school (walsh safety check).
        student_school = cstr(student.get("school") or "").strip()
        if csv_school and student_school and student_school != csv_school:
            school_mismatch += 1
            continue
        canonical_id = cstr(student.get("name") or sid).strip()
        # Skip a student repeated across CSV rows (they map to one ticket anyway).
        if canonical_id.lower() in processed_students:
            duplicates += 1
            continue
        processed_students.add(canonical_id.lower())
        # Carried context shared by the student AND their guardians.
        ctx = {**row, "_student": canonical_id}
        display = _student_display_name(student, sid)

        # Dedup WITHIN this student only — so siblings who share a guardian each get
        # that guardian in their own ticket (a global dedup would drop it from the
        # later sibling, the "only one guardian" bug).
        local_seen = set()
        email = cstr(student.get("user") or "").strip().lower()
        if email and validate_email_address(email, throw=False):
            local_seen.add(email)
            rows.append({"email": email, "name": display, "data": ctx})
            student_added += 1
        else:
            no_email += 1

        # Auto-include guardians — each inherits the student's merge context.
        for gemail in guardians.get(canonical_id, []):
            ge = cstr(gemail).strip().lower()
            if ge and validate_email_address(ge, throw=False) and ge not in local_seen:
                local_seen.add(ge)
                rows.append({"email": ge, "name": f"{display} (guardian)", "data": ctx})
                guardian_added += 1

        if len(rows) >= RECIPIENT_HARD_CAP:
            break

    school_bcc = _school_bcc_emails(csv_school) if csv_school else []

    # Merge-field hint: common student fields (auto-resolved) + extra CSV columns
    # (excluding the id/school helper columns).
    common = []
    if frappe.db.exists("DocType", "Student"):
        meta = frappe.get_meta("Student")
        common = [
            f
            for f in ("first_name", "last_name", "middle_name", "student_name", "school")
            if meta.has_field(f)
        ]
    skip = {id_col.lower()} | ({school_col.lower()} if school_col else set())
    extra = [h for h in headers if h.lower() not in skip]
    merge_fields = common + [e for e in extra if e not in common]

    # Group the flat rows into per-student send groups (student + their guardians),
    # so the composer can post `groups` straight to bulk_send_email — one ticket +
    # one email per student. Rows sharing a `_student` collapse into one group.
    groups_by_student = {}
    group_order = []
    for r in rows:
        sid = cstr((r.get("data") or {}).get("_student") or "").strip()
        key = sid.lower() if sid else f"__free__{r['email']}"
        grp = groups_by_student.get(key)
        if grp is None:
            grp = {
                "student": sid or None,
                "emails": [],
                "data": {k: v for k, v in (r.get("data") or {}).items() if k != "_student"},
            }
            groups_by_student[key] = grp
            group_order.append(key)
        if r["email"] not in grp["emails"]:
            grp["emails"].append(r["email"])
    groups = [groups_by_student[k] for k in group_order]

    # `truncated` => the CSV had more recipients than the per-send cap; the UI warns
    # so the agent knows to split the batch rather than silently under-sending.
    truncated = len(rows) >= RECIPIENT_HARD_CAP

    return {
        "headers": headers,
        "id_column": id_col,
        "school_column": school_col,
        "school": csv_school,
        "school_bcc": school_bcc,
        "merge_fields": merge_fields,
        "rows": rows,
        "groups": groups,
        "group_count": len(groups),
        "count": len(rows),
        "student_count": student_added,
        "guardian_count": guardian_added,
        "unmatched_count": unmatched,
        "school_mismatch_count": school_mismatch,
        "no_email_count": no_email,
        "duplicate_count": duplicates,
        "truncated": truncated,
    }
