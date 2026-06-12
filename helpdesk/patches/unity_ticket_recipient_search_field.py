"""Create the custom_search_recipient_emails search field on HD Ticket.

Stores the deduped external To/CC recipient emails of a ticket's communications
(the helpdesk's own inboxes stripped) so an email/phone search can also surface
tickets where a family member was a *recipient* — "send to this mail" — and not
only the sender (raised_by). Cases 2 & 3 of the Unity search spec.

Maintained inline by the search-index doc hooks (helpdesk/helpdesk/hooks/
search_index.py -> update_ticket_message_search_index, which now also writes this
field). Backfill of pre-existing tickets is a separate long-queue job, enqueued by
the companion patch unity_ticket_recipient_search_backfill.

Safe to re-run: create_custom_fields(update=True) never drops columns or data.
"""
import time

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	start = time.monotonic()
	try:
		if not frappe.db.exists("DocType", "HD Ticket"):
			return
		create_custom_fields(
			{
				"HD Ticket": [
					{
						"fieldname": "custom_search_recipient_emails",
						"fieldtype": "Small Text",
						"label": "Search: Recipient Emails",
						"read_only": 1,
						"hidden": 1,
						"no_copy": 1,
						"print_hide": 1,
						"insert_after": "custom_search_guardian_emails",
					}
				]
			},
			update=True,
		)
		frappe.clear_cache(doctype="HD Ticket")
	finally:
		frappe.logger().info(
			f"[unity-patch] unity_ticket_recipient_search_field took {time.monotonic() - start:.2f}s"
		)
