import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


MAX_BACKFILL_TICKETS = 5000
BATCH_SIZE = 200


def execute():
	create_custom_fields(
		{
			"HD Ticket": [
				{
					"fieldname": "custom_primary_message_html",
					"fieldtype": "Long Text",
					"label": "Primary Message HTML",
					"read_only": 1,
					"hidden": 1,
					"no_copy": 1,
					"print_hide": 1,
				},
				{
					"fieldname": "custom_primary_message_text",
					"fieldtype": "Small Text",
					"label": "Primary Message Text",
					"read_only": 1,
					"hidden": 1,
					"no_copy": 1,
					"print_hide": 1,
				},
				{
					"fieldname": "custom_search_message_body",
					"fieldtype": "Small Text",
					"label": "Message Search Body",
					"read_only": 1,
					"hidden": 1,
					"no_copy": 1,
					"print_hide": 1,
				},
			]
		},
		update=True,
		)
	frappe.clear_cache(doctype="HD Ticket")
	_backfill_existing_tickets()


def _backfill_existing_tickets():
	from helpdesk.api.unity_helpdesk import update_ticket_message_search_index

	updated = 0
	while updated < MAX_BACKFILL_TICKETS:
		rows = frappe.get_all(
			"HD Ticket",
			fields=["name"],
			filters=[
				["HD Ticket", "custom_search_message_body", "is", "not set"],
			],
			order_by="modified desc",
			limit_start=0,
			page_length=min(BATCH_SIZE, MAX_BACKFILL_TICKETS - updated),
		)
		if not rows:
			break

		for row in rows:
			update_ticket_message_search_index(row.name)
			updated += 1
