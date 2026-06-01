"""Add `owner_unity_idx` on tabHD Ticket.owner.

Why: the HD Ticket permission filter for a non-agent (customer/parent portal)
is `contact = user OR raised_by = user OR owner = user` (see
hd_ticket.permission_query). `contact` and `raised_by` are already indexed
(contact_unity_idx, raised_by_unity_idx), but `owner` was not — and MariaDB can
only use an index_merge *union* across the OR when ALL branches are indexed.
Without it, the whole condition fell back to a full scan of the (90K+ row) table.

Measured on unity.local (93K tickets):
- Before: index_merge impossible → ~4s cold / ~160ms warm full scan.
- After:  `Using union(contact_unity_idx, raised_by_unity_idx, owner_unity_idx)`,
          ~3 rows examined, ~1ms warm.

Split into its own patch (not folded into unity_ticket_list_indexes /
unity_raised_by_index) because those are already logged in tabPatch Log on UAT
and unity.local, so edits to them never re-run. A fresh patch in patches.txt
runs once everywhere, and is harmless on new installs (empty table).

Idempotent: frappe.db.add_index is a no-op when the named index already exists.
"""
import time

import frappe

_INDEX_NAME = "owner_unity_idx"


def execute():
	start = time.monotonic()
	try:
		if not frappe.db.exists("DocType", "HD Ticket"):
			return
		try:
			frappe.db.add_index("HD Ticket", ["owner"], index_name=_INDEX_NAME)
		except Exception:
			# Some MariaDB versions raise on duplicate-index even though the docs
			# say it's a no-op. Log and continue — the index is already there or
			# the next migrate will add it.
			frappe.log_error(
				title="unity_owner_index: add_index",
				message=frappe.get_traceback(),
			)
		frappe.db.commit()
	finally:
		frappe.logger().info(
			f"[unity-patch] unity_owner_index took {time.monotonic() - start:.2f}s"
		)
