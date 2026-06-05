"""
Create custom fields on HD Ticket that record whether the ticket was created
from the Unity Helpdesk SPA, and whether it represents a bulk email send.

- custom_via_unity_portal: Check. Set to 1 by helpdesk.api.unity_helpdesk_ext.create_ticket
  when a ticket is created via the Unity Helpdesk "New Ticket" button. Lets the
  SPA tint these rows green to distinguish them from email-in tickets.

- custom_is_bulk_email: Check. Set to 1 by helpdesk.api.unity_helpdesk_ext.bulk_send_email
  on the single audit-trail ticket that records a BCC-style bulk email send.

Both are hidden + read_only + no_copy. Safe to re-run.
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
						"fieldname": "custom_via_unity_portal",
						"fieldtype": "Check",
						"label": "Via Unity Portal",
						"read_only": 1,
						"hidden": 1,
						"no_copy": 1,
						"print_hide": 1,
						"default": "0",
						"insert_after": "custom_search_guardian_emails",
					},
					{
						"fieldname": "custom_is_bulk_email",
						"fieldtype": "Check",
						"label": "Bulk Email Send",
						"read_only": 1,
						"hidden": 1,
						"no_copy": 1,
						"print_hide": 1,
						"default": "0",
						"insert_after": "custom_via_unity_portal",
					},
				]
			},
			update=True,
		)
		frappe.clear_cache(doctype="HD Ticket")
	finally:
		frappe.logger().info(
			f"[unity-patch] unity_helpdesk_portal_origin_fields took {time.monotonic() - start:.2f}s"
		)
