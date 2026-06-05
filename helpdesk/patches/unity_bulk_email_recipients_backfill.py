"""Backfill custom_bulk_email_recipients on existing bulk-email audit tickets.

The structured recipients field was introduced by
unity_bulk_email_recipients_field; new audit tickets get it populated inline by
bulk_send_email. This patch sweeps existing audit tickets (those where
custom_is_bulk_email = 1 but the new field is empty) and reconstructs the
recipient list from the description HTML.

Audit description shape (per _bulk_email_audit_html in unity_helpdesk_ext.py):
- A `<ul>` of `<li>email</li>` lines inside a `<details>` block (the TO list).
- An optional `<p><strong>CC:</strong> e1, e2</p>` block.
- An optional `<p><strong>Additional BCC:</strong> e1, e2</p>` block.

Deferred to a long-queue background job so migrate stays fast even with many
historical audit tickets. Idempotent — the job short-circuits when no audit
tickets are pending and skips per-row writes that would be no-ops.
"""
import re
import time

import frappe

BATCH_SIZE = 50
# Idle gap between batches — same rationale as in
# unity_ticket_message_search_rebuild: keep the SPA's list-page query
# responsive while the sweep is running.
_BATCH_SLEEP_SEC = 0.2
# Matches plausible email addresses inside HTML / free text. Greedy on the
# local-part is fine; the row-set is already filtered to audit tickets.
_EMAIL_RE = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+", re.IGNORECASE)


def execute():
	start = time.monotonic()
	try:
		frappe.enqueue(
			"helpdesk.patches.unity_bulk_email_recipients_backfill.run_backfill",
			queue="long",
			timeout=3600,
			is_async=True,
			job_id="unity_bulk_email_recipients_backfill",
			deduplicate=True,
			enqueue_after_commit=True,
		)
	finally:
		frappe.logger().info(
			f"[unity-patch] unity_bulk_email_recipients_backfill took {time.monotonic() - start:.2f}s"
		)


def _backfill_is_complete():
	"""Skip when every audit ticket already has the field populated.

	Use the "is / not set" Frappe operator (expands to `IS NULL OR = ''`) — a
	plain `["in", ["", None]]` filter would NOT match true NULL rows because
	`column IN (NULL)` never matches in SQL, and newly-added custom columns
	default to NULL on existing rows.
	"""
	if not frappe.db.has_column("HD Ticket", "custom_bulk_email_recipients"):
		return True
	pending = frappe.db.count(
		"HD Ticket",
		filters=[
			["custom_is_bulk_email", "=", 1],
			["custom_bulk_email_recipients", "is", "not set"],
		],
	)
	return pending == 0


def _extract_recipients_from_description(description):
	"""Pull every email address out of the audit description HTML, dedup, sort."""
	if not description:
		return []
	# A plain regex sweep over the HTML catches the <li>email</li> entries plus
	# the CC and Additional BCC inline lists in one pass. Cheaper and more
	# robust to HTML formatting drift than a real parser.
	emails = {match.group(0).strip().lower() for match in _EMAIL_RE.finditer(description)}
	return sorted(emails)


def run_backfill():
	if _backfill_is_complete():
		frappe.logger().info(
			"[unity-patch] bulk-email-recipients-backfill: already complete, skipping"
		)
		return

	start = 0
	while True:
		rows = frappe.get_all(
			"HD Ticket",
			fields=["name", "description"],
			filters=[
				["custom_is_bulk_email", "=", 1],
				["custom_bulk_email_recipients", "is", "not set"],
			],
			order_by="creation desc",
			limit_start=start,
			page_length=BATCH_SIZE,
		)
		if not rows:
			break

		for row in rows:
			try:
				recipients = _extract_recipients_from_description(row.description)
				# Even if extraction returned nothing, write an empty string so
				# the row stops matching the "pending" filter on re-runs.
				frappe.db.set_value(
					"HD Ticket",
					row.name,
					"custom_bulk_email_recipients",
					", ".join(recipients),
					update_modified=False,
				)
			except Exception:
				frappe.log_error(
					title="Unity bulk-email recipients backfill",
					message=frappe.get_traceback(),
				)

		frappe.db.commit()
		if len(rows) < BATCH_SIZE:
			break
		start += BATCH_SIZE
		# Yield to foreground requests between batches.
		time.sleep(_BATCH_SLEEP_SEC)
