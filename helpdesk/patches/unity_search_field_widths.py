"""Widen the HD Ticket student-search columns so indexing can't overflow.

`_create_fields()` in unity_helpdesk_student_search_fields declared these as Frappe
`Data` with NO explicit `length`, so MariaDB created VARCHAR(140) — while the comment
there (and populate_ticket_student_search_fields) assumed 255. Any ticket whose
guardian-email list crossed 140 chars therefore raised MySQL 1406 "Data too long",
and because every search field is written in a SINGLE set_value, that one overflow
failed the WHOLE update: the ticket kept NULL search fields and became invisible to
search. On UAT that was ~97K of 100K tickets (a "gudia" search returned 7 of 216).

A guardian list is raised_by + every guardian of every sibling, which routinely runs
past 255 too — so guardian_emails becomes Small Text (TEXT), matching
custom_search_recipient_emails, which already works as text + FULLTEXT. names/refs get
an explicit 255.

The three columns sit inside the composite FULLTEXT index search_body_ft_idx, and
MariaDB won't retype a column underneath it, so the index is dropped, the columns
altered, and the index rebuilt. The Custom Field docs are updated too — otherwise the
next migrate would shrink the columns straight back to the Data default and re-break
indexing. Finally the backfill is re-enqueued to drain the backlog.

Idempotent: re-running on an already-widened site is two information_schema reads
and a return.
"""

import time
import traceback

import frappe

_PATCH_NAME = "unity_search_field_widths"
_INDEX_NAME = "search_body_ft_idx"

# Must match unity_ticket_search_fulltext._INDEXED_COLUMNS — we rebuild that index.
_FT_COLUMNS = (
	"custom_search_message_body",
	"subject",
	"custom_search_student_names",
	"custom_search_student_refs",
	"custom_search_guardian_emails",
)

# column -> (DDL type, Custom Field fieldtype, Custom Field length)
# NULL is preserved deliberately: the backfill's pending set is `IS NULL`, and the
# empty string is the "processed, nothing to index" sentinel.
_TARGETS = {
	"custom_search_guardian_emails": ("TEXT NULL", "Small Text", 0),
	"custom_search_student_names": ("VARCHAR(255) NULL", "Data", 255),
	"custom_search_student_refs": ("VARCHAR(255) NULL", "Data", 255),
}


def _report(level, message):
	line = f"[unity-patch:{_PATCH_NAME}] {level}: {message}"
	print(line, flush=True)
	if level == "ERROR":
		frappe.logger().error(line)
	else:
		frappe.logger().info(line)


def _column_info(column):
	rows = frappe.db.sql(
		"""SELECT DATA_TYPE, CHARACTER_MAXIMUM_LENGTH FROM information_schema.COLUMNS
		   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tabHD Ticket'
		     AND COLUMN_NAME = %s""",
		(column,),
		as_dict=True,
	)
	return rows[0] if rows else None


def _index_exists():
	rows = frappe.db.sql(
		"""SELECT COUNT(*) FROM information_schema.STATISTICS
		   WHERE table_schema = DATABASE() AND table_name = 'tabHD Ticket'
		     AND index_name = %s""",
		(_INDEX_NAME,),
	)
	return bool(rows and rows[0][0])


def _needs_widening():
	for column in _TARGETS:
		info = _column_info(column)
		if not info:
			continue
		data_type = (info.get("DATA_TYPE") or "").lower()
		length = int(info.get("CHARACTER_MAXIMUM_LENGTH") or 0)
		target_ddl = _TARGETS[column][0]
		if target_ddl.startswith("TEXT"):
			if data_type != "text":
				return True
		elif data_type == "varchar" and length < _TARGETS[column][2]:
			return True
	return False


def execute():
	start = time.monotonic()
	try:
		if not frappe.db.exists("DocType", "HD Ticket"):
			return

		missing = [c for c in _TARGETS if not frappe.db.has_column("HD Ticket", c)]
		if missing:
			_report("INFO", f"skipping — columns not present yet: {missing}")
			return

		if not _needs_widening():
			_report("INFO", "columns already at target widths — skipping")
			return

		# 1) Drop the composite FULLTEXT index — MariaDB won't retype its columns
		#    while it exists.
		had_index = _index_exists()
		if had_index:
			_report("INFO", f"dropping {_INDEX_NAME} ...")
			frappe.db.sql(f"ALTER TABLE `tabHD Ticket` DROP INDEX `{_INDEX_NAME}`")
			frappe.db.commit()

		# 2) Widen the columns.
		for column in _TARGETS:
			ddl = _TARGETS[column][0]
			_report("INFO", f"altering {column} -> {ddl} ...")
			frappe.db.sql(f"ALTER TABLE `tabHD Ticket` MODIFY `{column}` {ddl}")
		frappe.db.commit()

		# 3) Keep Frappe's meta in step with the DB. Without this the next migrate
		#    would "correct" these columns back to the Data default and re-break
		#    indexing — the exact bug this patch exists to fix.
		for column in _TARGETS:
			fieldtype = _TARGETS[column][1]
			length = _TARGETS[column][2]
			cf = frappe.db.get_value(
				"Custom Field", {"dt": "HD Ticket", "fieldname": column}, "name"
			)
			if cf:
				frappe.db.set_value(
					"Custom Field",
					cf,
					{"fieldtype": fieldtype, "length": length},
					update_modified=False,
				)
		frappe.db.commit()

		# 4) Rebuild the FULLTEXT index over the widened columns.
		if had_index:
			col_list = ", ".join(f"`{c}`" for c in _FT_COLUMNS)
			_report("INFO", f"rebuilding {_INDEX_NAME} ...")
			frappe.db.sql(
				f"ALTER TABLE `tabHD Ticket` ADD FULLTEXT INDEX `{_INDEX_NAME}` ({col_list})"
			)
			frappe.db.commit()

		frappe.clear_cache(doctype="HD Ticket")

		# 5) Any row written while the cap was 140 may have been silently truncated
		#    (the new writer truncates to the column's real width instead of raising).
		#    NULL the sentinel column so the sweep re-indexes them at full width.
		frappe.db.sql(
			"""UPDATE `tabHD Ticket` SET `custom_search_student_names` = NULL
			   WHERE `custom_search_student_names` IS NOT NULL
			     AND (CHAR_LENGTH(`custom_search_guardian_emails`) >= 140
			          OR CHAR_LENGTH(`custom_search_student_names`) >= 140
			          OR CHAR_LENGTH(`custom_search_student_refs`) >= 140)"""
		)
		frappe.db.commit()

		# 6) Drain the backlog. The dedupe job_id is only held while a job is
		#    queued/running, and the previous sweep aborted long ago, so this
		#    re-enqueues cleanly.
		frappe.enqueue(
			"helpdesk.patches.unity_helpdesk_student_search_fields.run_student_search_backfill",
			queue="long",
			timeout=21600,
			is_async=True,
			job_id="unity_student_search_backfill",
			deduplicate=True,
			enqueue_after_commit=True,
		)
		_report("INFO", "columns widened; student-search backfill re-enqueued")
	except Exception as exc:
		# Never abort migrate: search still works on whatever is already indexed.
		_report("ERROR", f"FAILED: {exc}")
		frappe.log_error(title=_PATCH_NAME, message=traceback.format_exc())
	finally:
		_report("INFO", f"done in {time.monotonic() - start:.2f}s")
