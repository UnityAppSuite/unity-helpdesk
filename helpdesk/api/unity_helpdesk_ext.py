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
    _status_field_updates,
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
        if not on_hold_selected and status not in STATUS_OPTIONS:
            frappe.throw(_("Invalid ticket status"))
        # Shared with bulk_update_tickets so inline + bulk agree on how a status
        # (incl. the virtual "On Hold") maps to fields. Full save below still runs
        # SLA/Activity/Version for the single-ticket path.
        for _field, _val in _status_field_updates(status, ticket.status).items():
            ticket.set(_field, _val)

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


# Volume caps. Historically a single flat RECIPIENT_HARD_CAP counted students AND
# guardians together, so a 500-student CSV (each with ~1-2 guardians) hit the wall at
# ~360 students and silently dropped the rest at parse time. Bulk sends are now bounded
# on DISTINCT STUDENTS (one ticket + one mail per student), with a separate ceiling on
# the total address volume (students + guardians + cc). Walsh Admin (edu_quality) has no
# recipient cap at all; these values comfortably cover a real single-school broadcast and
# stay inside one serial job's timeout — PR#2's parallel chunking removes the ceiling.
STUDENT_HARD_CAP = 1000
TOTAL_ADDRESS_HARD_CAP = 3000
# Deprecated flat cap — kept as an alias so any external caller still importing it works.
RECIPIENT_HARD_CAP = STUDENT_HARD_CAP


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


import hashlib


# A second identical submission within this window is treated as an accidental resend.
BULK_DUP_WINDOW_MINUTES = 5
# How far back an identical submission can RESUME a prior failed/stalled batch instead of
# minting a fresh one (which would re-create the tickets the dead batch already made).
BULK_RESUME_WINDOW_MINUTES = 60
# A Sending/Queued batch whose record hasn't been touched in this long has no live worker
# (the job writes progress after every student), so it is safe to resume. Matches the job
# timeout so an actively-running long send is never mistaken for a dead one.
BULK_STALE_MINUTES = 25


