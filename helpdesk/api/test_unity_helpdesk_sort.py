# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt
"""Tests for Unity Helpdesk ticket-list sorting.

The list used to be hardcoded to `modified desc`. It now accepts a curated
`order_by` string. Two properties are worth locking down hard:

  1. Only registry fields ever reach SQL. `helpdesk/api/doc.py` (the upstream
     desk list) passes `order_by` straight through to frappe.get_list, so an
     unknown column surfaces as a raw OperationalError 1054 with SQL in the
     500. Unity refuses the request instead.
  2. Under search, an explicit sort may reorder results but must never change
     WHICH results come back — `_ranked_ticket_ids` both filters and orders,
     so sorting the wrong list would resurrect tickets the ranker dropped.

No DocType is created anywhere: `User.insert()` (and doc creation generally)
is pathologically slow on benches carrying the full Walnut app set.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.api.unity_helpdesk import (
	DEFAULT_TICKET_ORDER_BY,
	MAX_SORT_TERMS,
	TICKET_DOCTYPE,
	TICKET_SORT_FIELD_MAP,
	TICKET_SORT_FIELDS,
	_apply_sort_to_ranked_ids,
	_parse_sort_terms,
	_resolve_ticket_context,
	_ticket_order_by,
)


def _row(name, **values):
	return frappe._dict({"name": name, **values})


class TestSortParsing(FrappeTestCase):
	def test_empty_is_no_sort(self):
		for empty in (None, "", "   ", ","):
			with self.subTest(value=empty):
				self.assertEqual(_parse_sort_terms(empty), [])

	def test_bare_field_defaults_to_ascending(self):
		self.assertEqual(_parse_sort_terms("subject"), [("subject", "asc")])

	def test_multi_term_keeps_order(self):
		self.assertEqual(
			_parse_sort_terms("status asc, creation desc"),
			[("status", "asc"), ("creation", "desc")],
		)

	def test_duplicate_field_deduped(self):
		self.assertEqual(_parse_sort_terms("subject asc, subject desc"), [("subject", "asc")])

	def test_excluded_fields_rejected(self):
		# Virtual composites and text blobs are deliberately not sortable.
		for key in ("summary", "hold_summary", "_assign", "custom_hold_reason", "custom_primary_message_text"):
			with self.subTest(key=key), self.assertRaises(frappe.exceptions.ValidationError):
				_parse_sort_terms(f"{key} asc")

	def test_unknown_field_rejected(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			_parse_sort_terms("no_such_column desc")

	def test_injection_attempts_rejected(self):
		for payload in (
			"name; drop table `tabHD Ticket`",
			"(select 1)",
			"sleep(5)",
			"name asc desc",
			"subject asc; --",
			"1=1",
		):
			with self.subTest(payload=payload), self.assertRaises(frappe.exceptions.ValidationError):
				_parse_sort_terms(payload)

	def test_bad_direction_rejected(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			_parse_sort_terms("subject sideways")

	def test_too_many_terms_rejected(self):
		keys = [f["key"] for f in TICKET_SORT_FIELDS][: MAX_SORT_TERMS + 1]
		with self.assertRaises(frappe.exceptions.ValidationError):
			_parse_sort_terms(", ".join(f"{k} asc" for k in keys))


class TestOrderBySql(FrappeTestCase):
	def test_default_when_no_sort(self):
		sql = _ticket_order_by(None)
		self.assertTrue(sql.startswith(DEFAULT_TICKET_ORDER_BY))
		self.assertTrue(sql.endswith("`name` desc"))

	def test_always_ends_with_name_tiebreaker(self):
		# Without a deterministic total order, offset pagination over a tied sort
		# duplicates and drops rows between "Load more" pages.
		for expr in ("subject asc", "status desc", "priority asc, creation desc"):
			with self.subTest(expr=expr):
				self.assertRegex(_ticket_order_by(expr), r"`name` (asc|desc)$")

	def test_tiebreaker_direction_follows_last_term(self):
		# Not cosmetic: InnoDB secondary indexes carry the PK ascending, so a
		# matching direction is served by an index walk while a mismatched one
		# filesorts the whole table (measured 20 rows vs 37,631 on this site).
		self.assertTrue(_ticket_order_by("subject asc").endswith("`name` asc"))
		self.assertTrue(_ticket_order_by("subject desc").endswith("`name` desc"))
		self.assertTrue(_ticket_order_by("status asc, creation desc").endswith("`name` desc"))

	def test_virtual_age_columns_map_to_real_fields(self):
		# `creation_age` is a SPA-only relative-time column. Letting it through
		# verbatim raises OperationalError 1054 at query time.
		for virtual, real in (("creation_age", "creation"), ("modified_age", "modified")):
			with self.subTest(key=virtual):
				sql = _ticket_order_by(f"{virtual} asc")
				self.assertIn(f"`{real}` asc", sql)
				self.assertNotIn(virtual, sql)

	def test_rank_fields_sort_by_meaning(self):
		sql = _ticket_order_by("priority asc")
		self.assertIn("field(", sql)
		for value in ("Urgent", "High", "Medium", "Low"):
			self.assertIn(f"'{value}'", sql)

	def test_every_registry_entry_is_executable(self):
		"""The highest-value test: each key x direction must actually run.

		Catches an ORDER_GROUP_PATTERN rejection, a typo'd custom field, or a
		virtual key leaking into SQL — all of which are 500s in production and
		none of which any amount of string-assertion would find. Needs no
		fixtures; passes on an empty table.
		"""
		failures = []
		for spec in TICKET_SORT_FIELDS:
			for direction in ("asc", "desc"):
				expr = f"{spec['key']} {direction}"
				try:
					frappe.get_list(
						TICKET_DOCTYPE,
						fields=["name"],
						order_by=_ticket_order_by(expr),
						page_length=1,
					)
				except Exception as exc:
					failures.append(f"{expr}: {type(exc).__name__}: {exc}")
		self.assertEqual(failures, [], "order_by expressions rejected by the database")


class TestSortRegistry(FrappeTestCase):
	def test_every_field_exists_on_the_doctype(self):
		# db.has_column, not meta.has_field: `name`/`creation`/`modified`/`owner`
		# are real columns but not DocFields, so meta doesn't know them.
		for spec in TICKET_SORT_FIELDS:
			with self.subTest(key=spec["key"]):
				self.assertTrue(
					frappe.db.has_column(TICKET_DOCTYPE, spec["field"]),
					f"{spec['key']} maps to missing column {spec['field']}",
				)

	def test_rank_specs_are_well_formed(self):
		for spec in TICKET_SORT_FIELDS:
			if spec["type"] == "rank":
				with self.subTest(key=spec["key"]):
					self.assertTrue(spec.get("rank"))
					self.assertEqual(len(set(spec["rank"])), len(spec["rank"]))

	def test_priority_rank_covers_every_live_priority(self):
		# A priority present in the data but absent from the rank list sorts to
		# position 0 — silently first in asc. Catch that at test time.
		live = {p.name for p in frappe.get_all("HD Ticket Priority", fields=["name"])}
		if not live:
			self.skipTest("no HD Ticket Priority records on this site")
		self.assertFalse(live - set(TICKET_SORT_FIELD_MAP["priority"]["rank"]))

	def test_labels_agree_with_the_column_registry(self):
		from helpdesk.api.unity_helpdesk import AVAILABLE_TICKET_COLUMNS

		columns = {c["key"]: c["label"] for c in AVAILABLE_TICKET_COLUMNS}
		for spec in TICKET_SORT_FIELDS:
			if spec["key"] in columns:
				with self.subTest(key=spec["key"]):
					self.assertEqual(spec["label"], columns[spec["key"]])


class TestSearchBranchSorting(FrappeTestCase):
	"""_apply_sort_to_ranked_ids reorders relevance results without refiltering."""

	def setUp(self):
		self.rows = [
			_row("101", subject="banana", priority="Low"),
			_row("102", subject="apple", priority="Urgent"),
			_row("103", subject="cherry", priority="Medium"),
		]
		# Deliberately excludes 103 — the ranker judged it a non-match.
		self.ranked = ["101", "102"]

	def test_no_terms_leaves_relevance_order_untouched(self):
		self.assertEqual(_apply_sort_to_ranked_ids(self.ranked, self.rows, []), self.ranked)

	def test_sorting_never_changes_membership(self):
		"""The core correctness property — sorting must not resurrect 103."""
		out = _apply_sort_to_ranked_ids(self.ranked, self.rows, [("subject", "asc")])
		self.assertEqual(sorted(out), sorted(self.ranked))
		self.assertNotIn("103", out)

	def test_sort_overrides_relevance_order(self):
		self.assertEqual(
			_apply_sort_to_ranked_ids(self.ranked, self.rows, [("subject", "asc")]),
			["102", "101"],  # apple before banana
		)

	def test_rank_field_sorts_by_meaning(self):
		self.assertEqual(
			_apply_sort_to_ranked_ids(self.ranked, self.rows, [("priority", "asc")]),
			["102", "101"],  # Urgent before Low
		)

	def test_unknown_rank_value_sorts_first_ascending(self):
		# Mirrors MariaDB's FIELD() = 0 and JS indexOf() = -1, so the server, the
		# search branch and the SPA's optimistic reorder all agree.
		rows = [_row("201", priority="Low"), _row("202", priority="")]
		out = _apply_sort_to_ranked_ids(["201", "202"], rows, [("priority", "asc")])
		self.assertEqual(out[0], "202")

	def test_ties_broken_deterministically_by_name(self):
		rows = [_row("301", subject="same"), _row("302", subject="same"), _row("303", subject="same")]
		out = _apply_sort_to_ranked_ids(["301", "302", "303"], rows, [("subject", "asc")])
		self.assertEqual(out, sorted(out, key=int), "asc sort must tiebreak name ascending")


class TestContextCaching(FrappeTestCase):
	def test_sort_does_not_fork_the_request_cache(self):
		"""Sort must ride ON the context, not be part of its cache key.

		get_tickets_page sends order_by and get_tickets_summary doesn't. If sort
		joined the key their contexts would diverge, and the search path would
		pay for a second _fetch_candidate_rows — the slowest query in the app.
		"""
		a = _resolve_ticket_context("all", None, None, None, 20, 0, "subject asc")
		b = _resolve_ticket_context("all", None, None, None, 20, 0, "creation desc")
		c = _resolve_ticket_context("all", None, None, None, 20, 0, None)

		self.assertNotEqual(a["order_by_sql"], b["order_by_sql"])
		self.assertEqual(a["sort_terms"], [("subject", "asc")])
		self.assertEqual(c["sort_terms"], [])
		# Same underlying context object reused across all three.
		self.assertIs(a["list_filters"], b["list_filters"])
		self.assertIs(a["list_filters"], c["list_filters"])

	def test_invalid_sort_fails_before_any_query(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			_resolve_ticket_context("all", None, None, None, 20, 0, "summary asc")
