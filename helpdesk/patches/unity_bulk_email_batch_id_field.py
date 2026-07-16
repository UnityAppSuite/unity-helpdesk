"""
Create `custom_bulk_batch_id` on HD Ticket — a per-submission id stamped on every
ticket a single bulk-email send creates.

It makes helpdesk.api.unity_helpdesk_ext._bulk_send_email_job idempotent: before
creating a student's ticket the job checks for an existing ticket with the same
(custom_bulk_batch_id, raised_by) and skips it. Without this, any re-run of the
job — a worker deadlock-retry inside frappe's execute_job, a worker restart, or an
accidental resend — re-created every ticket, turning a 40-student send into ~159
tickets (see the fix_bulk_email branch / bulk-email UAT blowup).

Indexed (search_index) so the per-student existence check is a fast lookup.
Hidden + read_only + no_copy. Safe to re-run.
"""
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from helpdesk.patches._unity_patch import run_patch


def execute():
	run_patch("unity_bulk_email_batch_id_field", _run)


_INDEX_NAME = "custom_bulk_batch_id_unity_idx"


def _run():
	create_custom_fields(
		{
			"HD Ticket": [
				{
					"fieldname": "custom_bulk_batch_id",
					"fieldtype": "Data",
					"label": "Bulk Email Batch Id",
					"read_only": 1,
					"hidden": 1,
					"no_copy": 1,
					"print_hide": 1,
					"search_index": 1,
					"insert_after": "custom_bulk_email_recipients",
				},
			]
		},
		update=True,
	)
	# create_custom_fields adds the COLUMN but not the index; add it explicitly so the
	# per-student idempotency lookup (get_value on custom_bulk_batch_id + raised_by) is
	# a fast range scan instead of a full-table scan, without waiting on a full migrate
	# sync. add_index is a no-op when the index already exists.
	try:
		frappe.db.add_index("HD Ticket", ["custom_bulk_batch_id"], index_name=_INDEX_NAME)
	except Exception:
		# Some MariaDB versions raise on duplicate-index; log and continue (the index
		# is already there, or the next migrate sync will add it from search_index).
		frappe.log_error(
			title="unity_bulk_email_batch_id_field: add_index",
			message=frappe.get_traceback(),
		)
	frappe.clear_cache(doctype="HD Ticket")
