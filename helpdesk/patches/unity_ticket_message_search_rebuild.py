"""Rebuild every HD Ticket's custom_search_message_body using the new
head + tail truncation layout (see unity_helpdesk._build_ticket_message_search_values).

The original backfill patch (unity_ticket_message_search_fields) capped at 5000
tickets and skipped already-populated rows. Both of those guards are
deliberately removed here:

- No cap — every ticket gets refreshed once.
- No "already populated" short-circuit — the old layout concatenated
  oldest-first then kept the first 12KB, dropping recent agent replies. We
  need to rewrite those rows so the latest replies are present in the index.

Chunked in 200-row batches with a per-batch commit. Per-row failures are
logged and skipped, never blocking the migration.
"""
import frappe

BATCH_SIZE = 200


def execute():
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
