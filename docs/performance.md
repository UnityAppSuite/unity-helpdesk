# Performance

Unity Helpdesk is tuned for large ticket tables (90k+ rows) and slow/remote SMTP.

## Fast list & dashboard

- Dashboard cards use narrow per-status `COUNT(*)` queries backed by composite indexes
  (`status_modified_unity_idx`, `on_hold_modified_unity_idx`) instead of a full-table
  `SUM(CASE)` scan; with filters it falls back to a single aggregate.
- The empty-search dashboard summary is Redis-cached for a short window.
- Search runs FULLTEXT-first for long queries with a legacy LIKE fallback.

## Fast sending (bulk email & replies)

Both flows avoid blocking the request on SMTP and on heavy document hooks.

### Bulk email
The composer returns in milliseconds. Validation is synchronous; the audit-ticket +
Communication creation and the actual send run in a **background job**
(`_bulk_send_email_job`). See [Bulk Email](bulk-email.md).

### Agent reply
`helpdesk.api.unity_helpdesk_ext.reply`:

- Keeps the email send **asynchronous** (queued, not `now=True`).
- Defers the post-reply search-index rebuild to a background job
  (`frappe.enqueue(..., enqueue_after_commit=True)`) instead of running it inline.
- Returns the newly created `Communication` so the SPA can **optimistically append** it
  to the thread. `TicketDetailView.sendReply` no longer blocks on a full `loadTicket()`
  reload, so the "Sending…" spinner clears as soon as the reply is recorded.

The net effect: the previous ~3–4 s stall on both bulk send and reply is gone; the email
is dispatched by the mail worker moments later.

## Notes for operators

- A **worker** must be running to process the background jobs and the Email Queue
  (standard for any Frappe bench: `bench worker` / supervisor).
- Replies appear instantly in the UI; delivery follows via the Email Queue.
