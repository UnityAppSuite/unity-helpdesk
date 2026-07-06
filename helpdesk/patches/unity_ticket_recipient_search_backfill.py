"""Backfill custom_search_recipient_emails for HD Tickets indexed before the field
existed. Enqueues a long-queue background job so `bench migrate` stays fast; new and
updated tickets populate inline via the search-index doc hooks regardless.

Idempotent: re-running on a fully-populated DB is a single COUNT and returns.
Batched + brief sleep so foreground SPA requests stay responsive while it drains.

Completeness is tracked with a raw `IS NULL` count, NOT Frappe's "is not set":
a ticket with no external recipients is legitimately stored as "" (empty, not
NULL), so IS NULL distinguishes "never processed" from "processed, none" and the
sweep terminates instead of looping on empty-recipient tickets.

Re-run manually:
  bench --site <site> execute helpdesk.patches.unity_ticket_recipient_search_backfill.run_recipient_search_backfill
"""
import time

import frappe

_BATCH = 50
_BATCH_SLEEP_SEC = 0.2
_COLUMN = "custom_search_recipient_emails"


def execute():
	if not frappe.db.has_column("HD Ticket", _COLUMN):
		# Field patch hasn't run yet (ordering) — it will enqueue on the next migrate.
		return
	frappe.enqueue(
		"helpdesk.patches.unity_ticket_recipient_search_backfill.run_recipient_search_backfill",
		queue="long",
		timeout=21600,
		is_async=True,
		job_id="unity_recipient_search_backfill",
		deduplicate=True,
		enqueue_after_commit=True,
	)


def _backfill_is_complete():
	if not frappe.db.has_column("HD Ticket", _COLUMN):
		return True
	pending = frappe.db.sql(
		f"SELECT COUNT(*) FROM `tabHD Ticket` WHERE `{_COLUMN}` IS NULL"
	)[0][0]
	return not pending


def run_recipient_search_backfill(limit=None):
	"""Populate the recipient field for tickets still NULL. ``limit`` (optional)
	caps the number of tickets processed this run — useful for a controlled
	off-peak chunk or a smoke test; omit to drain everything."""
	if _backfill_is_complete():
		frappe.logger().info(
			"[unity-patch] recipient-search-backfill: already complete, skipping"
		)
		return

	try:
		from helpdesk.api.unity_helpdesk import update_ticket_message_search_index
	except ImportError:
		return

	remaining = int(limit) if limit else None
	processed = 0
	prev_batch_names = None
	while remaining is None or remaining > 0:
		page = _BATCH if remaining is None else min(_BATCH, remaining)
		# Re-query each batch: once a ticket's recipient field is written (to emails
		# or ""), it stops being NULL and drops out of the pending head.
		batch = frappe.db.sql(
			f"""SELECT name FROM `tabHD Ticket`
			    WHERE `{_COLUMN}` IS NULL
			    ORDER BY modified DESC LIMIT %s""",
			(page,),
			as_dict=True,
		)
		if not batch:
			break
		batch_names = [row.name for row in batch]
		# Safety net: a full batch that repeats unchanged means none of these rows
		# cleared (e.g. a ticket whose update_ticket_message_search_index persistently
		# raises below) — the IS NULL head would then loop forever. Break + log once
		# instead of pinning the worker and flooding the Error Log.
		if batch_names == prev_batch_names:
			frappe.log_error(
				f"recipient-search-backfill stalled on {len(batch_names)} unpopulated "
				f"ticket(s) (e.g. {batch_names[:5]}); aborting to avoid an infinite loop",
				"unity_ticket_recipient_search_backfill",
			)
			break
		prev_batch_names = batch_names
		for row in batch:
			try:
				# Rebuilds body + recipients in one thread fetch (idempotent).
				update_ticket_message_search_index(row.name)
			except Exception:
				frappe.log_error(
					frappe.get_traceback(), "unity_ticket_recipient_search_backfill"
				)
		frappe.db.commit()
		processed += len(batch)
		if remaining is not None:
			remaining -= len(batch)
		if len(batch) < page:
			break
		time.sleep(_BATCH_SLEEP_SEC)
	frappe.logger().info(f"[unity-patch] recipient-search-backfill processed {processed} tickets")
