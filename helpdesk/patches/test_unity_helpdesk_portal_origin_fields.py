# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt
"""Tests for the portal-origin Custom Fields patch and flag-setting on create."""

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.patches.unity_helpdesk_portal_origin_fields import execute as run_patch


CUSTOM_FIELD_NAMES = (
	("HD Ticket", "custom_via_unity_portal"),
	("HD Ticket", "custom_is_bulk_email"),
)


class TestUnityHelpdeskPortalOriginPatch(FrappeTestCase):
	def test_patch_idempotent_runs_twice(self):
		run_patch()
		run_patch()
		# Exactly one Custom Field per (doctype, fieldname)
		for dt, field in CUSTOM_FIELD_NAMES:
			count = frappe.db.count(
				"Custom Field", {"dt": dt, "fieldname": field}
			)
			self.assertEqual(count, 1, f"Custom Field for {dt}.{field} not idempotent (count={count})")

	def test_fields_have_correct_properties(self):
		run_patch()
		for dt, field in CUSTOM_FIELD_NAMES:
			cf = frappe.get_doc("Custom Field", {"dt": dt, "fieldname": field})
			self.assertEqual(cf.fieldtype, "Check")
			self.assertEqual(int(cf.hidden or 0), 1)
			self.assertEqual(int(cf.read_only or 0), 1)
			self.assertEqual(int(cf.no_copy or 0), 1)


class TestPortalOriginFlagOnCreate(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		run_patch()
		cls._tickets = []

	@classmethod
	def tearDownClass(cls):
		for name in cls._tickets:
			if frappe.db.exists("HD Ticket", name):
				frappe.delete_doc("HD Ticket", name, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	def test_flag_set_when_created_via_spa_endpoint(self):
		from helpdesk.api.unity_helpdesk_ext import create_ticket
		result = create_ticket(
			subject="SPA ticket smoke test",
			raised_by="spa-test@example.com",
			message="body text",
		)
		ticket = (result or {}).get("ticket") or {}
		ticket_name = ticket.get("name")
		self.assertIsNotNone(ticket_name)
		self._tickets.append(ticket_name)
		flag = frappe.db.get_value("HD Ticket", ticket_name, "custom_via_unity_portal")
		self.assertEqual(int(flag or 0), 1)

	def test_flag_not_set_on_direct_doc_insert(self):
		# A ticket inserted directly (e.g., from email-in / API) should not have the SPA flag
		doc = frappe.get_doc(
			{
				"doctype": "HD Ticket",
				"subject": "Direct insert smoke test",
				"raised_by": "direct@example.com",
				"description": "body",
			}
		).insert(ignore_permissions=True)
		self._tickets.append(doc.name)
		flag = frappe.db.get_value("HD Ticket", doc.name, "custom_via_unity_portal")
		self.assertEqual(int(flag or 0), 0)
