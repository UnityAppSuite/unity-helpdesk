"""Add a `custom_color` (Color) custom field to HD Ticket Type.

Lets admins pick a per-type colour in the SPA Settings page; the Unity
TicketDetailView renders that colour as a circular dot next to the ticket
type name in the Previous Tickets panel. Provides immediate visual
identification of ticket categories without forcing a full row tint.

Idempotent — `create_custom_fields` with `update=True` is a no-op when
the field already exists. The execute() body is invoked both from
patches.txt (existing sites) and from `ensure_unity_custom_fields`
(fresh installs).

Prints to stdout in the [unity-patch:NAME] format so deploy-time failures
surface inline in the migrate output rather than getting buried in the
Error Log doctype.
"""
import time
import traceback

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


_PATCH_NAME = "unity_ticket_type_color_field"


def _report(level, message):
	prefix = f"[unity-patch:{_PATCH_NAME}]"
	line = f"{prefix} {level}: {message}"
	print(line, flush=True)
	if level == "ERROR":
		frappe.logger().error(line)
	else:
		frappe.logger().info(line)


def execute():
	start = time.monotonic()
	try:
		if not frappe.db.exists("DocType", "HD Ticket Type"):
			_report("INFO", "HD Ticket Type doctype not present yet — nothing to do")
			return
		create_custom_fields(
			{
				"HD Ticket Type": [
					{
						"fieldname": "custom_color",
						"fieldtype": "Color",
						"label": "Color",
						"description": (
							"Used by the Unity SPA to render a colored dot next to "
							"this ticket type in Previous Tickets / list views."
						),
						"insert_after": "priority",
					},
				]
			},
			update=True,
		)
		frappe.clear_cache(doctype="HD Ticket Type")
		_report("INFO", "custom_color field ensured on HD Ticket Type")
	except Exception as exc:
		_report("ERROR", f"add_custom_field failed: {exc}")
		frappe.log_error(
			title=f"{_PATCH_NAME}: add_custom_field",
			message=traceback.format_exc(),
		)
		raise
	finally:
		_report("INFO", f"done in {time.monotonic() - start:.2f}s")
