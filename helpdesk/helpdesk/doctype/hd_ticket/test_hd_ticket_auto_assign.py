# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt
"""Tests for keyword-driven auto-assignment of HD Ticket Type."""

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.consts import DEFAULT_TICKET_TYPE
from helpdesk.helpdesk.doctype.hd_ticket.hd_ticket import (
	_KEYWORD_CACHE_KEY,
	_get_ticket_type_keyword_map,
	_match_ticket_type_by_keywords,
)


def _ensure_type(name, keywords=""):
	if frappe.db.exists("HD Ticket Type", name):
		doc = frappe.get_doc("HD Ticket Type", name)
		doc.keywords = keywords
		doc.save(ignore_permissions=True)
		return doc
	return frappe.get_doc(
		{"doctype": "HD Ticket Type", "name": name, "keywords": keywords}
	).insert(ignore_permissions=True)


def _delete_type_if_exists(name):
	if frappe.db.exists("HD Ticket Type", name):
		frappe.delete_doc("HD Ticket Type", name, force=True, ignore_permissions=True)


class TestMatchTicketTypeByKeywords(FrappeTestCase):
	"""Pure-function tests on _match_ticket_type_by_keywords — no DB required."""

	def test_returns_none_for_empty_text(self):
		self.assertIsNone(_match_ticket_type_by_keywords("", [("Fees", ["pay"])]))

	def test_returns_none_for_empty_map(self):
		self.assertIsNone(_match_ticket_type_by_keywords("any text", []))

	def test_word_boundary_blocks_substring_match(self):
		# "pay" must NOT match "happy" or "display"
		self.assertIsNone(
			_match_ticket_type_by_keywords("Happy birthday display", [("Fees", ["pay"])])
		)

	def test_single_keyword_match(self):
		match = _match_ticket_type_by_keywords("late fees due", [("Fees", ["fees"])])
		self.assertEqual(match, ("Fees", "fees"))

	def test_case_insensitive(self):
		match = _match_ticket_type_by_keywords("PAYMENT pending", [("Fees", ["payment"])])
		self.assertEqual(match, ("Fees", "payment"))

	def test_multi_keyword_or_match(self):
		match = _match_ticket_type_by_keywords(
			"refund requested", [("Fees", ["pay", "refund", "due"])]
		)
		self.assertEqual(match, ("Fees", "refund"))

	def test_longest_keyword_wins_on_tie(self):
		# Both types match, but type B has the longer keyword "payment-plan"
		kw_map = [("AAA", ["pay"]), ("ZZZ", ["payment plan"])]
		match = _match_ticket_type_by_keywords("need payment plan now", kw_map)
		self.assertEqual(match[0], "ZZZ")

	def test_alpha_tiebreak_on_equal_length(self):
		kw_map = [("Beta", ["abc"]), ("Alpha", ["xyz"])]
		match = _match_ticket_type_by_keywords("abc xyz", kw_map)
		# Equal-length matches: type name asc → "Alpha" wins
		self.assertEqual(match[0], "Alpha")

	def test_skips_empty_keywords(self):
		match = _match_ticket_type_by_keywords("fees", [("Fees", ["", "fees", ""])])
		self.assertEqual(match[0], "Fees")


class TestHDTicketAutoAssign(FrappeTestCase):
	"""Integration tests: cache, on-save behavior, default fallback."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls._original_default = frappe.db.get_single_value(
			"HD Settings", "default_ticket_type"
		)
		# Seed test types
		_ensure_type("Fees", "fees,payment,refund")
		_ensure_type("Library", "book,library")
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		_delete_type_if_exists("Fees")
		_delete_type_if_exists("Library")
		frappe.db.set_single_value("HD Settings", "default_ticket_type", cls._original_default)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		frappe.cache().delete_value(_KEYWORD_CACHE_KEY)

	def test_keyword_map_excludes_types_without_keywords(self):
		# Default seeded types (e.g., "Question") have no keywords → not in map
		kw_map = _get_ticket_type_keyword_map()
		type_names = {t[0] for t in kw_map}
		self.assertIn("Fees", type_names)
		self.assertIn("Library", type_names)

	def test_cache_invalidates_on_type_update(self):
		_get_ticket_type_keyword_map()  # populate cache
		doc = frappe.get_doc("HD Ticket Type", "Fees")
		doc.keywords = "fees,payment,refund,invoice"
		doc.save(ignore_permissions=True)
		kw_map = _get_ticket_type_keyword_map()
		fees_kws = next(kws for name, kws in kw_map if name == "Fees")
		self.assertIn("invoice", fees_kws)

	def test_cache_invalidates_on_type_trash(self):
		_ensure_type("TempType", "tempkw")
		_get_ticket_type_keyword_map()
		_delete_type_if_exists("TempType")
		kw_map = _get_ticket_type_keyword_map()
		self.assertNotIn("TempType", {t[0] for t in kw_map})

	def test_existing_ticket_type_not_overridden(self):
		ticket = frappe.get_doc(
			{
				"doctype": "HD Ticket",
				"subject": "fees due immediately",
				"description": "...",
				"ticket_type": "Library",
			}
		).insert(ignore_permissions=True)
		try:
			self.assertEqual(ticket.ticket_type, "Library")
		finally:
			frappe.delete_doc("HD Ticket", ticket.name, force=True, ignore_permissions=True)

	def test_no_match_falls_back_to_settings_default(self):
		frappe.db.set_single_value("HD Settings", "default_ticket_type", "Question")
		frappe.cache().delete_value(_KEYWORD_CACHE_KEY)
		ticket = frappe.get_doc(
			{
				"doctype": "HD Ticket",
				"subject": "random unrelated subject",
				"description": "no keywords here",
			}
		).insert(ignore_permissions=True)
		try:
			self.assertEqual(ticket.ticket_type, "Question")
		finally:
			frappe.delete_doc("HD Ticket", ticket.name, force=True, ignore_permissions=True)

	def test_no_match_falls_back_to_constant_when_settings_empty(self):
		frappe.db.set_single_value("HD Settings", "default_ticket_type", "")
		frappe.cache().delete_value(_KEYWORD_CACHE_KEY)
		ticket = frappe.get_doc(
			{
				"doctype": "HD Ticket",
				"subject": "random unrelated subject",
				"description": "no keywords here",
			}
		).insert(ignore_permissions=True)
		try:
			self.assertEqual(ticket.ticket_type, DEFAULT_TICKET_TYPE)
		finally:
			frappe.delete_doc("HD Ticket", ticket.name, force=True, ignore_permissions=True)

	def test_keyword_match_assigns_type(self):
		ticket = frappe.get_doc(
			{
				"doctype": "HD Ticket",
				"subject": "payment overdue",
				"description": "please review",
			}
		).insert(ignore_permissions=True)
		try:
			self.assertEqual(ticket.ticket_type, "Fees")
		finally:
			frappe.delete_doc("HD Ticket", ticket.name, force=True, ignore_permissions=True)

	def test_match_in_description_when_subject_neutral(self):
		ticket = frappe.get_doc(
			{
				"doctype": "HD Ticket",
				"subject": "hello",
				"description": "I lost my library book",
			}
		).insert(ignore_permissions=True)
		try:
			self.assertEqual(ticket.ticket_type, "Library")
		finally:
			frappe.delete_doc("HD Ticket", ticket.name, force=True, ignore_permissions=True)
