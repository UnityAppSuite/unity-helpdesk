"""
Schema patch — create the three Unity mail-body search fields on HD Ticket.

Important: this patch is SCHEMA-ONLY. The actual ticket-by-ticket backfill of
custom_search_message_body / custom_primary_message_html /
custom_primary_message_text is owned by unity_ticket_message_search_rebuild,
which enqueues a long-queue background job so `bench migrate` stays fast.

We deliberately removed the previous inline 5000-row backfill from this patch
so fresh installs (and any environment that doesn't yet have this patch logged
in tabPatch Log) finish migrate in under a second instead of ~30–60 seconds.

The rebuild job iterates in modified-desc order, so the most recent tickets
are indexed first — within ~30s of the worker starting, recent-ticket
mail-body search is ready. Search by subject / ticket ID / student fields
keeps working immediately because they use other indexes.

For environments where the patch is already logged (UAT post-PR-#6 and
unity.local), this refactor changes nothing — Frappe won't re-run a logged
patch. The benefit is for fresh installs going forward.
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
	finally:
		frappe.logger().info(
			f"[unity-patch] unity_ticket_message_search_fields took {time.monotonic() - start:.2f}s"
		)
