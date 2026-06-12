"""Add BTREE indexes on Guardian.email_address and Guardian.user.

The email/phone family-search path (helpdesk/api/unity_helpdesk.py
_expand_email_to_family_search_terms) resolves a query email to a Guardian by
`email_address` and by `user`. Neither column is indexed by the education app, so
each email search did two full scans of tabGuardian (~16.5K rows). Indexing both
turns those into instant equality lookups — directly speeding cases 2, 3 and 5.

add_index is a no-op when the index already exists, so this is safe to re-run.
A failure is logged, not raised — the search still works (just with the scan).
"""
import time

import frappe


def execute():
	start = time.monotonic()
	try:
		if not frappe.db.exists("DocType", "Guardian"):
			return
		for column, index_name in (
			("email_address", "guardian_email_unity_idx"),
			("user", "guardian_user_unity_idx"),
		):
			if not frappe.db.has_column("Guardian", column):
				continue
			try:
				frappe.db.add_index("Guardian", [column], index_name=index_name)
			except Exception:
				# Some MariaDB versions raise on duplicate index despite add_index
				# being documented as a no-op. Log and continue.
				frappe.log_error(
					title="unity_guardian_search_index", message=frappe.get_traceback()
				)
	finally:
		frappe.logger().info(
			f"[unity-patch] unity_guardian_search_index took {time.monotonic() - start:.2f}s"
		)
