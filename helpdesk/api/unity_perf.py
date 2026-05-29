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
	_has_field,
	_normalize_search_text,
	_require_unity_access,
	_search_tokens,
	_ticket_message_search_fields,
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
	def _clear_request_cache():
		# `frappe.local` is a werkzeug Local proxy — it doesn't expose
		# __dict__, but delattr is supported. Wrap in try/except because
		# the attribute may not exist on the first call.
		try:
			delattr(frappe.local, "_unity_request_cache")
		except AttributeError:
			pass

	def _time_call(label, fn, *args, **kwargs):
		# Cold: clear the per-request cache so the first call pays the full cost.
		_clear_request_cache()
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


_DIAGNOSTIC_BODY_PREVIEW = 800
_DIAGNOSTIC_TOKEN_CAP = 32


@frappe.whitelist()
def print_search_diagnostic(ticket_name, query):
	"""Explain why a specific search query does (or doesn't) match a specific
	ticket. Reports the ticket's indexed body, the normalized tokens of the
	query, and whether each token is present in each indexed field.

	Usage:
	    bench --site <site> execute helpdesk.api.unity_perf.print_search_diagnostic \\
	        --kwargs '{"ticket_name": "97895", "query": "I am writing to inquire..."}'

	The output mirrors the actual search pipeline:
	1. Query → _normalize_search_text → _search_tokens → 3-char filter → 8-token cap.
	2. Each indexed field on the ticket is fetched and normalized the same way.
	3. Token-vs-field presence matrix shows the exact reason the AND-of-OR
	   path would fail (any token missing from every field => no match).
	"""
	_require_admin()
	ticket_name = (ticket_name or "").strip()
	if not ticket_name:
		frappe.throw(_("ticket_name is required"))
	if not frappe.db.exists("HD Ticket", ticket_name):
		frappe.throw(_("Ticket {0} not found").format(ticket_name))

	raw_query = (query or "").strip()
	# Replicate _search_candidate_ticket_names' token pipeline.
	all_tokens = _search_tokens(raw_query)
	filtered_tokens = (
		[t for t in all_tokens if len(t) >= 3] if len(all_tokens) > 1 else list(all_tokens)
	)
	# Mirror MAX_SEARCH_TOKENS = 8 (the local constant inside
	# _search_candidate_ticket_names). Diagnostic shows what the search WOULD
	# do, so we cap the same way.
	used_tokens = filtered_tokens[:8]

	# Collect every indexed field that's actually present on this site.
	candidate_fields = ["name", "subject", "raised_by"]
	for fname in (
		"custom_search_student_names",
		"custom_search_student_refs",
		"custom_search_guardian_emails",
	):
		if _has_field("HD Ticket", fname):
			candidate_fields.append(fname)
	# Message search body fields (might or might not be populated for this row)
	for fname in _ticket_message_search_fields():
		if fname not in candidate_fields:
			candidate_fields.append(fname)

	ticket_row = frappe.db.get_value("HD Ticket", ticket_name, candidate_fields, as_dict=True) or {}

	# Build the token-vs-field matrix using the same normalization the indexer
	# does — case + whitespace folded, HTML stripped — so the diagnostic
	# reflects what the LIKE / FULLTEXT operators would actually see.
	field_text = {
		fname: _normalize_search_text(value)
		for fname, value in ticket_row.items()
	}

	per_token = []
	for token in used_tokens[:_DIAGNOSTIC_TOKEN_CAP]:
		hits = [fname for fname, text in field_text.items() if token in text]
		per_token.append({
			"token": token,
			"matched_fields": hits,
			"matched": bool(hits),
		})

	missing_tokens = [row["token"] for row in per_token if not row["matched"]]
	verdict = (
		"MATCH — every token present in at least one indexed field"
		if not missing_tokens
		else f"NO MATCH — token(s) absent from every field: {missing_tokens}"
	)

	# Short previews of the heaviest field so the operator can eyeball the
	# indexed content without spamming the terminal.
	body_text = field_text.get("custom_search_message_body") or ""
	preview = body_text[:_DIAGNOSTIC_BODY_PREVIEW]
	if len(body_text) > _DIAGNOSTIC_BODY_PREVIEW:
		preview += f"... [truncated, full length={len(body_text)}]"

	report = {
		"ticket_name": ticket_name,
		"query": raw_query,
		"tokens_raw": all_tokens,
		"tokens_after_3char_filter": filtered_tokens,
		"tokens_used_for_search": used_tokens,
		"tokens_dropped_by_8_cap": filtered_tokens[8:],
		"indexed_fields_present": list(field_text.keys()),
		"per_token_match": per_token,
		"missing_tokens": missing_tokens,
		"custom_search_message_body_length": len(body_text),
		"custom_search_message_body_preview": preview,
		"verdict": verdict,
	}
	frappe.logger().info(f"[unity-perf] search-diagnostic: {report}")
	return report


