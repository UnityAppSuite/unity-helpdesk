"""Add a `custom_color` (Color) custom field to HD Team.

Lets admins pick a per-team colour in the SPA Settings page; the Unity ticket
list then tints the ASSIGNED TO chip with the colour of the ticket's
`agent_group`. Because `HD Team.autoname` is `field:team_name`, the team's
`name` IS the string stored in `HD Ticket.agent_group`, so the SPA can key
straight into a {team -> colour} map with no join.

Deliberately mirrors unity_ticket_type_color_field, down to the fallback and
the reporting format — the two features are the same shape and should stay
easy to diff against each other.

Idempotent — `create_custom_fields` with `update=True` is a no-op when the
field already exists. The execute() body is invoked both from patches.txt
(existing sites) and from `ensure_unity_custom_fields` (fresh installs).

Prints to stdout in the [unity-patch:NAME] format so deploy-time failures
surface inline in the migrate output rather than getting buried in the Error
Log doctype.
"""
import time
import traceback

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


_PATCH_NAME = "unity_team_color_field"


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
		if not frappe.db.exists("DocType", "HD Team"):
			_report("INFO", "HD Team doctype not present yet — nothing to do")
			return

		# Short-circuit when the column is already there. This makes the patch
		# genuinely idempotent even when its tabPatch Log entry has been
		# deleted to force a re-run.
		if frappe.db.has_column("HD Team", "custom_color"):
			_report("INFO", "custom_color column already present on tabHD Team — skipping")
			return

		create_custom_fields(
			{
				"HD Team": [
					{
						"fieldname": "custom_color",
						"fieldtype": "Color",
						"label": "Color",
						"description": (
							"Used by the Unity SPA to tint the Assigned To chip for "
							"tickets whose Agent Group is this team."
						),
						"insert_after": "ignore_restrictions",
					},
				]
			},
			update=True,
		)
		frappe.clear_cache(doctype="HD Team")
		frappe.db.commit()

		# Belt-and-braces: create_custom_fields inserts the Custom Field doc and
		# relies on a deferred meta reload to materialise the column. On sites
		# where that reload didn't fire, the Custom Field record exists but the
		# column doesn't, so every list/save fails with 'Unknown column'.
		if not frappe.db.has_column("HD Team", "custom_color"):
			_report(
				"INFO",
				"custom_color column missing after create_custom_fields; "
				"falling back to ALTER TABLE",
			)
			# Frappe Color fields are stored as VARCHAR(140).
			frappe.db.sql(
				"ALTER TABLE `tabHD Team` ADD COLUMN `custom_color` varchar(140) DEFAULT NULL"
			)
			frappe.db.commit()

		if frappe.db.has_column("HD Team", "custom_color"):
			_report("INFO", "custom_color field ensured on HD Team")
		else:
			# Both paths failed — surface loudly so the operator sees it in
			# migrate output (rather than the SPA quietly hiding the colour
			# column forever via its feature-detect).
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
		# Logged above; do NOT re-raise — migrate must continue (deploy-safety).
	finally:
		_report("INFO", f"done in {time.monotonic() - start:.2f}s")
