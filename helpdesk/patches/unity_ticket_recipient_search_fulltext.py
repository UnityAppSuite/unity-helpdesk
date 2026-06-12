"""Add a dedicated FULLTEXT index on HD Ticket.custom_search_recipient_emails.

The email/phone search path matches "sent to this mail" tickets (cases 2 & 3) by
the denormalised recipient/cc emails. A leading-wildcard ``%LIKE%`` over that
column is a full table scan (~3s on 93K rows); this single-column FULLTEXT index
turns it into an index lookup (see _recipient_candidates).

Kept SEPARATE from search_body_ft_idx (the 5-column content index) so we don't
rebuild the large body index — this column is tiny (<=2KB), so the index builds in
well under the body index's time. InnoDB online DDL: adding a FULLTEXT index does
not block reads on MariaDB 10.4+.

Idempotent: checks for the index by name before adding and treats a duplicate-key
error as success. Skips until the column exists (its field patch must run first).
Failure is logged, not raised — recipient search just falls back to no results.
"""

import time
import traceback

import frappe


_PATCH_NAME = "unity_ticket_recipient_search_fulltext"
_INDEX_NAME = "recipient_ft_idx"
_COLUMN = "custom_search_recipient_emails"


def _report(level, message):
	prefix = f"[unity-patch:{_PATCH_NAME}]"
	line = f"{prefix} {level}: {message}"
	print(line, flush=True)
	if level == "ERROR":
		frappe.logger().error(line)
	else:
		frappe.logger().info(line)


def _index_exists():
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

		if not frappe.db.has_column("HD Ticket", _COLUMN):
			_report(
				"INFO",
				f"skipping — column {_COLUMN} not present yet. "
				"unity_ticket_recipient_search_field must run first; re-run migrate.",
			)
			return

		if _index_exists():
			_report("INFO", f"index {_INDEX_NAME} already present — skipping")
			return

		_report("INFO", f"adding FULLTEXT index on (`{_COLUMN}`) ...")
		try:
			frappe.db.sql(
				f"ALTER TABLE `tabHD Ticket` ADD FULLTEXT INDEX `{_INDEX_NAME}` (`{_COLUMN}`)"
			)
			frappe.db.commit()
		except Exception as exc:
			msg = str(exc)
			if "1061" in msg or "Duplicate key name" in msg:
				_report("INFO", f"index {_INDEX_NAME} reported as duplicate — already present")
				return
			_report("ERROR", f"FULLTEXT index creation FAILED: {exc}")
			frappe.log_error(
				title=f"{_PATCH_NAME}: add_fulltext_index",
				message=traceback.format_exc(),
			)
			# Don't re-raise — recipient search degrades to no results, the rest works.
	finally:
		_report("INFO", f"done in {time.monotonic() - start:.2f}s")
