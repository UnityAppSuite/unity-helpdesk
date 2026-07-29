"""Add the secondary indexes the Unity Helpdesk ticket-list SORT feature needs.

The list can now be ordered by any field in `TICKET_SORT_FIELDS`
(helpdesk/api/unity_helpdesk.py). `creation`/`modified`/`owner`/`raised_by`/
`status` are already indexed by earlier patches, but the rest were not —
measured on a 67K-row `tabHD Ticket`:

    ORDER BY modified DESC LIMIT 20   ->  type: index, Using index   (~2 ms)
    ORDER BY subject  ASC  LIMIT 20   ->  type: ALL,   Using filesort (~155 ms)

...and that 155 ms is for `SELECT name` alone; the real list selects ~25
columns, so the filesort row width — and the cost — is several times larger.

NOT indexed here, deliberately:
  - `priority` / `status` for sorting. Both sort through `FIELD(col, ...)` so
    the ordering by *meaning* is not sargable; an index can't serve it and you'd
    pay the write cost for nothing. (`status` already has its own index for
    filtering, which is a different query shape.)
  - `_assign`, `custom_hold_reason`, `custom_primary_message_text` — excluded
    from the sort registry entirely, so nothing can order by them.

Re-runs safely: every index is checked before it's created, and verified after.

Why the verify step exists: `run_patch` swallows failures so a broken patch can
never abort `bench migrate` — but Frappe still records the patch as applied.
That combination already bit this app once. `helpdesk.patches.unity_ticket_type_index`
is in `tabPatch Log` (2026-06-09) while `ticket_type_unity_idx` does not exist on
the table, so sorting/filtering by Ticket Type has been full-scanning ever since,
invisibly. This patch re-attempts that index and reports loudly on any index that
is still missing after the attempt, so a silent failure is never silent twice.
"""

import frappe

from helpdesk.patches._unity_patch import run_patch


_PATCH_NAME = "unity_ticket_sort_indexes"
_TABLE = "tabHD Ticket"
_INDEXES = (
	# (fields, index_name)
	(["subject"], "subject_unity_idx"),
	# Re-attempt of unity_ticket_type_index — see the module docstring.
	(["ticket_type"], "ticket_type_unity_idx"),
	(["response_by"], "response_by_unity_idx"),
	(["resolution_by"], "resolution_by_unity_idx"),
	(["first_responded_on"], "first_responded_on_unity_idx"),
	(["resolution_date"], "resolution_date_unity_idx"),
)


def _report(level, message):
	line = f"[unity-patch:{_PATCH_NAME}] {level}: {message}"
	print(line, flush=True)
	if level == "ERROR":
		frappe.logger().error(line)
	else:
		frappe.logger().info(line)


def execute():
	run_patch(_PATCH_NAME, _run)


def _run():
	if not frappe.db.exists("DocType", "HD Ticket"):
		_report("INFO", "HD Ticket doctype not present yet — nothing to do")
		return

	added = existing = skipped = failed = 0
	for fields, index_name in _INDEXES:
		missing_cols = [f for f in fields if not frappe.db.has_column("HD Ticket", f)]
		if missing_cols:
			# e.g. a custom field that ensure_unity_custom_fields() hasn't created yet.
			_report("INFO", f"skipping {index_name} — missing column(s) {missing_cols}")
			skipped += 1
			continue
		if frappe.db.has_index(_TABLE, index_name):
			existing += 1
			continue
		try:
			frappe.db.add_index("HD Ticket", fields, index_name=index_name)
		except Exception as exc:
			# Keep going: one bad index must not cost the others. The verify pass
			# below is what actually decides success.
			_report("ERROR", f"add_index {index_name} on {fields} raised: {exc}")

		if frappe.db.has_index(_TABLE, index_name):
			added += 1
			_report("INFO", f"added {index_name} on {fields}")
		else:
			failed += 1
			_report("ERROR", f"{index_name} on {fields} STILL MISSING after add_index")

	frappe.db.commit()
	_report(
		"INFO" if not failed else "ERROR",
		f"added={added} already_present={existing} skipped={skipped} failed={failed}",
	)
