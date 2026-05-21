"""Add `raised_by_unity_idx` on tabHD Ticket.raised_by.

Why this is its own patch (split out from unity_helpdesk_student_search_fields):

unity_helpdesk_student_search_fields shipped with PR #6 (de2fd7488) and is
already logged in tabPatch Log on UAT and unity.local. A patch that's already
logged does not re-run on subsequent migrates, so adding `frappe.db.add_index`
inside that patch would never reach those environments.

Splitting the index into its own patch — registered fresh in patches.txt —
guarantees it runs once on every existing environment, and also on any new
install (where it's harmless because the table is empty).

Speeds up:
- Runtime guardian-family search path (~695ms full-table-scan → ~1ms range
  scan on the b-tree).
- The student-search backfill UPDATE.

Idempotent: frappe.db.add_index is a no-op when the named index already
exists, so it's safe to keep across deploys.
"""
import time

import frappe

_INDEX_NAME = "raised_by_unity_idx"


def execute():
	start = time.monotonic()
	try:
		if not frappe.db.exists("DocType", "HD Ticket"):
			return
		try:
			frappe.db.add_index("HD Ticket", ["raised_by"], index_name=_INDEX_NAME)
		except Exception:
			# Some MariaDB versions raise on duplicate-index even though the docs
			# say it's a no-op. Log and continue — the index is already there or
			# the next migrate will add it.
			frappe.log_error(
				title="unity_raised_by_index: add_index",
				message=frappe.get_traceback(),
			)
		frappe.db.commit()
	finally:
		frappe.logger().info(
			f"[unity-patch] unity_raised_by_index took {time.monotonic() - start:.2f}s"
		)
