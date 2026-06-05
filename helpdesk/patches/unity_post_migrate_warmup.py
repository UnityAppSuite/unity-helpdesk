"""Touch the hot pages of `tabHD Ticket` + `tabToDo` so the very first user
request after `bench migrate` doesn't pay a cold InnoDB buffer-pool cost.

The earlier Unity backfills (message search, student search, bulk-email
recipients) issue large UPDATE sweeps that evict the ticket table from the
buffer pool. A migrate cycle that finishes "successfully" therefore leaves
the SPA's first page-load reading cold pages from disk — easily 5–10
seconds even after every other optimisation lands.

This patch runs LAST in `patches.txt` and reads the indexes the list-page
queries use, so they're resident in memory by the time the first user
request arrives. <1 second on a 100K-row table; idempotent and safe to
re-run.
"""

import time
import traceback

import frappe


_PATCH_NAME = "unity_post_migrate_warmup"
_WARMUP_QUERIES = (
	# (label, sql)
	("count", "SELECT COUNT(*) FROM `tabHD Ticket`"),
	(
		"modified_desc",
		"SELECT name FROM `tabHD Ticket` ORDER BY modified DESC LIMIT 500",
	),
	(
		"creation_desc",
		"SELECT name FROM `tabHD Ticket` ORDER BY creation DESC LIMIT 500",
	),
	(
		"open_todos",
		"SELECT reference_name FROM `tabToDo` "
		"WHERE reference_type = 'HD Ticket' AND status = 'Open' LIMIT 500",
	),
)


def _report(level, message):
	"""Echo to both stdout (so `bench migrate` shows it inline) and Frappe's
	logger / Error Log (so the deployer can still find it later)."""
	prefix = f"[unity-patch:{_PATCH_NAME}]"
	line = f"{prefix} {level}: {message}"
	print(line, flush=True)
	if level == "ERROR":
		frappe.logger().error(line)
	else:
		frappe.logger().info(line)


def execute():
	start = time.monotonic()
	ok = 0
	failed = 0
	try:
		if not frappe.db.exists("DocType", "HD Ticket"):
			_report("INFO", "HD Ticket doctype not present yet — nothing to warm up")
			return
		for label, sql in _WARMUP_QUERIES:
			try:
				frappe.db.sql(sql)
				ok += 1
			except Exception as exc:
				# Warmup is best-effort — never abort migrate on a stray
				# permissions / version issue with one of the queries. But
				# still surface so the deployer can investigate.
				failed += 1
				_report("ERROR", f"warmup query {label!r} FAILED: {exc}")
				frappe.log_error(
					title=f"{_PATCH_NAME}: {label}",
					message=traceback.format_exc(),
				)
	except Exception as exc:
		# Defense in depth — anything else (e.g. DocType.exists itself
		# blowing up) should be visible in the migrate output. Re-raise so
		# Frappe's runner records the patch as failed.
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
			f"done in {elapsed:.2f}s — ok={ok} failed={failed}",
		)
