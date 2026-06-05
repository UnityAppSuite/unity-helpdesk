"""
Create `custom_bulk_email_recipients` on HD Ticket — a comma-separated list of
every email a bulk-email audit ticket was sent to (TO + CC + BCC + additional BCC).

Populated by helpdesk.api.unity_helpdesk_ext.bulk_send_email for new audit
tickets, and by the backfill patch unity_bulk_email_recipients_backfill for
existing audit tickets (parses the recipient list out of the description HTML).

Lets us surface the bulk email in each recipient's "Previous Tickets" history
without scanning HTML on every load — a single LIKE on this denormalised field
is enough.

Hidden + read_only + no_copy. Safe to re-run.
"""
import time

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	start = time.monotonic()
	try:
		create_custom_fields(
			{
				"HD Ticket": [
					{
						"fieldname": "custom_bulk_email_recipients",
						"fieldtype": "Long Text",
						"label": "Bulk Email Recipients",
						"read_only": 1,
						"hidden": 1,
						"no_copy": 1,
						"print_hide": 1,
						"insert_after": "custom_replied_to_ticket",
					},
				]
			},
			update=True,
		)
		frappe.clear_cache(doctype="HD Ticket")
	finally:
		frappe.logger().info(
			f"[unity-patch] unity_bulk_email_recipients_field took {time.monotonic() - start:.2f}s"
		)
