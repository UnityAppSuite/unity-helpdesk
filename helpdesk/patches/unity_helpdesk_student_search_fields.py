"""
Create student identity search custom fields on HD Ticket.

These fields were previously owned by the edu_quality app's helpdesk_portal_switch branch.
Moving them here so the helpdesk app is self-contained and does not depend on edu_quality
for its custom field definitions.

Safe to re-run: create_custom_fields with update=True never drops columns or existing data.

Also backfills custom_search_student_names/refs/guardian_emails for tickets that are
missing them (skips tickets that are already populated).
"""
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	_create_fields()
	frappe.clear_cache(doctype="HD Ticket")
	_backfill_student_search_fields()


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


def _backfill_student_search_fields():
	try:
		from helpdesk.api.unity_helpdesk import populate_ticket_student_search_fields
	except ImportError:
		return

	if not frappe.db.has_column("HD Ticket", "custom_search_student_names"):
		return

	tickets = frappe.get_all(
		"HD Ticket",
		filters=[["HD Ticket", "custom_search_student_names", "in", ["", None]]],
		fields=["name", "raised_by"],
		page_length=0,
	)

	if not tickets:
		return

	_BATCH = 100
	for i in range(0, len(tickets), _BATCH):
		batch = tickets[i : i + _BATCH]
		for t in batch:
			try:
				populate_ticket_student_search_fields(t.name)
			except Exception:
				frappe.log_error(
					frappe.get_traceback(),
					"unity_helpdesk_student_search_fields backfill",
				)
		frappe.db.commit()
