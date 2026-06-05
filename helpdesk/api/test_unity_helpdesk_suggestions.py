# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt
"""Tests for the lightweight as-you-type get_ticket_suggestions endpoint."""

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.api.unity_helpdesk import (
	SUGGESTION_LIMIT,
	SUGGESTION_MIN_QUERY,
	get_ticket_suggestions,
)


def _delete_if_exists(doctype, name):
	if frappe.db.exists(doctype, name):
		frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)


class TestSuggestionsBasics(FrappeTestCase):
	"""Behavioral tests against the public endpoint, run as Administrator."""

	def setUp(self):
		self._tickets = []

	def tearDown(self):
		for name in self._tickets:
			_delete_if_exists("HD Ticket", name)
		frappe.db.commit()

	def _make_ticket(self, subject, raised_by="suggestion-test@example.com", description=""):
		doc = frappe.get_doc(
			{
				"doctype": "HD Ticket",
				"subject": subject,
				"raised_by": raised_by,
				"description": description or subject,
			}
		).insert(ignore_permissions=True)
		self._tickets.append(doc.name)
		return doc

	def test_below_min_length_returns_empty(self):
		result = get_ticket_suggestions(search="a")
		self.assertEqual(result["data"], [])
		# Confirm we don't pretend to have done a search either
		self.assertEqual(result["query"], "a")

	def test_empty_query_returns_empty(self):
		self.assertEqual(get_ticket_suggestions(search="").get("data"), [])
		self.assertEqual(get_ticket_suggestions(search=None).get("data"), [])

	def test_basic_subject_match(self):
		self._make_ticket("Refund SUGG_UNIQUE_TOKEN required")
		self._make_ticket("Unrelated subject")
		result = get_ticket_suggestions(search="sugg_unique_token")
		names = [row["name"] for row in result["data"]]
		self.assertEqual(len(names), 1)
		self.assertIn("Refund SUGG_UNIQUE_TOKEN required", [row["subject"] for row in result["data"]])

	def test_returns_lightweight_fields_only(self):
		self._make_ticket("Subject for field check SUGG_FIELDS_TOKEN")
		result = get_ticket_suggestions(search="sugg_fields_token")
		self.assertTrue(result["data"])
		row = result["data"][0]
		# Required keys present
		for key in ("name", "subject", "raised_by", "status", "modified"):
			self.assertIn(key, row, f"suggestion row missing {key}")
		# Heavy decoration NOT present
		self.assertNotIn("_assign", row)
		self.assertNotIn("agreement_status", row)

	def test_respects_limit(self):
		for i in range(10):
			self._make_ticket(f"Bulk SUGG_BULK_TOKEN ticket {i}")
		result = get_ticket_suggestions(search="sugg_bulk_token", limit=3)
		self.assertLessEqual(len(result["data"]), 3)

	def test_limit_capped_at_suggestion_limit_constant(self):
		for i in range(SUGGESTION_LIMIT + 3):
			self._make_ticket(f"Cap SUGG_CAP_TOKEN ticket {i}")
		result = get_ticket_suggestions(search="sugg_cap_token", limit=999)
		self.assertLessEqual(len(result["data"]), SUGGESTION_LIMIT)

	def test_exact_id_ranks_first(self):
		# Create three tickets where one contains the other's ID in its subject.
		first = self._make_ticket("First ticket about suggestions")
		# Embed first.name in another ticket's subject so it scores via subject hit
		decoy = self._make_ticket(f"Reference to {first.name} in subject")
		# Querying for the ticket ID should land the exact match at top
		result = get_ticket_suggestions(search=first.name)
		self.assertTrue(result["data"])
		self.assertEqual(result["data"][0]["name"], first.name)

	def test_subject_prefix_outranks_body_match(self):
		# One ticket has the query as a subject prefix; another has it deep in description
		prefix = self._make_ticket(
			"SUGG_PREFIX_TOKEN starts the subject"
		)
		# Force the description (and thus the indexed body) to contain the token
		body_only = self._make_ticket(
			"Generic subject",
			description="long description with SUGG_PREFIX_TOKEN buried inside",
		)
		result = get_ticket_suggestions(search="sugg_prefix_token")
		names = [row["name"] for row in result["data"]]
		self.assertIn(prefix.name, names)
		self.assertLess(names.index(prefix.name), names.index(body_only.name) if body_only.name in names else len(names))


class TestSuggestionsTokenization(FrappeTestCase):
	"""Ensure suggestions reuse the same normalization as the main search path."""

	def test_min_query_constant_is_two(self):
		# Sanity guard: the SPA hardcodes 2; if this changes, the SPA must change too.
		self.assertEqual(SUGGESTION_MIN_QUERY, 2)

	def test_query_with_only_whitespace_below_threshold(self):
		self.assertEqual(get_ticket_suggestions(search="  ").get("data"), [])


class TestSuggestionsMultiTokenAcrossBody(FrappeTestCase):
	"""Regression for the per-token LIMIT bug: an older ticket whose body
	contains all tokens of a multi-word query (including common words like
	'the' and 'and') must still surface. The old per-token-then-intersect
	approach silently dropped older tickets because each token's 400-row
	window was sorted modified-desc."""

	def setUp(self):
		self._tickets = []

	def tearDown(self):
		for name in self._tickets:
			if frappe.db.exists("HD Ticket", name):
				frappe.delete_doc("HD Ticket", name, force=True, ignore_permissions=True)
		frappe.db.commit()

	def _make_ticket(self, subject, description):
		doc = frappe.get_doc(
			{
				"doctype": "HD Ticket",
				"subject": subject,
				"raised_by": "multi-token-test@example.com",
				"description": description,
			}
		).insert(ignore_permissions=True)
		self._tickets.append(doc.name)
		return doc

	def test_finds_ticket_by_long_phrase_from_body(self):
		# Long phrase that hits many common tokens: "the", "and", plus
		# distinctive ones like "regulations", "cancellation", "deadline".
		target = self._make_ticket(
			subject="Refund and admission cancellation",
			description=(
				"In the latest Rules and Regulations kindly refer to the "
				"Cancellation of Admission clause. Deadline was December 31. "
				"REGRESSION_BODY_UNIQUE_TOKEN_82554 in body."
			),
		)
		# A few decoy tickets so the query is not trivially small
		for i in range(5):
			self._make_ticket(
				subject=f"Decoy ticket {i}",
				description=f"Unrelated body content number {i} the and",
			)

		result = get_ticket_suggestions(
			search="latest Rules and Regulations Cancellation of Admission clause",
			view="all",
		)
		names = [row["name"] for row in result["data"]]
		self.assertIn(target.name, names, "multi-token body search dropped the target ticket")

	def test_finds_by_unique_body_token_with_common_filler(self):
		target = self._make_ticket(
			subject="Common-words subject",
			description=(
				"This message contains the special word REGRESSION_UNIQUE_BODY_TOKEN "
				"alongside many common words like the and is for that."
			),
		)
		# Decoys with the common words but NOT the unique token
		for i in range(5):
			self._make_ticket(
				subject=f"Common decoy {i}",
				description=f"the and is for that — generic noise {i}",
			)
		result = get_ticket_suggestions(
			search="the regression_unique_body_token and",
			view="all",
		)
		names = [row["name"] for row in result["data"]]
		self.assertIn(target.name, names)
