# Bulk Email

The Unity Helpdesk SPA can send one email to many recipients at once ("Send Bulk
Email"). It is designed for school → guardian/student broadcasts and always leaves an
auditable trail.

## What it does

- Composes a single message and delivers it to many recipients via **BCC**, so
  recipients never see each other's addresses.
- Optionally expands each selected **student** to their **guardian** email addresses
  (see [Education integration](education-integration.md)).
- Creates **one audit HD Ticket** per send that records the full recipient list
  (`custom_bulk_email_recipients`) and is flagged with `custom_is_bulk_email`.
- Optionally BCCs a configurable set of **default recipients** (e.g. an audit mailbox)
  on every bulk email — see [Configuration](configuration.md).

## How it works

Endpoint: `helpdesk.api.unity_helpdesk_ext.bulk_send_email`.

1. **Cheap, synchronous validation** (runs in the request, returns errors immediately):
   permission check (`can_view_all_tickets`), required subject/message/ticket type,
   HTML sanitisation, a fail-fast check that a default outgoing Email Account exists,
   recipient parsing/de-duplication/validation, and the address hard caps
   (`RECIPIENT_HARD_CAP`, `TOTAL_ADDRESS_HARD_CAP`). Attachments are resolved with a
   single `IN (...)` query.
2. The validated payload is handed to a **background job**
   (`_bulk_send_email_job`, enqueued via `frappe.enqueue`). The request returns in
   milliseconds with `{ "queued": true, "count": <n> }`; the composer shows an instant
   success toast.
3. The job (running in a worker) creates the audit ticket, creates the thread
   `Communication`, and sends **one** `frappe.sendmail(..., delayed=True)` with all real
   recipients **plus** the configured default recipients in BCC.

> Why a background job: the audit-ticket and Communication inserts trigger the full HD
> Ticket lifecycle (SLA, assignment rules, search-index hooks). Doing that in-request
> caused a 3–4 s "Sending…" stall. Moving it to a worker makes the send feel instant
> while the email itself is queued to the Email Queue and dispatched by the mail worker.

## Recipient layout (one send, audit in BCC)

A single email is sent. The agent (sender) is the visible `To`; everyone else —
students, expanded guardians, any extra BCC, and the configured default recipients — is
in **BCC**. The full recipient list is not exposed in the email headers; it lives on the
audit ticket, where the default-recipient/owner can see exactly who was emailed.

If no default recipients are configured, only the real recipients are BCC'd — the
feature works the same, just without an audit copy.

## Related

- [Configuration](configuration.md) — the default-recipients setting.
- [Education integration](education-integration.md) — guardian expansion.
- [Performance](performance.md) — why sends and replies are fast.
