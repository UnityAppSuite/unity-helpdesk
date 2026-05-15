"""On-demand performance benchmarks for the Unity Helpdesk ticket list.

Invocation:

    bench --site <site> execute helpdesk.api.unity_perf.run_filter_benchmark

Each query runs twice; the second (warm) timing is the headline.
EXPLAIN output for the two highest-traffic queries is included so a missing
or unused index shows up immediately.

Future work — known full-scan offenders we deliberately don't index here:
- `_assign LIKE '%user%'` (line in `_build_filters`). Cannot use a regular
  index because the pattern starts with `%`; needs FULLTEXT or a join-table
  refactor.
- `custom_search_message_body` LIKE. Same shape; covered by the search-body
  patch's denormalized field but still full-scan.
"""

import time

import frappe
from frappe import _

from helpdesk.api.unity_helpdesk import _require_unity_access


_BENCHMARK_QUERIES = (
	("total_count", "SELECT COUNT(*) FROM `tabHD Ticket`"),
	(
		"date_range_3w",
		"SELECT COUNT(*) FROM `tabHD Ticket` "
		"WHERE creation >= DATE_SUB(NOW(), INTERVAL 21 DAY)",
	),
	(
		"date_range_closed",
		"SELECT COUNT(*) FROM `tabHD Ticket` "
		"WHERE creation >= DATE_SUB(NOW(), INTERVAL 21 DAY) "
		"AND status = 'Closed'",
	),
	(
		"assign_like",
		"SELECT COUNT(*) FROM `tabHD Ticket` WHERE _assign LIKE '%administrator%'",
	),
	(
		"dashboard_cards_agg",
		"SELECT COUNT(*) AS total, "
		"SUM(CASE WHEN status='Replied' THEN 1 ELSE 0 END) AS replied, "
		"SUM(CASE WHEN status='Resolved' THEN 1 ELSE 0 END) AS resolved, "
		"SUM(CASE WHEN status='Closed' THEN 1 ELSE 0 END) AS closed, "
		"SUM(CASE WHEN custom_is_on_hold=1 THEN 1 ELSE 0 END) AS on_hold "
		"FROM `tabHD Ticket`",
	),
	(
		"list_page_100",
		"SELECT name, subject, status, priority, _assign, creation, modified "
		"FROM `tabHD Ticket` ORDER BY modified DESC LIMIT 100",
	),
)

_EXPLAIN_TARGETS = ("date_range_3w", "dashboard_cards_agg")


def _require_admin():
	capabilities = _require_unity_access()
	if not (capabilities.can_manage_unity_settings or capabilities.can_manage_agents):
		frappe.throw(_("Only admins can run the perf benchmark"), frappe.PermissionError)
	return capabilities


@frappe.whitelist()
def run_filter_benchmark():
	"""Time the major HD Ticket list operations and return a structured table.

	Returns ``{"timings": [...], "explains": {...}, "row_count": int}`` so the
	output is easy to compare across runs (e.g. before/after the
	`creation_unity_idx` index lands).
	"""
	_require_admin()
	timings = []
	for name, sql in _BENCHMARK_QUERIES:
		runs = []
		for _i in range(2):
			t0 = time.perf_counter()
			frappe.db.sql(sql)
			runs.append(round((time.perf_counter() - t0) * 1000, 1))
		timings.append({"name": name, "cold_ms": runs[0], "warm_ms": runs[1]})

	query_by_name = dict(_BENCHMARK_QUERIES)
	explains = {}
	for target in _EXPLAIN_TARGETS:
		try:
			explains[target] = frappe.db.sql(
				f"EXPLAIN {query_by_name[target]}", as_dict=True
			)
		except Exception as exc:
			# EXPLAIN isn't supported on every backend (e.g. SQLite in CI).
			explains[target] = [{"error": str(exc)}]

	row_count = frappe.db.count("HD Ticket")
	return {"timings": timings, "explains": explains, "row_count": row_count}
