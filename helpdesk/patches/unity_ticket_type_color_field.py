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

		# Short-circuit when the column is already there. This makes the
		# patch genuinely idempotent even when its tabPatch Log entry has
		# been deleted to force a re-run.
		if frappe.db.has_column("HD Ticket Type", "custom_color"):
			_report("INFO", "custom_color column already present on tabHD Ticket Type — skipping")
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
		frappe.db.commit()

		# Belt-and-braces: create_custom_fields inserts the Custom Field
		# doc and relies on a deferred meta reload to materialise the
		# column. On sites where that reload didn't fire (e.g.
		# `bench migrate --skip-failing` swallowed the underlying error
		# and still logged the patch as done), the Custom Field record
		# exists but the column doesn't, so every list/save fails with
		# 'Unknown column'. Force-add the column directly if it's still
		# missing after the create_custom_fields call.
		if not frappe.db.has_column("HD Ticket Type", "custom_color"):
			_report(
				"INFO",
				"custom_color column missing after create_custom_fields; "
				"falling back to ALTER TABLE",
			)
			# Frappe Color fields are stored as VARCHAR(140). Match the
			# default so downstream Frappe code doesn't surprise itself.
			frappe.db.sql(
				"ALTER TABLE `tabHD Ticket Type` ADD COLUMN `custom_color` varchar(140) DEFAULT NULL"
			)
			frappe.db.commit()

		if frappe.db.has_column("HD Ticket Type", "custom_color"):
			_report("INFO", "custom_color field ensured on HD Ticket Type")
		else:
			# Both paths failed — surface loudly so the operator sees it
			# in migrate output (rather than the SPA quietly skipping
			# colour saves forever).
			_report(
				"ERROR",
				"custom_color column still missing after fallback ALTER TABLE — "
				"check tabPatch Log + tabCustom Field for inconsistent state",
			)
	except Exception as exc:
		_report("ERROR", f"add_custom_field failed: {exc}")
		frappe.log_error(
			title=f"{_PATCH_NAME}: add_custom_field",
			message=traceback.format_exc(),
		)
		raise
	finally:
		_report("INFO", f"done in {time.monotonic() - start:.2f}s")
