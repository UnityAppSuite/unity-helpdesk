"""Add an index on `tabHD Ticket`.`creation` to speed up the Unity SPA's
date-range filter (and any list query ordered by recency).

Before this index, `WHERE creation >= X AND creation <= Y` falls back to a full
table scan — ~770 ms on a 92K-row table. With the index, MariaDB switches to a
range scan on the b-tree and the same query lands in the ~50-100 ms region.

Idempotent — `frappe.db.add_index` is a no-op when the named index already
exists, so it's safe to re-run.
"""

import time

import frappe


def execute():
	start = time.monotonic()
	try:
		if not frappe.db.exists("DocType", "HD Ticket"):
			return
		frappe.db.add_index("HD Ticket", ["creation"], index_name="creation_unity_idx")
		frappe.db.commit()
	finally:
		frappe.logger().info(
			f"[unity-patch] unity_ticket_creation_index took {time.monotonic() - start:.2f}s"
		)
