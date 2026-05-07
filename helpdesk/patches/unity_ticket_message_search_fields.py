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

	# Scan all tickets in modified-desc order with a stable cursor. We skip the
	# "is not set" filter to avoid the result set shifting under us as we update
	# rows. update_ticket_message_search_index is idempotent and cheap to re-run
	# on already-populated rows (it short-circuits when set_value writes the
	# same value), so a few re-runs across migrations are fine.
	updated = 0
	start = 0
	while updated < MAX_BACKFILL_TICKETS:
		page = min(BATCH_SIZE, MAX_BACKFILL_TICKETS - updated)
		rows = frappe.get_all(
			"HD Ticket",
			fields=["name", "custom_search_message_body"],
			order_by="modified desc",
			limit_start=start,
			page_length=page,
		)
		if not rows:
			break

		for row in rows:
			# Skip rows that already have a populated index — saves the index rebuild
			# cost on re-runs without hiding them from the cursor.
			if (row.get("custom_search_message_body") or "").strip():
				continue
			try:
				update_ticket_message_search_index(row.name)
			except Exception:
				frappe.log_error(
					title="Unity message search backfill",
					message=frappe.get_traceback(),
				)
			updated += 1

		frappe.db.commit()
		if len(rows) < page:
			break
		start += page
