# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt
"""Tests for Unity Helpdesk generic ticket filters (the "Filter" popover).

The list used to accept only six fixed keys. It now also accepts a curated
`conditions` list of {key, operator, value} rows. Three properties are worth
locking down hard:

  1. Only registry fields and registry operators ever reach SQL. Unity refuses
     anything else rather than dropping it — a dropped filter silently returns
     MORE rows than the user asked for, and since the same filter list also
     carries the view scoping, a drop can widen past what the user may see.
  2. Every field x operator pairing the picker can offer must actually execute.
     A typo'd column or an operator the dialect rejects is a 500 in production
     and no amount of string assertion would find it.
  3. The dashboard cards must agree with the list. They are a SEPARATE query
     (_apply_ticket_filters_to_query, hand-written pypika), so an operator it
     handles differently shows up as counts that contradict the rows below.

No DocType is created anywhere: doc creation is pathologically slow on benches
carrying the full Walnut app set, and none of this needs fixtures.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.api.unity_helpdesk import (
	FILTER_OPERATORS,
	MAX_FILTER_CONDITIONS,
	MAX_FILTER_IN_VALUES,
	TICKET_DOCTYPE,
	TICKET_FILTER_FIELD_MAP,
	TICKET_FILTER_FIELDS,
	UNSPECIFIED_AGENT_GROUP,
	_build_filters,
	_count,
	_dashboard_cards_for_filters,
	_filter_field_operators,
	_has_field,
	_localized_filter_fields,
	_parse_filter_conditions,
)


def _sample_value(spec, arity):
	"""A type-appropriate dummy so every pairing can actually be executed."""
	kind = spec["type"]
	if kind == "int":
		one = 1
	elif kind == "check":
		one = 1
	elif kind in ("date", "datetime"):
		one = "2026-01-01"
	elif spec.get("options"):
		one = spec["options"][0]
	else:
		one = "x"
	if arity == "many":
		return [one]
	if arity == "two":
		return ["2026-01-01", "2026-01-02"]
	return one


def _condition(key, operator, value=None):
	row = {"key": key, "operator": operator}
	if value is not None:
		row["value"] = value
	return row


def _live_fields():
	"""Registry entries whose column exists on THIS site (custom_* may not)."""
	return [
		spec
		for spec in TICKET_FILTER_FIELDS
		if not spec["field"].startswith("custom_") or _has_field(TICKET_DOCTYPE, spec["field"])
	]


class TestFilterParsing(FrappeTestCase):
	def test_empty_is_no_conditions(self):
		for empty in (None, "", [], "[]"):
			with self.subTest(value=empty):
				self.assertEqual(_parse_filter_conditions(empty), [])

	def test_builds_a_doctype_qualified_tuple(self):
		self.assertEqual(
			_parse_filter_conditions([_condition("status", "equals", "Open")]),
			[[TICKET_DOCTYPE, "status", "=", "Open"]],
		)

	def test_json_string_payload_accepted(self):
		# Form-encoded / back-compat callers send the filters dict as a string.
		built = _parse_filter_conditions('[{"key":"status","operator":"equals","value":"Open"}]')
		self.assertEqual(built, [[TICKET_DOCTYPE, "status", "=", "Open"]])

	def test_unknown_field_rejected(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			_parse_filter_conditions([_condition("no_such_column", "equals", "x")])

	def test_excluded_fields_rejected(self):
		# _assign is a JSON blob, the message/search bodies are huge text — all
		# deliberately absent from the registry.
		for key in ("_assign", "custom_primary_message_text", "custom_search_message_body", "summary"):
			with self.subTest(key=key), self.assertRaises(frappe.exceptions.ValidationError):
				_parse_filter_conditions([_condition(key, "equals", "x")])

	def test_unknown_operator_rejected(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			_parse_filter_conditions([_condition("subject", "regexp", "x")])

	def test_operator_not_allowed_for_that_type_rejected(self):
		# "between" is a date operator; offering it on Subject would build SQL
		# that runs but means nothing.
		with self.assertRaises(frappe.exceptions.ValidationError):
			_parse_filter_conditions([_condition("subject", "between", ["a", "b"])])
		# ...and Status is a Select, so ">" is meaningless on it.
		with self.assertRaises(frappe.exceptions.ValidationError):
			_parse_filter_conditions([_condition("status", ">", "Open")])

	def test_injection_attempts_rejected(self):
		for payload in (
			"name; drop table `tabHD Ticket`",
			"(select 1)",
			"sleep(5)",
			"1=1",
			"subject`, `name",
		):
			with self.subTest(payload=payload), self.assertRaises(frappe.exceptions.ValidationError):
				_parse_filter_conditions([_condition(payload, "equals", "x")])

	def test_injection_in_operator_rejected(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			_parse_filter_conditions([_condition("subject", "= 1 or 1=1 --", "x")])

	def test_too_many_conditions_rejected(self):
		rows = [
			_condition(spec["key"], _filter_field_operators(spec)[0], _sample_value(spec, "one"))
			for spec in _live_fields()[: MAX_FILTER_CONDITIONS + 1]
		]
		if len(rows) <= MAX_FILTER_CONDITIONS:
			self.skipTest("registry too small to exceed the cap")
		with self.assertRaises(frappe.exceptions.ValidationError):
			_parse_filter_conditions(rows)

	def test_too_many_in_values_rejected(self):
		values = [f"v{i}" for i in range(MAX_FILTER_IN_VALUES + 1)]
		with self.assertRaises(frappe.exceptions.ValidationError):
			_parse_filter_conditions([_condition("ticket_type", "in", values)])

	def test_missing_value_rejected(self):
		for row in (
			_condition("subject", "like", ""),
			_condition("ticket_type", "in", []),
			_condition("creation", "between", ["2026-01-01"]),
		):
			with self.subTest(row=row), self.assertRaises(frappe.exceptions.ValidationError):
				_parse_filter_conditions([row])

	def test_is_set_needs_no_value(self):
		self.assertEqual(
			_parse_filter_conditions([_condition("agent_group", "is set")]),
			[[TICKET_DOCTYPE, "agent_group", "is", "set"]],
		)
		self.assertEqual(
			_parse_filter_conditions([_condition("agent_group", "is not set")]),
			[[TICKET_DOCTYPE, "agent_group", "is", "not set"]],
		)

	def test_like_wraps_the_value(self):
		built = _parse_filter_conditions([_condition("subject", "like", "fee")])
		self.assertEqual(built[0][3], "%fee%")

	def test_like_does_not_double_wrap(self):
		# A user typing their own wildcard must keep control of it.
		built = _parse_filter_conditions([_condition("subject", "like", "fee%")])
		self.assertEqual(built[0][3], "fee%")

	def test_datetime_upper_bound_covers_the_whole_day(self):
		# The classic off-by-a-day: `creation <= '2026-01-05'` means midnight,
		# so it silently drops everything raised during the 5th.
		built = _parse_filter_conditions([_condition("creation", "<=", "2026-01-05")])
		self.assertEqual(built[0][3], "2026-01-05 23:59:59")

	def test_datetime_lower_bound_is_untouched(self):
		built = _parse_filter_conditions([_condition("creation", ">=", "2026-01-05")])
		self.assertEqual(built[0][3], "2026-01-05")

	def test_between_stretches_only_the_end(self):
		built = _parse_filter_conditions(
			[_condition("creation", "between", ["2026-01-01", "2026-01-05"])]
		)
		self.assertEqual(built[0][3], ["2026-01-01", "2026-01-05 23:59:59"])

	def test_datetime_has_no_equals_operator(self):
		# A bare YYYY-MM-DD equals midnight exactly and matches nothing, so the
		# registry must not offer it at all.
		self.assertNotIn("equals", _filter_field_operators(TICKET_FILTER_FIELD_MAP["creation"]))

	def test_int_value_coerced(self):
		built = _parse_filter_conditions([_condition("name", "equals", "123")])
		self.assertEqual(built[0][3], 123)

	def test_non_numeric_int_rejected(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			_parse_filter_conditions([_condition("name", "equals", "abc")])

	def test_check_value_coerced_to_int(self):
		self.assertEqual(
			_parse_filter_conditions([_condition("custom_is_on_hold", "equals", "Yes")])[0][3], 1
		)
		self.assertEqual(
			_parse_filter_conditions([_condition("custom_is_on_hold", "equals", "0")])[0][3], 0
		)


class TestFilterRegistry(FrappeTestCase):
	def test_every_field_exists_on_the_doctype(self):
		for spec in _live_fields():
			with self.subTest(key=spec["key"]):
				self.assertTrue(
					frappe.db.has_column(TICKET_DOCTYPE, spec["field"]),
					f"{spec['key']} maps to missing column {spec['field']}",
				)

	def test_every_field_offers_at_least_one_operator(self):
		for spec in TICKET_FILTER_FIELDS:
			with self.subTest(key=spec["key"]):
				self.assertTrue(_filter_field_operators(spec))

	def test_every_offered_operator_is_known(self):
		for spec in TICKET_FILTER_FIELDS:
			for op in _filter_field_operators(spec):
				with self.subTest(key=spec["key"], op=op):
					self.assertIn(op, FILTER_OPERATORS)

	def test_shipped_registry_carries_what_the_spa_needs(self):
		# The SPA picks a value control from `type` and populates the operator
		# dropdown from `operators`; a missing key means an unusable row.
		for spec in _localized_filter_fields():
			with self.subTest(key=spec["key"]):
				self.assertTrue(spec.get("label"))
				self.assertIn(spec.get("type"), ("text", "int", "select", "link", "check", "date", "datetime"))
				self.assertTrue(spec.get("operators"))

	def test_get_profile_ships_a_non_empty_registry(self):
		"""The SPA disables "+ Add filter" when the registry is empty.

		That failure is invisible — the popover still opens, it just can't be
		used — so assert the endpoint actually carries the keys the client reads
		(`filterable_fields`, `max_filter_conditions`), not merely that the
		registry constant is non-empty.
		"""
		from helpdesk.api.unity_helpdesk import get_profile

		profile = get_profile()
		self.assertTrue(profile.get("filterable_fields"), "get_profile shipped no filterable_fields")
		self.assertTrue(profile.get("max_filter_conditions"))

	def test_shipped_registry_omits_absent_custom_fields(self):
		# Offering a filter the parser would skip is worse than not offering it.
		shipped = {s["key"] for s in _localized_filter_fields()}
		for spec in TICKET_FILTER_FIELDS:
			if spec["field"].startswith("custom_") and not _has_field(TICKET_DOCTYPE, spec["field"]):
				with self.subTest(key=spec["key"]):
					self.assertNotIn(spec["key"], shipped)

	def test_labels_agree_with_the_column_registry(self):
		from helpdesk.api.unity_helpdesk import AVAILABLE_TICKET_COLUMNS

		columns = {c["key"]: c["label"] for c in AVAILABLE_TICKET_COLUMNS}
		for spec in TICKET_FILTER_FIELDS:
			if spec["key"] in columns:
				with self.subTest(key=spec["key"]):
					self.assertEqual(spec["label"], columns[spec["key"]])


class TestFilterExecution(FrappeTestCase):
	"""The highest-value tests: the SQL has to actually run, and the two query
	paths have to agree. Both pass on an empty table."""

	def test_every_field_and_operator_executes(self):
		failures = []
		for spec in _live_fields():
			for op_key in _filter_field_operators(spec):
				arity = FILTER_OPERATORS[op_key]["arity"]
				value = None if arity == "none" else _sample_value(spec, arity)
				try:
					built = _parse_filter_conditions([_condition(spec["key"], op_key, value)])
					frappe.get_list(TICKET_DOCTYPE, fields=["name"], filters=built, page_length=1)
				except Exception as exc:
					failures.append(f"{spec['key']} {op_key}: {type(exc).__name__}: {exc}")
		self.assertEqual(failures, [], "filter conditions rejected by the database")

	def test_cards_agree_with_the_list_for_every_operator(self):
		"""_dashboard_cards_for_filters is a different query from the row fetch.

		If _apply_ticket_filters_to_query renders an operator differently — or,
		as it used to, silently degrades an unknown one to equality — the KPI
		cards contradict the rows underneath them, which reads as corrupt data
		rather than a filter bug.
		"""
		mismatches = []
		for spec in _live_fields():
			for op_key in _filter_field_operators(spec):
				arity = FILTER_OPERATORS[op_key]["arity"]
				value = None if arity == "none" else _sample_value(spec, arity)
				built = _parse_filter_conditions([_condition(spec["key"], op_key, value)])
				expected = _count(built)
				actual = _dashboard_cards_for_filters(built).get("total")
				if expected != actual:
					mismatches.append(f"{spec['key']} {op_key}: list={expected} cards={actual}")
		self.assertEqual(mismatches, [], "dashboard cards disagree with the ticket list")

	def test_blank_in_value_matches_untagged_rows(self):
		"""`in [..., ""]` must match NULL rows.

		frappe.get_list renders that as ifnull(col, '') IN (...); a bare pypika
		isin() does not, which is why _qb_in_condition exists. Without the mirror
		the cards would count a different set than the list displays.
		"""
		with_blank = [[TICKET_DOCTYPE, "agent_group", "in", ["__nope__", ""]]]
		without_blank = [[TICKET_DOCTYPE, "agent_group", "in", ["__nope__"]]]
		untagged = _count([[TICKET_DOCTYPE, "agent_group", "is", "not set"]])
		self.assertEqual(_count(with_blank), _count(without_blank) + untagged)
		# ...and the cards path must reach the same conclusion.
		self.assertEqual(_dashboard_cards_for_filters(with_blank).get("total"), _count(with_blank))


class TestBuildFiltersIntegration(FrappeTestCase):
	def test_conditions_are_appended_to_the_fixed_filters(self):
		built = _build_filters(
			"all",
			{"status": "Open", "conditions": [_condition("subject", "like", "fee")]},
		)
		self.assertIn([TICKET_DOCTYPE, "status", "=", "Open"], built)
		self.assertIn([TICKET_DOCTYPE, "subject", "like", "%fee%"], built)

	def test_fixed_filters_still_work_without_conditions(self):
		# Back-compat: every pre-existing URL and the primary dropdowns must be
		# untouched by this feature.
		built = _build_filters("all", {"status": "Open", "priority": "High"})
		self.assertIn([TICKET_DOCTYPE, "status", "=", "Open"], built)
		self.assertIn([TICKET_DOCTYPE, "priority", "=", "High"], built)

	def test_every_primary_toolbar_filter_builds(self):
		"""The five dropdowns the toolbar always shows.

		`agent_group` is the newest and the easiest to drop in a refactor: it is
		both a primary filter here AND a registry field for the popover, so a
		missing branch would look fine in the UI and silently return every team.
		"""
		built = _build_filters(
			"all",
			{
				"status": "Open",
				"priority": "High",
				"ticket_type": "Question",
				"agent_group": "Support",
			},
		)
		self.assertIn([TICKET_DOCTYPE, "status", "=", "Open"], built)
		self.assertIn([TICKET_DOCTYPE, "priority", "=", "High"], built)
		self.assertIn([TICKET_DOCTYPE, "ticket_type", "=", "Question"], built)
		self.assertIn([TICKET_DOCTYPE, "agent_group", "=", "Support"], built)

	def test_agent_group_primary_filter_executes(self):
		built = _build_filters("all", {"agent_group": "Support"})
		frappe.get_list(TICKET_DOCTYPE, fields=["name"], filters=built, page_length=1)
		self.assertEqual(_dashboard_cards_for_filters(built).get("total"), _count(built))

	def test_unspecified_agent_group_uses_the_indexable_operator(self):
		"""The "Unspecified" choice, and the operator matters for speed.

		`is not set` emits `(agent_group IS NULL OR agent_group = '')`, which
		keeps agent_group_unity_idx usable. The obvious alternative, `in [""]`,
		makes DatabaseQuery wrap the column as `ifnull(agent_group, '')`, and a
		function on the column throws the index away: measured at 12.8s for one
		page of 20 versus 2.5s here. Identical rows either way, so nothing but a
		test defends the fast shape.
		"""
		built = _build_filters("all", {"agent_group": UNSPECIFIED_AGENT_GROUP})
		self.assertIn([TICKET_DOCTYPE, "agent_group", "is", "not set"], built)
		self.assertNotIn(
			[TICKET_DOCTYPE, "agent_group", "in", [""]],
			built,
			"in [''] defeats agent_group_unity_idx; keep the `is not set` shape",
		)
		self.assertNotIn(
			[TICKET_DOCTYPE, "agent_group", "=", UNSPECIFIED_AGENT_GROUP],
			built,
			"the sentinel must never reach the query as a literal team name",
		)

	def test_unspecified_agent_group_executes_and_counts_blank_rows(self):
		"""Runs for real, and counts exactly the blank tickets, NULL or ''."""
		built = _build_filters("all", {"agent_group": UNSPECIFIED_AGENT_GROUP})
		frappe.get_list(TICKET_DOCTYPE, fields=["name"], filters=built, page_length=1)
		expected = frappe.db.sql(
			"SELECT COUNT(*) FROM `tabHD Ticket` WHERE agent_group IS NULL OR agent_group = ''"
		)[0][0]
		self.assertEqual(_count(built), expected)
		self.assertEqual(_dashboard_cards_for_filters(built).get("total"), _count(built))

	def test_created_by_key_still_honoured_for_old_links(self):
		# Created By left the toolbar for the Filter popover, but the key stays so
		# links shared before the move don't quietly widen to every creator.
		built = _build_filters("all", {"created_by": "Administrator"})
		self.assertIn([TICKET_DOCTYPE, "owner", "=", "Administrator"], built)

	def test_no_filters_stays_empty(self):
		"""An empty filter list is what selects the fast dashboard-cards path
		(six index-only COUNTs instead of a full-table SUM(CASE) aggregate), so
		'no filters' must not quietly become 'one no-op filter'."""
		self.assertEqual(_build_filters("all", {}), [])
		self.assertEqual(_build_filters("all", {"conditions": []}), [])

	def test_invalid_condition_fails_before_any_query(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			_build_filters("all", {"conditions": [_condition("nope", "equals", "x")]})
