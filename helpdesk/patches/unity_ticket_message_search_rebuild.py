"""Rebuild every HD Ticket's custom_search_message_body using the new
head + tail truncation layout (see unity_helpdesk._build_ticket_message_search_values).

The original backfill patch (unity_ticket_message_search_fields) capped at 5000
tickets and skipped already-populated rows. Both of those guards are
deliberately removed here:

- No cap — every ticket gets refreshed once.
- No "already populated" short-circuit — the old layout concatenated
  oldest-first then kept the first 12KB, dropping recent agent replies. We
  need to rewrite those rows so the latest replies are present in the index.

Deferred to a long-queue background job so `bench migrate` doesn't block on a
multi-hour sweep. Re-run manually after a failure with:
  bench --site <site> execute helpdesk.patches.unity_ticket_message_search_rebuild.run_message_search_rebuild

Search continues to work during the backfill: new tickets are indexed live by
the Communication / HD Ticket Comment on_update hooks (see search_index.py);
only historical tickets show partial search results until the job drains.
"""
import time

import frappe

BATCH_SIZE = 50
# Idle gap between batches. Lets foreground SPA requests acquire row locks and
# keeps the InnoDB buffer pool from being flooded by the backfill's UPDATE
# stream. ~3× slower wall-clock for the full sweep, but the SPA's list-page
# query stays responsive throughout.
_BATCH_SLEEP_SEC = 0.2
# Threshold for considering the backfill "done enough" to skip on subsequent
# migrates. 0.5% covers cases where a few rows legitimately have an empty body
# (no description, no replies) — those would otherwise keep tripping re-runs.
_COMPLETE_PCT_THRESHOLD = 0.005


def execute():
	start = time.monotonic()
	try:
		# Recent rows were populated synchronously by unity_ticket_message_search_fields.
		# The full sweep runs async — migrate stays fast.
		frappe.enqueue(
			"helpdesk.patches.unity_ticket_message_search_rebuild.run_message_search_rebuild",
			queue="long",
			timeout=21600,
			is_async=True,
			job_id="unity_message_search_rebuild",
			deduplicate=True,
			enqueue_after_commit=True,
		)
	finally:
		frappe.logger().info(
			f"[unity-patch] unity_ticket_message_search_rebuild took {time.monotonic() - start:.2f}s"
		)


def _backfill_is_complete():
	"""Cheap COUNT(*) check — used to short-circuit re-runs of the enqueued
	job on subsequent migrates. If nearly every ticket already has a populated
	custom_search_message_body, there's nothing useful for the worker to do.

	Use the Frappe "is / not set" operator (expands to `IS NULL OR = ''`).
	A plain `["in", ["", None]]` filter would NOT match true NULL rows because
	`column IN (NULL)` never matches in SQL.
	"""
	total = frappe.db.count("HD Ticket")
	if total == 0:
		return True
	pending = frappe.db.count(
		"HD Ticket",
		filters=[["custom_search_message_body", "is", "not set"]],
	)
	if pending == 0:
		return True
	return (pending / total) < _COMPLETE_PCT_THRESHOLD


def run_message_search_rebuild():
	"""Background job: rebuild custom_search_message_body for every HD Ticket
	in modified-desc order. Chunked, per-batch commits, per-row try/except so
	one bad ticket can't block the sweep. Idempotent — _backfill_is_complete()
	short-circuits when there's nothing left to populate."""
	if _backfill_is_complete():
		frappe.logger().info(
			"[unity-patch] message-search-rebuild: already complete, skipping"
		)
		return

	from helpdesk.api.unity_helpdesk import update_ticket_message_search_index

	start = 0
	while True:
		rows = frappe.get_all(
			"HD Ticket",
			fields=["name"],
			order_by="modified desc",
			limit_start=start,
			page_length=BATCH_SIZE,
		)
		if not rows:
			break

		for row in rows:
			try:
				update_ticket_message_search_index(row.name)
			except Exception:
				frappe.log_error(
					title="Unity message search rebuild",
					message=frappe.get_traceback(),
				)

		frappe.db.commit()
		if len(rows) < BATCH_SIZE:
			break
		start += BATCH_SIZE
		# Yield to foreground requests + give InnoDB room to flush.
		time.sleep(_BATCH_SLEEP_SEC)
