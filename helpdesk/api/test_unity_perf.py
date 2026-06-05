# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt
"""Tests for the Unity Helpdesk perf benchmark endpoint."""

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.api.unity_perf import run_filter_benchmark


class TestRunFilterBenchmark(FrappeTestCase):
	def test_requires_unity_access(self):
		original = frappe.session.user
		try:
			frappe.set_user("Guest")
			with self.assertRaises(frappe.PermissionError):
				run_filter_benchmark()
		finally:
			frappe.set_user(original)

	def test_returns_expected_shape(self):
		res = run_filter_benchmark()
		self.assertIn("timings", res)
		self.assertIn("explains", res)
		self.assertIn("row_count", res)
		self.assertIsInstance(res["row_count"], int)

		expected_names = {
			"total_count",
			"date_range_3w",
			"date_range_closed",
			"assign_like",
			"dashboard_cards_agg",
			"list_page_100",
		}
		got_names = {t["name"] for t in res["timings"]}
		self.assertEqual(got_names, expected_names)

		# Each timing row has both cold and warm reads in milliseconds.
		for row in res["timings"]:
			self.assertIn("cold_ms", row)
			self.assertIn("warm_ms", row)
			self.assertGreaterEqual(row["cold_ms"], 0)
			self.assertGreaterEqual(row["warm_ms"], 0)

	def test_explain_includes_high_traffic_queries(self):
		res = run_filter_benchmark()
		# We deliberately EXPLAIN these two because they dominate the list page
		# load and are the first place an index regression would show up.
		self.assertIn("date_range_3w", res["explains"])
		self.assertIn("dashboard_cards_agg", res["explains"])

	def test_explain_uses_creation_index_when_present(self):
		"""If the `creation_unity_idx` index exists, the date-range EXPLAIN row
		should reference it. Skips if the index hasn't been migrated yet (e.g.
		fresh test DB without the patch applied)."""
		row = frappe.db.sql(
			"SHOW INDEXES FROM `tabHD Ticket` WHERE Key_name='creation_unity_idx'"
		)
		if not row:
			self.skipTest("creation_unity_idx not present in this DB")
		res = run_filter_benchmark()
		explain_rows = res["explains"]["date_range_3w"]
		# `key` is the index actually used by the optimizer. Allow it to be
		# either our named index or any index that covers `creation` — MariaDB
		# may merge planning across equivalent indexes.
		keys_used = {(r.get("key") or "") for r in explain_rows}
		self.assertTrue(
			any("creation" in k.lower() for k in keys_used if k),
			f"expected an index covering `creation` to be used; got {keys_used}",
		)
