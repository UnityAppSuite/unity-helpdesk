"""
Create `custom_replied_to_ticket` on HD Ticket — a Link pointing back to the
outgoing ticket (typically a bulk-email audit ticket) that this ticket is a
reply to.

Set by helpdesk.helpdesk.hooks.reply_link.on_communication_after_insert when
an inbound email's In-Reply-To chain resolves to a Sent Communication on a
ticket marked `custom_is_bulk_email = 1`.

Read-only, hidden, no_copy. Safe to re-run.
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
						"fieldname": "custom_replied_to_ticket",
						"fieldtype": "Link",
						"options": "HD Ticket",
						"label": "Replied To Ticket",
						"read_only": 1,
						"hidden": 1,
						"no_copy": 1,
						"print_hide": 1,
						"insert_after": "custom_is_bulk_email",
					},
				]
			},
			update=True,
		)
		frappe.clear_cache(doctype="HD Ticket")
	finally:
		frappe.logger().info(
			f"[unity-patch] unity_reply_link_field took {time.monotonic() - start:.2f}s"
		)