@frappe.whitelist()
def diagnose_guardian_lookup(emails):
	"""Walk through `get_student_guardian_emails`' lookup chain step by step,
	reporting the result of each step. Lets the operator see exactly why no
	guardians were resolved on a given site.

	Usage:
	    bench --site <site> execute helpdesk.api.unity_perf.diagnose_guardian_lookup \\
	        --kwargs '{"emails": ["sample.student@walnutedu.in"]}'
	"""
	_require_admin()
	# Accept JSON-string (via bench --kwargs) or list/tuple.
	if isinstance(emails, str):
		try:
			emails = frappe.parse_json(emails)
		except Exception:
			emails = [emails]
	if not isinstance(emails, (list, tuple)):
		emails = []
	normalized = sorted({(e or "").strip().lower() for e in emails if (e or "").strip()})

	report = {
		"input_emails": normalized,
		"steps": [],
	}

	def _step(label, **payload):
		entry = {"step": label, **payload}
		report["steps"].append(entry)
		frappe.logger().info(f"[unity-perf] guardian-diagnostic: {entry}")

	# Step 1: doctypes exist?
	for dt in ("Student", "Student Guardian", "Guardian"):
		_step(
			f"doctype_exists::{dt}",
			present=bool(frappe.db.exists("DocType", dt)),
		)

	# Step 2: required fields exist on each doctype?
	_step(
		"field::Student.student_email_id",
		present=_has_field("Student", "student_email_id") if frappe.db.exists("DocType", "Student") else False,
	)
	_step(
		"field::Guardian.email_address",
		present=_has_field("Guardian", "email_address") if frappe.db.exists("DocType", "Guardian") else False,
	)

	if not normalized:
		_step("input_empty", note="No emails passed; aborting further checks")
		return report

	# Step 3: Student lookup by student_email_id
	try:
		students = frappe.get_all(
			"Student",
			fields=["name", "student_email_id"],
			filters={"student_email_id": ["in", normalized]},
			page_length=200,
		)
	except Exception as exc:
		_step("student_lookup_error", error=str(exc))
		return report
	_step(
		"student_lookup",
		input_count=len(normalized),
		matched_count=len(students),
		matched=[{"name": s.name, "email": s.student_email_id} for s in students[:20]],
	)

	if not students:
		_step(
			"no_students_matched",
			note=(
				"None of the input emails are listed as `student_email_id` on a Student record. "
				"Confirm the Student records on this site actually have those emails populated."
			),
		)
		return report

	student_ids = [s.name for s in students]

	# Step 4: Student Guardian rows
	try:
		sg_rows = frappe.get_all(
			"Student Guardian",
			fields=["parent", "guardian", "email"],
			filters={"parenttype": "Student", "parent": ["in", student_ids]},
			page_length=1000,
		)
	except Exception as exc:
		_step("student_guardian_lookup_error", error=str(exc))
		return report
	_step(
		"student_guardian_lookup",
		matched_count=len(sg_rows),
		sample=[{"student": r.parent, "guardian": r.guardian, "email": r.email} for r in sg_rows[:20]],
	)

	if not sg_rows:
		_step(
			"no_student_guardian_rows",
			note=(
				"Students were matched but no Student Guardian child-table rows reference them. "
				"Verify the Student.guardians child table is populated."
			),
		)
		return report

	# Step 5: Guardian email_address resolution
	guardian_ids = sorted({r.guardian for r in sg_rows if r.guardian})
	try:
		guardian_rows = frappe.get_all(
			"Guardian",
			fields=["name", "email_address"],
			filters={"name": ["in", guardian_ids]},
			page_length=len(guardian_ids) + 1,
		)
	except Exception as exc:
		_step("guardian_lookup_error", error=str(exc))
		return report
	guardians_with_email = [g for g in guardian_rows if (g.email_address or "").strip()]
	_step(
		"guardian_lookup",
		total=len(guardian_rows),
		with_email=len(guardians_with_email),
		without_email=len(guardian_rows) - len(guardians_with_email),
		sample=[{"name": g.name, "email_address": g.email_address} for g in guardian_rows[:20]],
	)

	_step(
		"final",
		guardians_per_student=(
			len([r for r in sg_rows if (
				next((g for g in guardian_rows if g.name == r.guardian and (g.email_address or "").strip()), None)
				or (r.email or "").strip()
			)]) / max(len(students), 1)
		),
	)
	return report


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
