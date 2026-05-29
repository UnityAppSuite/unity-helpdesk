"""Add a MariaDB FULLTEXT index on the ticket's searchable text columns.

The existing search pipeline (`_search_candidate_ticket_names` in
helpdesk/api/unity_helpdesk.py) tokenises the query, drops <3-char words,
caps at 8 tokens, and runs an AND-of-OR LIKE search. That works for short
queries ("TA16", "Prajwal") but fails when the user pastes a whole
paragraph: the 8-token cap picks common English words ("writing", "the",
"about") that may or may not all be present in the indexed body — and if
any one is missing, the whole AND returns zero rows.

This patch adds the FULLTEXT index that the new `_fulltext_candidates`
helper uses as a relevance-ranked fallback when the legacy LIKE path
returns nothing. MATCH(...) AGAINST(... IN NATURAL LANGUAGE MODE)
automatically ignores stopwords, handles long queries gracefully, and
returns results scored by relevance.

The index covers exactly the five columns the search reads from:
- custom_search_message_body — primary mail body (Small Text)
- subject — the ticket subject (Data)
- custom_search_student_names — denormalised student names (Data)
- custom_search_student_refs — denormalised student reference numbers (Data)
- custom_search_guardian_emails — denormalised guardian emails (Data)

Idempotent: the patch checks for the index by name before adding it, and
catches duplicate-key errors as a safety net. Skips entirely if any of the
columns aren't present (e.g. fresh install before
ensure_unity_custom_fields ran).

Online DDL: InnoDB supports adding a FULLTEXT index without blocking
writes on MariaDB 10.4+. On a ~90K row table this should complete in
seconds. The elapsed time is printed to stdout in the standard
[unity-patch:NAME] format so the deployer can spot regressions.
"""

import time
import traceback

import frappe


_PATCH_NAME = "unity_ticket_search_fulltext"
_INDEX_NAME = "search_body_ft_idx"
_INDEXED_COLUMNS = (
	"custom_search_message_body",
	"subject",
	"custom_search_student_names",
	"custom_search_student_refs",
	"custom_search_guardian_emails",
)


def _report(level, message):
	prefix = f"[unity-patch:{_PATCH_NAME}]"
	line = f"{prefix} {level}: {message}"
	print(line, flush=True)
	if level == "ERROR":
		frappe.logger().error(line)
	else:
		frappe.logger().info(line)


def _index_exists():
	# information_schema.STATISTICS lists every index on the table. Filtering
	# by index_name is enough — uniqueness within the table is guaranteed.
	rows = frappe.db.sql(
		"""
		SELECT COUNT(*) FROM information_schema.STATISTICS
		WHERE table_schema = DATABASE()
		  AND table_name = 'tabHD Ticket'
		  AND index_name = %s
		""",
		(_INDEX_NAME,),
	)
	return bool(rows and rows[0][0])


def execute():
	start = time.monotonic()
	try:
		if not frappe.db.exists("DocType", "HD Ticket"):
			_report("INFO", "HD Ticket doctype not present yet — nothing to do")
			return

		missing = [c for c in _INDEXED_COLUMNS if not frappe.db.has_column("HD Ticket", c)]
		if missing:
			_report(
				"INFO",
				f"skipping — columns not yet present: {missing}. "
				"This usually means an earlier patch (unity_ticket_message_search_fields / "
				"unity_helpdesk_student_search_fields) hasn't run on this site. "
				"Re-run migrate once those land and this patch will pick them up.",
			)
			return

		if _index_exists():
			_report("INFO", f"index {_INDEX_NAME} already present — skipping")
			return

		col_list = ", ".join(f"`{c}`" for c in _INDEXED_COLUMNS)
		sql = (
			f"ALTER TABLE `tabHD Ticket` "
			f"ADD FULLTEXT INDEX `{_INDEX_NAME}` ({col_list})"
		)
		_report("INFO", f"adding FULLTEXT index on ({', '.join(_INDEXED_COLUMNS)}) ...")
		try:
			frappe.db.sql(sql)
			frappe.db.commit()
		except Exception as exc:
			msg = str(exc)
			# 1061 = duplicate key name. Treat as success (idempotent re-run
			# against a site that already has the index).
			if "1061" in msg or "Duplicate key name" in msg:
				_report("INFO", f"index {_INDEX_NAME} reported as duplicate — already present")
				return
			_report("ERROR", f"FULLTEXT index creation FAILED: {exc}")
			frappe.log_error(
				title=f"{_PATCH_NAME}: add_fulltext_index",
				message=traceback.format_exc(),
			)
			# Don't re-raise — search still works via the existing LIKE path,
			# just without the relevance-ranked fallback.
	finally:
		elapsed = time.monotonic() - start
		_report("INFO", f"done in {elapsed:.2f}s")
