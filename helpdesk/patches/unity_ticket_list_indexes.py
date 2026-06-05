"""Add the secondary indexes the Unity Helpdesk ticket-list relies on.

Without these, the list endpoint's default queries fall back to full table
scans on a 90K-row `tabHD Ticket`:

- `ORDER BY modified DESC LIMIT 20` — no usable index, MariaDB filesorts the
  whole table. ~600 ms cold.
- `WHERE status = 'Open' ORDER BY modified DESC` — same shape; composite
  `(status, modified)` lets the optimiser walk the index instead.
- `WHERE custom_is_on_hold = 1 ...` — used by the On-Hold KPI card +
  the On-Hold status filter; without an index, it's a full scan every
  refresh.
- `permission_query` (helpdesk/helpdesk/doctype/hd_ticket/hd_ticket.py:964)
  ORs over `contact`, `raised_by`, `owner`, `customer` for non-agents.
  `raised_by` is covered by `unity_raised_by_index`; `contact` is the
  one most often hit in practice for portal users whose contact differs
  from raised_by.

Each `frappe.db.add_index` call is idempotent — a no-op when the named
index already exists — so re-running this patch is safe.
"""

import time
import traceback

import frappe


_PATCH_NAME = "unity_ticket_list_indexes"
_INDEXES = (
	# (fields, index_name)
	(["modified"], "modified_unity_idx"),
	(["status", "modified"], "status_modified_unity_idx"),
	(["custom_is_on_hold", "modified"], "on_hold_modified_unity_idx"),
	(["contact"], "contact_unity_idx"),
)


def _report(level, message):
	"""Echo to both stdout (so `bench migrate` shows it inline) and Frappe's
	logger / Error Log (so the deployer can still find it after the migrate
	finishes). Without the stdout copy, errors hide inside the Error Log
	doctype and the deploy looks clean even when an index didn't get added.
	"""
	prefix = f"[unity-patch:{_PATCH_NAME}]"
	line = f"{prefix} {level}: {message}"
	print(line, flush=True)
	if level == "ERROR":
		frappe.logger().error(line)
	else:
		frappe.logger().info(line)


def execute():
	start = time.monotonic()
	added = 0
	skipped = 0
	failed = 0
	try:
		if not frappe.db.exists("DocType", "HD Ticket"):
			_report("INFO", "HD Ticket doctype not present yet — nothing to do")
			return
		for fields, index_name in _INDEXES:
			# Skip composite indexes whose columns don't yet exist on this site
			# (e.g. custom_is_on_hold lands via ensure_unity_custom_fields()).
			missing = [f for f in fields if not frappe.db.has_column("HD Ticket", f)]
			if missing:
				_report(
					"INFO",
					f"skipping {index_name} — missing column(s) {missing}",
				)
				skipped += 1
				continue
			try:
				frappe.db.add_index("HD Ticket", fields, index_name=index_name)
				added += 1
				_report("INFO", f"added {index_name} on {fields}")
			except Exception as exc:
				# Some MariaDB versions raise on duplicate-index even though the
				# docs promise a no-op. Surface the error so the deployer sees
				# it, but don't abort — the rest of the indexes (and migrate)
				# should still proceed.
				failed += 1
				_report(
					"ERROR",
					f"add_index {index_name} on {fields} FAILED: {exc}",
				)
				frappe.log_error(
					title=f"{_PATCH_NAME}: add_index {index_name}",
					message=traceback.format_exc(),
				)
		frappe.db.commit()
	except Exception as exc:
		# Defense in depth — if anything above this try/except escapes (e.g.
		# DocType.exists failed, frappe.db.commit failed), surface it before
		# Frappe's migrate runner wraps it in a generic "patch failed" message.
		_report("ERROR", f"unexpected failure: {exc}")
		frappe.log_error(
			title=f"{_PATCH_NAME}: unexpected failure",
			message=traceback.format_exc(),
		)
		raise
	finally:
		elapsed = time.monotonic() - start
		_report(
			"INFO",
			f"done in {elapsed:.2f}s — added={added} skipped={skipped} failed={failed}",
		)
