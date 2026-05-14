# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt
"""Tests for the bulk auto-assign-ticket-type background job."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.api.unity_helpdesk import (
	_bulk_auto_assign_ticket_types,
	enqueue_auto_assign_ticket_types,
)
from helpdesk.helpdesk.doctype.hd_ticket.hd_ticket import _KEYWORD_CACHE_KEY


def _ensure_type(name, keywords=""):
	if frappe.db.exists("HD Ticket Type", name):
		doc = frappe.get_doc("HD Ticket Type", name)
		doc.keywords = keywords
		doc.save(ignore_permissions=True)
		return doc
	return frappe.get_doc(
		{"doctype": "HD Ticket Type", "name": name, "keywords": keywords}
	).insert(ignore_permissions=True)


class TestEnqueueAutoAssign(FrappeTestCase):
	def test_gated_with_only_for_system_manager(self):
		# frappe.only_for is short-circuited in test mode, so assert the call shape instead.
		with (
			patch("helpdesk.api.unity_helpdesk.frappe.only_for") as mock_only_for,
			patch("helpdesk.api.unity_helpdesk.frappe.enqueue"),
		):
			enqueue_auto_assign_ticket_types()
		mock_only_for.assert_called_once_with("System Manager")

	def test_passes_job_id_and_deduplicate_to_enqueue(self):
		with patch("helpdesk.api.unity_helpdesk.frappe.enqueue") as mock_enqueue:
			enqueue_auto_assign_ticket_types()
		mock_enqueue.assert_called_once()
		_, kwargs = mock_enqueue.call_args
		self.assertEqual(kwargs.get("job_id"), "auto_assign_ticket_types")
		self.assertTrue(kwargs.get("deduplicate"))


class TestBulkAutoAssignPagination(FrappeTestCase):
	"""Regression test for A4: matched rows leaving the filter set used to shift
	pagination and skip ~50 unmatched rows per batch."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_type("BulkTestFees", "payment,fees,refund")
		_ensure_type("BulkTestLibrary", "book,library")
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		# Clean up created tickets
		frappe.db.sql(
			"DELETE FROM `tabHD Ticket` WHERE subject LIKE 'BULKTEST-%'"
		)
		for name in ("BulkTestFees", "BulkTestLibrary"):
			if frappe.db.exists("HD Ticket Type", name):
				frappe.delete_doc("HD Ticket Type", name, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		frappe.cache().delete_value(_KEYWORD_CACHE_KEY)

	def test_all_unspecified_processed_no_pagination_skip(self):
		# Create 250 tickets with empty ticket_type — every one matches "payment"
		# in subject. If pagination skipped, some rows would stay unassigned.
		created = []
		try:
			for i in range(250):
				doc = frappe.get_doc(
					{
						"doctype": "HD Ticket",
						"subject": f"BULKTEST-{i:03d} payment overdue",
						"description": "auto-test row",
						"ticket_type": "",  # ← empty so the filter picks it up
					}
				).insert(ignore_permissions=True)
				# Defensive: clear ticket_type in DB in case set_ticket_type assigned it
				frappe.db.set_value("HD Ticket", doc.name, "ticket_type", "")
				created.append(doc.name)
			frappe.db.commit()

			_bulk_auto_assign_ticket_types()

			unassigned = frappe.db.count(
				"HD Ticket",
				filters=[["name", "in", created], ["ticket_type", "in", ["", "Unspecified"]]],
			)
			self.assertEqual(unassigned, 0, "bulk job left rows unassigned — pagination skipped them")

			# All 250 should land on BulkTestFees (longest keyword "payment" wins)
			assigned_to_fees = frappe.db.count(
				"HD Ticket",
				filters=[["name", "in", created], ["ticket_type", "=", "BulkTestFees"]],
			)
			self.assertEqual(assigned_to_fees, 250)
		finally:
			if created:
				frappe.db.sql(
					"DELETE FROM `tabHD Ticket` WHERE name IN %(names)s", {"names": tuple(created)}
				)
				frappe.db.commit()

	def test_returns_early_if_keyword_map_empty(self):
		# Temporarily clear keywords on test types
		for name in ("BulkTestFees", "BulkTestLibrary"):
			doc = frappe.get_doc("HD Ticket Type", name)
			doc.keywords = ""
			doc.save(ignore_permissions=True)
		# Also need to clear keywords on any other types — too invasive; instead,
		# patch the map to be empty.
		with patch(
			"helpdesk.helpdesk.doctype.hd_ticket.hd_ticket._get_ticket_type_keyword_map",
			return_value=[],
		):
			# Should not raise even with no tickets / no map
			_bulk_auto_assign_ticket_types()
