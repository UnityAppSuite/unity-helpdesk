"""Add a composite (raised_by, modified) index on tabHD Ticket.

The customer-portal / edu_quality ticket intake builds a "Previous Tickets" block on
every ticket CREATE via `get_all("HD Ticket", {"raised_by": ...})` ordered by modified
(edu_quality/.../overrides/hd_ticket.py get_pevious_tickets). Without an index that
covers BOTH the raised_by equality AND the modified sort, that query full-scans the whole
HD Ticket table (~96K rows on a large/restored DB) and filesorts — adding many seconds of
latency to ticket creation (observed: 23s → 0.02s after this index). This composite index
turns it into a single-row range scan with no filesort.

Complements the single-column `raised_by_unity_idx`: the composite additionally covers the
`ORDER BY modified` so no filesort is needed. Idempotent — frappe.db.add_index is a no-op
when the named index already exists.
"""
import time

import frappe

_INDEX_NAME = "raised_by_modified_unity_idx"


def execute():
	start = time.monotonic()
	try:
		if not frappe.db.exists("DocType", "HD Ticket"):
			return
		try:
			frappe.db.add_index("HD Ticket", ["raised_by", "modified"], index_name=_INDEX_NAME)
		except Exception:
			# Some MariaDB versions raise on duplicate-index even though the docs say
			# it's a no-op. Log and continue — the index is already there or the next
			# migrate adds it.
			frappe.log_error(
				title="unity_ticket_raised_by_modified_index: add_index",
				message=frappe.get_traceback(),
			)
		frappe.db.commit()
	finally:
		frappe.logger().info(
			f"[unity-patch] unity_ticket_raised_by_modified_index took {time.monotonic() - start:.2f}s"
		)