def _bulk_fingerprint(sender, subject, message, ticket_type, groups):
    """Stable hash of a submission's identity — sender + content + the EXACT set of
    recipient addresses. Two submissions with the same fingerprint a few minutes apart
    are an accidental duplicate (BUG-4 guard) unless the agent confirms a resend."""
    emails = sorted({e for g in (groups or []) for e in (g.get("emails") or [])})
    basis = "".join(
        [
            cstr(sender or ""),
            cstr(subject or ""),
            cstr(message or ""),
            cstr(ticket_type or ""),
            ",".join(emails),
        ]
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _group_key(group):
    """Idempotency key for ONE send group — keyed on the STUDENT, never on raised_by.
    This is the BUG-1 fix: two emailless siblings who share a guardian resolve to the
    SAME recipient address (and so the same raised_by), but they are different students,
    so keying on the student id keeps them distinct and neither is dropped. Free-typed
    groups (no student) fall back to their sorted email set."""
    sid = cstr((group or {}).get("student") or "").strip().lower()
    if sid:
        return "s:" + sid
    return "e:" + "|".join(sorted((group or {}).get("emails") or []))


def _find_recent_duplicate_batch(fingerprint):
    """A non-failed batch with the same fingerprint in the last few minutes = an
    accidental resend (second tab, refresh-and-click, two agents). Returns it or None."""
    if not fingerprint:
        return None
    from frappe.utils import add_to_date, now_datetime

    cutoff = add_to_date(now_datetime(), minutes=-BULK_DUP_WINDOW_MINUTES)
    rows = frappe.get_all(
        "Unity Bulk Email Batch",
        filters={
            "fingerprint": fingerprint,
            "creation": [">=", cutoff],
            "status": ["!=", "Failed"],
        },
        fields=["name", "status", "sent_count", "total_count", "creation"],
        order_by="creation desc",
        limit=1,
    )
    return rows[0] if rows else None


def _find_resumable_batch(fingerprint):
    """A prior batch with the SAME fingerprint that has no live worker — either it was
    marked ``Failed``, or it is a ``Sending``/``Queued`` record whose progress hasn't
    advanced in ``BULK_STALE_MINUTES`` (the job writes after every student, so a stale
    record means the worker died, e.g. a SIGKILL on timeout that skipped the Failed
    handler). Resuming THIS batch — re-enqueuing the job with the same batch_name/batch_id
    — is the BUG-6 fix: the job reloads ``processed_keys`` and skips the students it
    already handled, so a resend-after-failure never re-creates the tickets the dead run
    already made (the 360 -> 720 duplication). Resume is always idempotent, so it needs no
    confirmation. Returns the batch row (incl. ``batch_id``) or None."""
    if not fingerprint:
        return None
    from frappe.utils import add_to_date, get_datetime, now_datetime

    now = now_datetime()
    window_cutoff = add_to_date(now, minutes=-BULK_RESUME_WINDOW_MINUTES)
    stale_cutoff = get_datetime(add_to_date(now, minutes=-BULK_STALE_MINUTES))
    rows = frappe.get_all(
        "Unity Bulk Email Batch",
        filters={
            "fingerprint": fingerprint,
            "creation": [">=", window_cutoff],
            "status": ["in", ["Failed", "Sending", "Queued"]],
        },
        fields=["name", "batch_id", "status", "processed_count", "total_count", "modified", "creation"],
        order_by="modified desc",
        limit=5,
    )
    for row in rows:
        if row.status == "Failed":
            return row
        # A Sending/Queued batch is resumable only once its worker is clearly gone —
        # otherwise we'd race a still-running job on the same record.
        if get_datetime(row.modified) < stale_cutoff:
            return row
    return None


@frappe.whitelist()
def bulk_send_email(
    subject,
    message,
    groups=None,
    cc=None,
    ticket_type=None,
    attachments=None,
    mode=None,
    confirm_resend=None,
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

    # One optional CC/BCC list. It is NO LONGER attached to every per-student mail
    # (that flooded the CC address with one copy per student and exposed it to every
    # family — BUG-6). The job sends the CC list ONE hidden copy of the broadcast.
    cc_list, invalid_cc_count = _split_email_list_with_counts(cc)

    # Per-student (and per-free-email) groups: one ticket + one email each.
    normalized_groups, invalid_count, total_recipients = _normalize_bulk_email_groups(
        groups, recipients=recipients, bcc=bcc, merge_data=merge_data
    )
    if not normalized_groups:
        frappe.throw(_("Add at least one recipient (student) before sending"))

    # Hard caps bound the send volume; over the limit we throw (rather than silently
    # send a partial batch) so the user knowingly splits it. The primary cap is on
    # DISTINCT STUDENTS (one ticket + one mail each); a second, higher ceiling bounds the
    # total address volume so guardians never blow past a sane maximum. (PR#2 replaces
    # both with auto-split parallel chunks.)
    student_count = len(normalized_groups)
    if student_count > STUDENT_HARD_CAP:
        frappe.throw(
            _("This send has {0} students, over the {1}-student limit for one send. Split it into smaller batches.").format(
                student_count, STUDENT_HARD_CAP
            )
        )
    if total_recipients + len(cc_list) > TOTAL_ADDRESS_HARD_CAP:
        frappe.throw(
            _("This send has {0} total addresses (students + guardians + cc), over the {1} limit. Reduce the recipients or turn off guardians.").format(
                total_recipients + len(cc_list), TOTAL_ADDRESS_HARD_CAP
            )
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

    # DUPLICATE-SUBMISSION GUARD (BUG-4). The per-batch id alone only stops a re-run of
    # the SAME enqueued job; a second submission (second tab, refresh-and-click, two
    # agents) mints a new id and would duplicate everyone. Fingerprint the submission
    # (sender + content + exact recipient set) and refuse an identical one sent in the
    # last few minutes — unless the agent explicitly confirms a resend.
    fingerprint = _bulk_fingerprint(
        frappe.session.user, subject, message, ticket_type, normalized_groups
    )
    resend_confirmed = cstr(confirm_resend or "").strip().lower() in ("1", "true", "yes")
    if not resend_confirmed:
        dup = _find_recent_duplicate_batch(fingerprint)
        if dup:
            return {
                "ok": False,
                "duplicate": True,
                "existing_batch": dup["name"],
                "student_count": len(normalized_groups),
                "message": _(
                    "You already sent this exact email {0} and it is still being processed. "
                    "Resend only if you are sure it did not go out."
                ).format(frappe.utils.pretty_date(dup["creation"])),
            }

    # Respect HD Settings "instantly_send_email". The job now always QUEUES each mail
    # (delayed) so a large batch can't block on synchronous SMTP and blow the worker
    # timeout (BUG-3); `instant` only asks the job to flush THIS batch's queued mail at
    # the end so delivery still feels immediate.
    instant = bool(int(frappe.db.get_single_value("HD Settings", "instantly_send_email") or 0))

    # AUTO-RESUME (BUG-6). If an identical prior send has no live worker — it Failed, or
    # stalled — RESUME that batch rather than minting a new one. Minting a new batch would
    # give it an empty processed_keys and re-create every ticket the dead run already made
    # (the 360 -> 720 duplication). Re-enqueuing the SAME batch_name/batch_id lets the job
    # skip already-handled students. Idempotent, so no prompt (runs even without confirm).
    resumable = _find_resumable_batch(fingerprint)
    if resumable:
        resume_batch_id = cstr(resumable.get("batch_id") or "") or resumable["name"]
        frappe.db.set_value(
            "Unity Bulk Email Batch",
            resumable["name"],
            {"status": "Queued", "error": None},
            update_modified=True,
        )
        frappe.db.commit()
        frappe.enqueue(
            "helpdesk.api.unity_helpdesk_ext._bulk_send_email_job",
            queue="long",
            timeout=1500,
            subject=subject,
            message=message,
            groups=normalized_groups,
            cc_list=cc_list,
            sender=frappe.session.user,
            file_names=file_names,
            ticket_type=ticket_type,
            now=instant,
            batch_id=resume_batch_id,
            batch_name=resumable["name"],
        )
        already_done = int(resumable.get("processed_count") or 0)
        total = int(resumable.get("total_count") or len(normalized_groups))
        return {
            "ok": True,
            "queued": True,
            "resumed": True,
            "instant": instant,
            "batch_id": resume_batch_id,
            "already_done": already_done,
            "tickets": [],
            "ticket": None,
            "ticket_count": 0,
            "student_count": len(normalized_groups),
            "recipient_count": total_recipients,
            "count": total_recipients,
            "invalid_count": invalid_count,
            "invalid_cc_count": invalid_cc_count,
            "message": _(
                "Resuming your previous send that didn't finish — {0} of {1} students were already done."
            ).format(already_done, total),
        }

    # Durable per-send record (BUG-2). The job updates it live, so the SPA can poll a
    # real progress bar and show an honest "X sent / K failed" result + an exportable
    # failed list, instead of a fire-and-forget "Done". It also carries the per-student
    # idempotency keys (processed_keys) so a re-run never duplicates or drops.
    batch_id = frappe.generate_hash(length=12)
    batch = frappe.get_doc(
        {
            "doctype": "Unity Bulk Email Batch",
            "batch_id": batch_id,
            "status": "Queued",
            "sender": frappe.session.user,
            "subject": subject,
            "ticket_type": ticket_type,
            "mode": cstr(mode or "").strip() or None,
            "fingerprint": fingerprint,
            "cc_recipients": ", ".join(cc_list) if cc_list else None,
            "total_count": len(normalized_groups),
            "processed_count": 0,
            "sent_count": 0,
            "failed_count": 0,
            "skipped_count": 0,
            "failed_rows": "[]",
            "processed_keys": "[]",
        }
    ).insert(ignore_permissions=True)
    frappe.db.commit()

    frappe.enqueue(
        "helpdesk.api.unity_helpdesk_ext._bulk_send_email_job",
        queue="long",
        timeout=1500,
        subject=subject,
        message=message,
        groups=normalized_groups,
        cc_list=cc_list,
        sender=frappe.session.user,
        file_names=file_names,
        ticket_type=ticket_type,
        now=instant,
        batch_id=batch_id,
        batch_name=batch.name,
    )

    return {
        "ok": True,
        "queued": True,
        "instant": instant,
        "batch_id": batch_id,
        # Tickets are created asynchronously in the worker — poll
        # get_bulk_email_batch_status(batch_id) for live progress. student_count is the
        # target ticket count.
        "tickets": [],
        "ticket": None,
        "ticket_count": 0,
        "student_count": len(normalized_groups),
        "recipient_count": total_recipients,
        "count": total_recipients,
        "invalid_count": invalid_count,
        "invalid_cc_count": invalid_cc_count,
    }


def _is_deadlock(exc):
    """True for a TRANSIENT MariaDB deadlock (1213) or lock-wait timeout (1205).
    Both mean "try restarting transaction" — the same statement almost always
    succeeds on a retry, so we must NOT drop the student on these."""
    args = getattr(exc, "args", None) or ()
    if args and args[0] in (1213, 1205):
        return True
    text = str(exc)
    return "Deadlock found" in text or "Lock wait timeout" in text


def _commit_with_retry(attempts=6):
    """Commit, retrying on a transient deadlock/lock-wait (1213/1205) instead of
    letting it escape the job.

    Why this matters: an UNGUARDED commit that deadlocks bubbles out of
    ``_bulk_send_email_job``; because that job runs via ``frappe.enqueue``, the
    raised ``InternalError`` reaches frappe's worker wrapper ``execute_job``
    (frappe/utils/background_jobs.py), which on a deadlock RE-RUNS THE ENTIRE job up
    to 5x — and since the job was not idempotent, each re-run re-created all the
    tickets. That silent whole-job retry is what turned a 40-student send into ~159
    tickets. Absorbing the transient failure here keeps it local to this student."""
    for attempt in range(attempts):
        try:
            frappe.db.commit()
            return
        except Exception as exc:
            if _is_deadlock(exc) and attempt < attempts - 1:
                frappe.db.rollback()
                time.sleep(0.2 * (attempt + 1))
                continue
            raise


def _bulk_send_email_job(
    subject,
    message,
    groups,
    cc_list=None,
    sender=None,
    file_names=None,
    ticket_type=None,
    now=False,
    batch_id=None,
    batch_name=None,
):
    """Send a PER-STUDENT bulk email. Always runs in a background worker (enqueued by
    ``bulk_send_email``).

    For each group (one student + their guardians, or one free-typed address) it:
      * renders the subject + message against that student's record + carried merge
        data (``{**student, **data}``) — so {{first_name}} etc. fill per student,
      * SANITISES the rendered subject + body (BUG-7: merge values are cleaned AFTER
        the merge, so markup carried in a Student field never reaches the mail),
      * creates ONE HD Ticket (description = rendered message only), raised_by the
        student's own ``user`` email when resolvable (never ``student_email_id``),
      * QUEUES ONE email (``delayed=True`` — BUG-3: never a synchronous SMTP send in
        the loop, so a large batch can't blow the worker timeout), recipients visible.
    The optional CC/BCC list is sent ONE hidden copy at the end, not per student (BUG-6).

    Idempotency is keyed on the STUDENT (``processed_keys`` on the batch record), NOT on
    raised_by — so two emailless siblings sharing a guardian stay distinct (BUG-1) and a
    re-run (worker restart, resend) never duplicates or drops. Live counts + the failed
    list are written to the Unity Bulk Email Batch record so the SPA shows real progress
    and an honest result (BUG-2). It NEVER re-raises — a raised error would let frappe's
    execute_job wrapper re-run the whole batch.
    """
    from frappe.utils import now_datetime, sanitize_html

    groups = groups or []
    cc_list = cc_list or []
    file_names = file_names or []

    # Load prior progress from the batch record so a resumed run continues, not restarts.
    batch = None
    processed_keys = set()
    failed_rows = []
    counts = {"processed": 0, "sent": 0, "failed": 0, "skipped": 0}
    if batch_name and frappe.db.exists("Unity Bulk Email Batch", batch_name):
        batch = frappe.get_doc("Unity Bulk Email Batch", batch_name)
        processed_keys = set(_parse_json(batch.processed_keys, []) or [])
        failed_rows = _parse_json(batch.failed_rows, []) or []
        counts = {
            "processed": int(batch.processed_count or 0),
            "sent": int(batch.sent_count or 0),
            "failed": int(batch.failed_count or 0),
            "skipped": int(batch.skipped_count or 0),
        }

    def _save_batch(status=None, finished=False):
        """Persist counters + processed_keys + failed_rows to the batch record. Uses a
        single-row set_value (cheap) and is committed by the surrounding _commit_with_retry."""
        if not batch_name:
            return
        fields = {
            "processed_count": counts["processed"],
            "sent_count": counts["sent"],
            "failed_count": counts["failed"],
            "skipped_count": counts["skipped"],
            "processed_keys": frappe.as_json(sorted(processed_keys)),
            "failed_rows": frappe.as_json(failed_rows),
        }
        if status:
            fields["status"] = status
        if finished:
            fields["finished_at"] = now_datetime()
        try:
            frappe.db.set_value("Unity Bulk Email Batch", batch_name, fields, update_modified=True)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Unity bulk batch update failed")

    tickets = []
    try:
        sendmail_attachments = [{"fid": name} for name in file_names]

        # Resolve every student record in ONE query for the merge context.
        student_ids = [g["student"] for g in groups if g.get("student")]
        students_by_name = _students_by_name(student_ids) if student_ids else {}

        if batch_name:
            frappe.db.set_value(
                "Unity Bulk Email Batch",
                batch_name,
                {"status": "Sending", "started_at": now_datetime()},
                update_modified=True,
            )
            _commit_with_retry()

        for group in groups:
            key = _group_key(group)
            # BUG-1 / re-run safety: skip a student ALREADY handled (this run or a prior
            # one), keyed on the student — NOT on raised_by (which collides for emailless
            # siblings who share a guardian).
            if key in processed_keys:
                counts["skipped"] += 1
                _save_batch()
                _commit_with_retry()
                continue

            emails = group.get("emails") or []
            sid = cstr(group.get("student") or "").strip()
            student = students_by_name.get(sid.lower()) if sid else None
            recipient_hint = (emails[0] if emails else "") or (sid or "")

            if not emails:
                failed_rows.append(
                    {"student": sid, "email": "", "reason": "No deliverable email"}
                )
                counts["failed"] += 1
                processed_keys.add(key)
                counts["processed"] += 1
                _save_batch()
                _commit_with_retry()
                continue

            context = {**(student or {}), **(group.get("data") or {})}
            # Render, then SANITISE the rendered result (BUG-7) so a merge value carrying
            # markup can't reach the stored ticket OR the outgoing mail.
            rendered_subject = sanitize_html(_safe_render(subject, context) or subject)
            rendered_message = sanitize_html(_safe_render(message, context))

            # raised_by: the student's own `user` email when resolvable (never
            # student_email_id), else the first recipient, else the agent. Only used to
            # group the ticket under the student — NOT for idempotency anymore.
            raised_by = _student_primary_email(student) or emails[0] or sender

            payload = {
                "doctype": TICKET_DOCTYPE,
                "subject": rendered_subject,
                "raised_by": raised_by,
                "description": rendered_message,
                "status": "Open",
                "ticket_type": ticket_type,
            }
            if _has_field(TICKET_DOCTYPE, "custom_via_unity_portal"):
                payload["custom_via_unity_portal"] = 1
            if _has_field(TICKET_DOCTYPE, "custom_is_bulk_email"):
                payload["custom_is_bulk_email"] = 1
            if _has_field(TICKET_DOCTYPE, "custom_bulk_email_recipients"):
                payload["custom_bulk_email_recipients"] = ", ".join(sorted(set(emails)))
            if batch_id and _has_field(TICKET_DOCTYPE, "custom_bulk_batch_id"):
                payload["custom_bulk_batch_id"] = batch_id

            # Insert with retry-on-deadlock (transient 1213/1205 during the concurrent
            # search-index updates). A genuine failure records the student as failed
            # (with a reason for the exportable list) and moves on — never aborts.
            doc = None
            insert_error = ""
            for _attempt in range(6):
                try:
                    doc = frappe.get_doc(payload).insert(ignore_permissions=True)
                    break
                except Exception as exc:
                    frappe.db.rollback()
                    if _is_deadlock(exc) and _attempt < 5:
                        time.sleep(0.2 * (_attempt + 1))
                        continue
                    insert_error = cstr(exc)[:180] or "Ticket create failed"
                    frappe.log_error(
                        frappe.get_traceback(),
                        "Unity bulk per-student ticket insert failed",
                    )
                    break

            processed_keys.add(key)
            counts["processed"] += 1

            if doc is None:
                failed_rows.append(
                    {"student": sid, "email": recipient_hint, "reason": insert_error or "Ticket create failed"}
                )
                counts["failed"] += 1
                _save_batch()
                _commit_with_retry()
                continue

            tickets.append(doc.name)
            _commit_with_retry()

            # QUEUE the mail (delayed=True ALWAYS — BUG-3). No per-student CC (BUG-6).
            send_error = ""
            sent_ok = False
            for _attempt in range(6):
                try:
                    frappe.sendmail(
                        recipients=emails,
                        subject=rendered_subject,
                        message=rendered_message,
                        attachments=sendmail_attachments,
                        delayed=True,
                        reference_doctype=TICKET_DOCTYPE,
                        reference_name=doc.name,
                        expose_recipients="header",
                    )
                    sent_ok = True
                    break
                except Exception as exc:
                    if _is_deadlock(exc) and _attempt < 5:
                        frappe.db.rollback()
                        time.sleep(0.2 * (_attempt + 1))
                        continue
                    send_error = cstr(exc)[:180] or "Email queue failed"
                    frappe.log_error(
                        frappe.get_traceback(),
                        f"Unity bulk send failed for ticket {doc.name}",
                    )
                    break

            if sent_ok:
                counts["sent"] += 1
            else:
                # Ticket exists but the mail couldn't be queued — record it so the agent
                # can see (and re-send) exactly who missed out.
                failed_rows.append(
                    {"student": sid, "email": recipient_hint, "reason": send_error or "Email queue failed"}
                )
                counts["failed"] += 1

            _save_batch()
            _commit_with_retry()

        # ONE hidden copy of the broadcast to the CC/BCC list — sent once, never per
        # student, and never exposed to the families (BUG-6).
        if cc_list:
            try:
                note = (
                    f"<p style='color:#888;font-size:12px'>Copy of a bulk message sent to "
                    f"{counts['sent']} recipient(s).</p>"
                )
                frappe.sendmail(
                    recipients=cc_list,
                    subject="[Broadcast copy] " + sanitize_html(cstr(subject or "")),
                    message=note + sanitize_html(cstr(message or "")),
                    attachments=sendmail_attachments,
                    delayed=True,
                )
                _commit_with_retry()
            except Exception:
                frappe.log_error(frappe.get_traceback(), "Unity bulk CC copy failed")

        final_status = "Completed" if counts["failed"] == 0 else "Completed with Errors"
        _save_batch(status=final_status, finished=True)
        _commit_with_retry()
        return {"tickets": tickets, **counts, "batch": batch_name}
    except Exception:
        # Roll back, record the job-level failure on the batch, log — but DO NOT re-raise
        # (a raised error would let execute_job re-run the whole batch).
        frappe.db.rollback()
        if batch_name:
            try:
                frappe.db.set_value(
                    "Unity Bulk Email Batch",
                    batch_name,
                    {
                        "status": "Failed",
                        "error": (frappe.get_traceback() or "")[-900:],
                        "finished_at": now_datetime(),
                        "processed_count": counts["processed"],
                        "sent_count": counts["sent"],
                        "failed_count": counts["failed"],
                        "skipped_count": counts["skipped"],
                        "failed_rows": frappe.as_json(failed_rows),
                        "processed_keys": frappe.as_json(sorted(processed_keys)),
                    },
                    update_modified=True,
                )
                frappe.db.commit()
            except Exception:
                frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "Unity Helpdesk _bulk_send_email_job")
        return {"tickets": tickets, **counts, "error": True, "batch": batch_name}


@frappe.whitelist()
def get_bulk_email_batch_status(batch_id):
    """Live status of a bulk send (BUG-2), polled by the SPA to drive the progress bar
    and the final "X sent / K failed" result + exportable failed list. Gated like the
    send itself (can_view_all_tickets)."""
    capabilities = _require_unity_access()
    if not capabilities.get("can_view_all_tickets"):
        frappe.throw(_("You are not allowed to view bulk email status"), frappe.PermissionError)

    batch_id = cstr(batch_id or "").strip()
    if not batch_id or not frappe.db.exists("Unity Bulk Email Batch", batch_id):
        return {"found": False, "batch_id": batch_id}

    row = frappe.db.get_value(
        "Unity Bulk Email Batch",
        batch_id,
        [
            "batch_id",
            "status",
            "subject",
            "total_count",
            "processed_count",
            "sent_count",
            "failed_count",
            "skipped_count",
            "failed_rows",
            "started_at",
            "finished_at",
        ],
        as_dict=True,
    ) or {}

    failed = _parse_json(row.get("failed_rows"), []) or []
    total = int(row.get("total_count") or 0)
    processed = int(row.get("processed_count") or 0)
    skipped = int(row.get("skipped_count") or 0)
    done = row.get("status") in ("Completed", "Completed with Errors", "Failed")
    return {
        "found": True,
        "batch_id": row.get("batch_id"),
        "status": row.get("status"),
        "subject": row.get("subject"),
        "total": total,
        "processed": processed,
        "sent": int(row.get("sent_count") or 0),
        "failed": int(row.get("failed_count") or 0),
        "skipped": skipped,
        # Progress counts both processed students and skipped (already-done) ones so the
        # bar reaches 100% even when a re-run skipped some.
        "progress": min(100, round(((processed + skipped) / total) * 100)) if total else (100 if done else 0),
        "done": done,
        "failed_rows": failed,
        "started_at": str(row.get("started_at")) if row.get("started_at") else None,
        "finished_at": str(row.get("finished_at")) if row.get("finished_at") else None,
    }


@frappe.whitelist()
def send_test_email(
    subject,
    message,
    test_email,
    ticket_type=None,
    groups=None,
    raised_by=None,
    attachments=None,
):
    """Send ONE test copy of a bulk / new-ticket email to `test_email` before the
    real send, so an admin can eyeball the rendered result in their own inbox.

    It walks the SAME validate → render → attach → send path as the real send
    (bulk_send_email / create_ticket's reply_via_agent) but:
      * creates NO ticket and mails ONLY `test_email` (never the real recipients/CC),
      * renders against the FIRST real recipient's merge context — the first
        `groups` entry's ``{**student, **data}`` (bulk), or
        ``_merge_context_for_email(raised_by)`` (new-ticket) — so what the admin
        sees is exactly what the first parent would get,
      * prefixes the subject with ``[TEST] `` and sends immediately (delayed=False).

    Reusing the real path's checks means a misconfigured outgoing account, an
    invalid ticket type, or a bad template surfaces at TEST time, not mid-batch.
    """
    # Gate to match the operation being tested, not stricter: the bulk path
    # (`groups`) needs can_view_all_tickets exactly like bulk_send_email; the
    # new-ticket path needs only basic access, exactly like create_ticket — else an
    # agent who can create+send a single ticket but lacks all-tickets access would
    # be blocked from testing it (and stuck, since the real send is gated on it).
    capabilities = _require_unity_access()
    if groups and not capabilities.get("can_view_all_tickets"):
        frappe.throw(
            _("You are not allowed to send bulk test emails"),
            frappe.PermissionError,
        )

    subject = cstr(subject or "").strip()
    raw_message = cstr(message or "").strip()
    ticket_type = cstr(ticket_type or "").strip()
    if not subject:
        frappe.throw(_("Subject is required"))
    if not raw_message:
        frappe.throw(_("Message is required"))
    if ticket_type and not frappe.db.exists("HD Ticket Type", ticket_type):
        frappe.throw(_("Invalid Ticket Type: {0}").format(ticket_type))

    # One or more comma/semicolon-separated tester addresses — validated, lowercased
    # and deduped. A test can go to several verifiers at once (all in one send).
    test_emails, invalid_test_count = _split_email_list_with_counts(test_email)
    if not test_emails:
        frappe.throw(_("Enter at least one valid email address to send the test copy to"))
    if invalid_test_count:
        frappe.throw(
            _("{0} test email address(es) are invalid — fix or remove them").format(invalid_test_count)
        )

    # Strip script tags, on* handlers, javascript: URLs, etc. — same as the real send.
    from frappe.utils import sanitize_html
    message = sanitize_html(raw_message)
    if not cstr(message).strip():
        frappe.throw(_("Message is required"))

    # Fail fast if there's no outgoing email account (same guard as bulk_send_email).
    from frappe.email.doctype.email_account.email_account import EmailAccount
    if not EmailAccount.find_default_outgoing():
        frappe.throw(
            _("No default outgoing Email Account is configured. Please configure one before sending a test email."),
            frappe.OutgoingEmailError,
        )

    # Merge context = the FIRST real recipient's data, so the test renders exactly
    # like the first parent's mail. Bulk: first normalized group's student+row.
    # New-ticket: the raised_by student's record. Neither present -> blank render.
    context = {}
    if groups:
        normalized_groups, _invalid, _total = _normalize_bulk_email_groups(groups)
        if not normalized_groups:
            frappe.throw(_("Add at least one recipient (student) before sending a test"))
        first = normalized_groups[0]
        sid = cstr(first.get("student") or "").strip()
        student = _students_by_name([sid]).get(sid.lower()) if sid else None
        context = {**(student or {}), **(first.get("data") or {})}
    elif raised_by:
        context = _merge_context_for_email(raised_by)

    # Render, then SANITISE the rendered output (BUG-7) so a merge value carrying markup
    # is cleaned in the test copy exactly as it is in the real send.
    from frappe.utils import sanitize_html
    rendered_subject = sanitize_html(_safe_render(subject, context) or subject)
    rendered_message = sanitize_html(_safe_render(message, context))

    # Resolve attachment File names cheaply (single IN(...) query) — same as bulk.
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
    sendmail_attachments = [{"fid": name} for name in file_names]

    # ONE mail, to the tester(s) only. No cc, no reference ticket. Sent immediately so
    # it lands in the verifiers' inboxes right away.
    frappe.sendmail(
        recipients=test_emails,
        subject="[TEST] " + rendered_subject,
        message=rendered_message,
        attachments=sendmail_attachments,
        delayed=False,
    )

    return {"ok": True, "sent_to": test_emails}


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
        # Read a generous buffer of raw rows (some may be duplicate students); the
        # distinct-student budget is enforced below on the resolved students.
        if len(parsed) >= STUDENT_HARD_CAP * 2:
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
    truncated = False  # set if we stop early on a cap (distinct students OR total addresses)
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
        # DISTINCT-STUDENT budget (the 500->359 fix): stop taking NEW students once the
        # cap is reached — the cap counts students, not the mixed student+guardian address
        # list, so guardians never evict students at parse time.
        if len(processed_students) >= STUDENT_HARD_CAP:
            truncated = True
            break
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

        # Absolute address ceiling (students + guardians). Far above the student cap, so
        # it only trips on pathological guardian counts — bounds the payload either way.
        if len(rows) >= TOTAL_ADDRESS_HARD_CAP:
            truncated = True
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

    # `truncated` (set in the loop above) => we stopped early on the distinct-student cap
    # or the total-address ceiling, so the CSV had more than one send can carry. The UI
    # warns the agent to split the batch rather than silently under-sending.

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
