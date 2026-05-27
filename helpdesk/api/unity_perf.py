"""On-demand performance benchmarks for the Unity Helpdesk ticket list.

Invocation:

    bench --site <site> execute helpdesk.api.unity_perf.run_filter_benchmark
    bench --site <site> execute helpdesk.api.unity_perf.run_endpoint_benchmark

`run_filter_benchmark` times raw SQL — useful for spotting missing indexes.
`run_endpoint_benchmark` times the actual whitelisted API methods end-to-end
so the "what does the user feel" wall-clock matches the SPA's experience.

Each query runs twice; the second (warm) timing is the headline.
EXPLAIN output for the two highest-traffic queries is included so a missing
or unused index shows up immediately.

The historical full-scan offenders mentioned in the original docstring:
- `_assign LIKE '%user%'` — replaced by the ToDo-based lookup in
  `_apply_assignee_filter`, which uses the indexed `tabToDo` table instead.
- `custom_search_message_body` LIKE — still full-scan but only fires
  when the user actively searches; the empty-search default path no
  longer touches that column.
"""

import time

import frappe
from frappe import _

from helpdesk.api.unity_helpdesk import (
	_assigned_ticket_names,
	_require_unity_access,
	get_tickets_page,
	get_tickets_summary,
)


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


@frappe.whitelist()
def run_endpoint_benchmark(view="all", page_length=20):
	"""Time the actual SPA-facing endpoints end-to-end.

	Returns timings (cold + warm) for:
	- `get_tickets_page` and `get_tickets_summary` separately (the post-split
	  endpoints the SPA now fires in parallel)
	- `_assigned_ticket_names(session_user)` so the ToDo-resolved "My Tickets"
	  filter cost is visible
	- The legacy combined `get_tickets` wrapper, to confirm the back-compat
	  path didn't regress

	Useful before/after the helpdesk-optimizations branch deploys — confirms
	the SPA's wall-clock matches expectations and that the ToDo lookup is
	indeed sub-millisecond.
	"""
	_require_admin()
	from helpdesk.api.unity_helpdesk import get_tickets

	try:
		page_length_int = int(page_length or 20)
	except (TypeError, ValueError):
		page_length_int = 20

	# Two-call timing pattern — call() returns warm; call() again returns
	# cache-hit if any internal memoization fires. We deliberately reset the
	# per-request cache between cold/warm so we measure the underlying work
	# cost, not the request-scoped memoization (which doesn't survive across
	# real HTTP requests anyway).
	def _time_call(label, fn, *args, **kwargs):
		# Cold: clear the per-request cache so the first call pays the full cost.
		frappe.local.__dict__.pop("_unity_request_cache", None)
		t0 = time.perf_counter()
		try:
			fn(*args, **kwargs)
		except Exception as exc:
			return {"name": label, "error": str(exc), "cold_ms": None, "warm_ms": None}
		cold = round((time.perf_counter() - t0) * 1000, 1)
		# Warm: same call again with the per-request cache primed.
		t0 = time.perf_counter()
		try:
			fn(*args, **kwargs)
		except Exception:
			pass
		warm = round((time.perf_counter() - t0) * 1000, 1)
		return {"name": label, "cold_ms": cold, "warm_ms": warm}

	timings = [
		_time_call(
			"assigned_ticket_names_for_session_user",
			_assigned_ticket_names,
			frappe.session.user,
		),
		_time_call(
			f"get_tickets_page__{view}",
			get_tickets_page,
			view=view,
			page_length=page_length_int,
			start=0,
		),
		_time_call(
			f"get_tickets_summary__{view}",
			get_tickets_summary,
			view=view,
		),
		_time_call(
			f"get_tickets_combined__{view}__back_compat",
			get_tickets,
			view=view,
			page_length=page_length_int,
			start=0,
		),
	]
	return {
		"timings": timings,
		"row_count": frappe.db.count("HD Ticket"),
		"session_user": frappe.session.user,
		"view": view,
	}


_BACKFILL_JOBS = (
	# (job_id, label, completion_field, scope_filters)
	# scope_filters narrows the "total" count — bulk-email-recipients only
	# applies to audit tickets, so reporting % across all 92K rows would be
	# misleading.
	(
		"unity_message_search_rebuild",
		"message-search-rebuild",
		"custom_search_message_body",
		[],
	),
	(
		"unity_student_search_backfill",
		"student-search-backfill",
		"custom_search_student_names",
		[],
	),
	(
		"unity_bulk_email_recipients_backfill",
		"bulk-email-recipients-backfill",
		"custom_bulk_email_recipients",
		[["custom_is_bulk_email", "=", 1]],
	),
)


def _rq_job_state(job_id):
	"""Best-effort lookup of a single RQ job's state by job_id. Returns None if
	the job isn't in the registry (worker may have finished and evicted it)."""
	try:
		from rq.job import Job

		from frappe.utils.background_jobs import get_redis_conn

		conn = get_redis_conn()
		# Frappe namespaces jobs by site, so iterate keys and find the matching id.
		for key in conn.scan_iter(match=f"rq:job:*{job_id}*"):
			try:
				job = Job.fetch(key.decode().split(":")[-1], connection=conn)
				return {
					"id": job.id,
					"status": job.get_status(refresh=True),
					"enqueued_at": str(job.enqueued_at) if job.enqueued_at else None,
					"started_at": str(job.started_at) if job.started_at else None,
					"ended_at": str(job.ended_at) if job.ended_at else None,
				}
			except Exception:
				continue
	except Exception:
		return None
	return None


@frappe.whitelist()
def print_backfill_status():
	"""Report on the two long-queue background jobs that the Unity patches
	enqueue. Useful immediately after a deploy to verify migrate didn't
	silently fail to schedule them.

	Run with:
	  bench --site <site> execute helpdesk.api.unity_perf.print_backfill_status
	"""
	_require_admin()
	total = frappe.db.count("HD Ticket")
	report = {"total_tickets": total, "jobs": []}
	for job_id, label, completion_field, scope_filters in _BACKFILL_JOBS:
		# "is/not set" matches both NULL and empty-string — plain ["in", ["", None]]
		# misses NULL rows because column IN (NULL) never matches in SQL, and
		# newly-added custom columns default to NULL on existing rows.
		pending_filters = list(scope_filters) + [[completion_field, "is", "not set"]]
		scope_total = (
			frappe.db.count("HD Ticket", filters=scope_filters) if scope_filters else total
		)
		if scope_total == 0:
			pct = 100.0
			pending = 0
		else:
			pending = frappe.db.count("HD Ticket", filters=pending_filters)
			pct = round(100.0 * (scope_total - pending) / scope_total, 2)
		report["jobs"].append(
			{
				"label": label,
				"job_id": job_id,
				"completion_field": completion_field,
				"scope_total": scope_total,
				"populated_pct": pct,
				"pending_rows": pending,
				"rq_job": _rq_job_state(job_id),
			}
		)
	# Surface the report via the logger too so it shows up in bench logs.
	frappe.logger().info(f"[unity-perf] backfill status: {report}")
	return report
