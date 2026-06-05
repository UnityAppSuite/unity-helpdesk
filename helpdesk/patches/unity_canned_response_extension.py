"""Extend HD Canned Response with category, language, subject_template, and is_active
so the Unity Helpdesk template picker can group templates, filter by language,
optionally substitute an email subject, and disable templates without deleting them.

Idempotent — safe to re-run on every migrate.
"""
import time

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	start = time.monotonic()
	try:
		_execute()
	finally:
		frappe.logger().info(
			f"[unity-patch] unity_canned_response_extension took {time.monotonic() - start:.2f}s"
		)


def _execute():
	create_custom_fields(
		{
			"HD Saved Reply": [
				{
					"fieldname": "category",
					"fieldtype": "Link",
					"label": "Category",
					"options": "HD Canned Response Category",
					"reqd": 1,
					"in_list_view": 1,
					"in_standard_filter": 1,
					"insert_after": "title",
				},
				{
					"fieldname": "language",
					"fieldtype": "Select",
					"label": "Language",
					"options": "\nEnglish\nHindi\nMarathi",
					"default": "English",
					"in_list_view": 1,
					"in_standard_filter": 1,
					"insert_after": "category",
				},
				{
					"fieldname": "subject_template",
					"fieldtype": "Data",
					"label": "Subject Template",
					"description": "Optional Jinja template for the email subject. Used by surfaces with a subject input (create-ticket, bulk-email); ignored by the reply composer.",
					"insert_after": "language",
				},
				{
					"default": "1",
					"fieldname": "is_active",
					"fieldtype": "Check",
					"label": "Is Active",
					"in_list_view": 1,
					"in_standard_filter": 1,
					"insert_after": "subject_template",
				},
			]
		},
		update=True,
	)
	frappe.clear_cache(doctype="HD Saved Reply")
