"""
Create student identity search custom fields on HD Ticket.

These fields were previously owned by the edu_quality app's helpdesk_portal_switch branch.
Moving them here so the helpdesk app is self-contained and does not depend on edu_quality
for its custom field definitions.

Safe to re-run: create_custom_fields with update=True never drops columns or existing data.

Backfill of custom_search_student_names/refs/guardian_emails is deferred to a
long-queue background job so `bench migrate` doesn't block on the sweep.
Re-run manually with:
  bench --site <site> execute helpdesk.patches.unity_helpdesk_student_search_fields.run_student_search_backfill
"""
import time

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

# Batched sweep size + inter-batch sleep. Tighter than the message-search
# rebuild because populate_ticket_student_search_fields may hit the education
# app (network round-trip) per ticket, so we don't want a single batch to
# starve the RQ worker.
_BATCH = 50
_BATCH_SLEEP_SEC = 0.2

# Index on raised_by accelerates both the backfill UPDATE and the runtime
# guardian-family search path (full-table scan -> ~1ms). Cheap idempotent add.
_RAISED_BY_INDEX = "raised_by_unity_idx"


def execute():
	start = time.monotonic()
	try:
		_create_fields()
		_ensure_raised_by_index()
		frappe.clear_cache(doctype="HD Ticket")
		# Enqueue the heavy sweep. migrate stays sub-second; the worker drains
		# in the background. New tickets get populated inline by create_ticket
		# regardless of whether the worker has caught up yet.
		frappe.enqueue(
			"helpdesk.patches.unity_helpdesk_student_search_fields.run_student_search_backfill",
			queue="long",
			timeout=21600,
			is_async=True,
			job_id="unity_student_search_backfill",
			deduplicate=True,
			enqueue_after_commit=True,
		)
	finally:
		frappe.logger().info(
			f"[unity-patch] unity_helpdesk_student_search_fields took {time.monotonic() - start:.2f}s"
		)


def _create_fields():
	create_custom_fields(
		{
			"HD Ticket": [
				# --- Student identity search fields (Data / VARCHAR 255, B-tree indexed) ---
				{
					"fieldname": "custom_search_student_names",
					"fieldtype": "Data",
					"label": "Search: Student Names",
					"read_only": 1,
					"hidden": 1,
					"no_copy": 1,
					"print_hide": 1,
					"insert_after": "custom_previous_ticket_details",
				},
				{
					"fieldname": "custom_search_student_refs",
					"fieldtype": "Data",
					"label": "Search: Student Reference Numbers",
					"read_only": 1,
					"hidden": 1,
					"no_copy": 1,
					"print_hide": 1,
					"insert_after": "custom_search_student_names",
				},
				{
					"fieldname": "custom_search_guardian_emails",
					"fieldtype": "Data",
					"label": "Search: Guardian Emails",
					"read_only": 1,
					"hidden": 1,
					"no_copy": 1,
					"print_hide": 1,
					"insert_after": "custom_search_student_refs",
				},
				# --- Legacy fields: data exists in DB from edu_quality; keep definitions
				#     official so Frappe's meta knows about them. No longer populated by
				#     new code (Unity UI sidebar renders student context dynamically). ---
				{
					"fieldname": "custom_list_of_student",
					"fieldtype": "Long Text",
					"label": "List of Student",
					"read_only": 1,
					"hidden": 1,
					"no_copy": 1,
					"print_hide": 1,
					"insert_after": "description",
				},
				{
					"fieldname": "custom_all_fees_details_of_students",
					"fieldtype": "Long Text",
					"label": "All Fees Details of Students",
					"read_only": 1,
					"hidden": 1,
					"no_copy": 1,
					"print_hide": 1,
					"insert_after": "custom_list_of_student",
				},
				{
					"fieldname": "custom_payment_schedule",
					"fieldtype": "Long Text",
					"label": "Payment Schedule",
					"read_only": 1,
					"hidden": 1,
					"no_copy": 1,
					"print_hide": 1,
					"insert_after": "custom_all_fees_details_of_students",
				},
				{
					"fieldname": "custom_student_remark",
					"fieldtype": "Long Text",
					"label": "Student Remark",
					"read_only": 1,
					"hidden": 1,
					"no_copy": 1,
					"print_hide": 1,
					"insert_after": "custom_payment_schedule",
				},
				{
					"fieldname": "custom_previous_ticket_details",
					"fieldtype": "Long Text",
					"label": "Previous Ticket Details",
					"read_only": 1,
					"hidden": 1,
					"no_copy": 1,
					"print_hide": 1,
					"insert_after": "custom_student_remark",
				},
			]
		},
		update=True,
	)


