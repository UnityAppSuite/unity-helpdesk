"""Add an index on `tabHD Ticket`.`ticket_type`.

The Unity SPA filters and counts tickets by ticket_type (the "Ticket Type"
list filter, dashboard aggregates, and the HD Ticket Type delete check in
Desk). Without an index this is a full scan of the ~93k-row table — a
`COUNT(*) WHERE ticket_type = X` measured ~32 seconds on production-sized data.
A plain b-tree index turns it into a fast range scan.

Idempotent — `frappe.db.add_index` is a no-op when the named index already
exists, so re-running is safe. A new dedicated patch (rather than editing
unity_ticket_list_indexes) ensures it also runs on environments where that
older patch is already logged as applied (e.g. UAT).
"""

import frappe

from helpdesk.patches._unity_patch import run_patch


def execute():
	run_patch("unity_ticket_type_index", _run)


def _run():
	if not frappe.db.exists("DocType", "HD Ticket"):
		return
	if not frappe.db.has_column("HD Ticket", "ticket_type"):
		return
	frappe.db.add_index("HD Ticket", ["ticket_type"], index_name="ticket_type_unity_idx")
	frappe.db.commit()