def _ensure_raised_by_index():
	# add_index is a no-op if the index already exists. Safe to call on every migrate.
	try:
		frappe.db.add_index("HD Ticket", ["raised_by"], index_name=_RAISED_BY_INDEX)
	except Exception:
		# Some MariaDB versions raise on duplicate index even though the docs say it's a no-op.
		# Log and continue — the index either exists already or will be added next migrate.
		frappe.log_error(
			title="unity_helpdesk_student_search_fields: raised_by index",
			message=frappe.get_traceback(),
		)


def _backfill_is_complete():
	"""Short-circuit when there's nothing left to backfill. Cheap COUNT only.

	Completeness is tracked with a raw `IS NULL` count, NOT Frappe's "is / not
	set" (which also matches ''): a ticket that legitimately resolves to no
	students — or has no raised_by — is stored as the empty-string sentinel ""
	(non-NULL), so IS NULL distinguishes "never processed" from "processed, none"
	and the sweep terminates instead of staying forever-incomplete and
	re-enqueuing on every migrate. Mirrors unity_ticket_recipient_search_backfill.
	"""
	if not frappe.db.has_column("HD Ticket", "custom_search_student_names"):
		return True
	pending = frappe.db.sql(
		"SELECT COUNT(*) FROM `tabHD Ticket` WHERE `custom_search_student_names` IS NULL"
	)[0][0]
	return not pending


def run_student_search_backfill(limit=None):
	"""Background job: populate custom_search_student_* fields for tickets that
	don't have them yet. Idempotent — re-running on a fully-populated DB is a
	single COUNT(*) and then a return. ``limit`` (optional) caps the number of
	tickets processed this run — useful for a bounded smoke test or an off-peak
	chunk; omit to drain everything.

	Fetches in paginated batches (the old `page_length=0` query loaded ~90K rows
	on UAT). Each batch commits + sleeps briefly so foreground SPA requests stay
	responsive while the sweep is running.
	"""
	if _backfill_is_complete():
		frappe.logger().info(
			"[unity-patch] student-search-backfill: already complete, skipping"
		)
		return

	try:
		from helpdesk.api.unity_helpdesk import populate_ticket_student_search_fields
	except ImportError:
		return

	if not frappe.db.has_column("HD Ticket", "custom_search_student_names"):
		return

	remaining = int(limit) if limit else None
	prev_batch_names = None
	while remaining is None or remaining > 0:
		page = _BATCH if remaining is None else min(_BATCH, remaining)
		# Re-query the pending head each batch (rows never processed = IS NULL). A
		# processed ticket that resolves to no students — or has no raised_by — is
		# written "" (non-NULL) by the callee and drops out here, so the sweep
		# terminates. Raw IS NULL (not Frappe "is not set", which also matches "").
		batch = frappe.db.sql(
			"""SELECT name FROM `tabHD Ticket`
			   WHERE `custom_search_student_names` IS NULL
			   ORDER BY modified DESC LIMIT %s""",
			(page,),
			as_dict=True,
		)
		if not batch:
			break
		batch_names = [t.name for t in batch]
		# Safety net: if a full batch makes NO progress (every row still pending
		# after we processed it — e.g. a row that always raises), the re-query
		# returns the same head forever. Break instead of looping + flooding the
		# Error Log. One diagnostic log so the stall stays visible.
		if batch_names == prev_batch_names:
			frappe.log_error(
				f"student-search-backfill stalled on {len(batch_names)} unpopulated "
				f"ticket(s) (e.g. {batch_names[:5]}); aborting to avoid an infinite loop",
				"unity_helpdesk_student_search_fields backfill",
			)
			break
		prev_batch_names = batch_names
		for t in batch:
			try:
				populate_ticket_student_search_fields(t.name)
			except Exception:
				frappe.log_error(
					frappe.get_traceback(),
					"unity_helpdesk_student_search_fields backfill",
				)
		frappe.db.commit()
		if remaining is not None:
			remaining -= len(batch)
		if len(batch) < page:
			break
		time.sleep(_BATCH_SLEEP_SEC)
