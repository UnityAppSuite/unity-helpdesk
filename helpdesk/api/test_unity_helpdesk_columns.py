# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt
"""Tests for per-user column preferences in the Unity Helpdesk tickets list."""

import json

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.api.unity_helpdesk import (
	AVAILABLE_TICKET_COLUMNS,
	COLUMN_PREFS_DEFAULT_KEY,
	COLUMN_PREFS_MAX_ITEMS,
	COLUMN_WIDTH_MAX,
	COLUMN_WIDTH_MIN,
	_default_column_preferences,
	_load_column_preferences,
	update_column_preferences,
)


FIXED_KEYS = [c["key"] for c in AVAILABLE_TICKET_COLUMNS if c["fixed"]]


def _clear_default(user):
	frappe.db.sql(
		"DELETE FROM `tabDefaultValue` WHERE parent=%s AND defkey=%s",
		(user, COLUMN_PREFS_DEFAULT_KEY),
	)
	frappe.clear_cache(user=user)


class TestColumnPreferencesLoad(FrappeTestCase):
	def setUp(self):
		_clear_default(frappe.session.user)

	def tearDown(self):
		_clear_default(frappe.session.user)

	def test_default_returned_when_no_user_setting(self):
		self.assertEqual(_load_column_preferences(), _default_column_preferences())

	def test_round_trip_persists_user_choice(self):
		update_column_preferences(json.dumps([{"key": "priority", "width": 200}]))
		loaded = _load_column_preferences()
		# Fixed columns are prepended, then user's selection
		fixed_loaded = [p["key"] for p in loaded if p["key"] in FIXED_KEYS]
		self.assertEqual(fixed_loaded, FIXED_KEYS)
		priority = next((p for p in loaded if p["key"] == "priority"), None)
		self.assertIsNotNone(priority)
		self.assertEqual(priority["width"], 200)


class TestUpdateColumnPreferences(FrappeTestCase):
	def setUp(self):
		_clear_default(frappe.session.user)

	def tearDown(self):
		_clear_default(frappe.session.user)

	def test_non_list_payload_throws(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			update_column_preferences(json.dumps({"not": "a list"}))

	def test_invalid_json_string_throws(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			update_column_preferences("{not-json")

	def test_invalid_column_key_sanitized_out(self):
		# Unknown key should be dropped; defaults injected back in
		result = update_column_preferences(
			json.dumps([{"key": "DROP TABLE tickets", "width": 100}])
		)
		keys = {p["key"] for p in result["column_preferences"]}
		self.assertNotIn("DROP TABLE tickets", keys)
		# Falls back to defaults when everything is invalid (after fixed-col injection)
		self.assertIn("name", keys)
		self.assertIn("subject", keys)

	def test_width_clamped_to_min(self):
		result = update_column_preferences(
			json.dumps([{"key": "priority", "width": 10}])
		)
		priority = next(p for p in result["column_preferences"] if p["key"] == "priority")
		self.assertEqual(priority["width"], COLUMN_WIDTH_MIN)

	def test_width_clamped_to_max(self):
		result = update_column_preferences(
			json.dumps([{"key": "priority", "width": 9999}])
		)
		priority = next(p for p in result["column_preferences"] if p["key"] == "priority")
		self.assertEqual(priority["width"], COLUMN_WIDTH_MAX)

	def test_fixed_columns_always_present(self):
		# Submit prefs omitting name/subject — they must still appear at the front
		result = update_column_preferences(
			json.dumps([{"key": "priority", "width": 130}])
		)
		keys = [p["key"] for p in result["column_preferences"]]
		for k in FIXED_KEYS:
			self.assertIn(k, keys)
		# Fixed keys come before the user-chosen ones
		first_non_fixed = next(i for i, k in enumerate(keys) if k not in FIXED_KEYS)
		fixed_indices = [keys.index(k) for k in FIXED_KEYS]
		self.assertTrue(all(idx < first_non_fixed for idx in fixed_indices))

	def test_dedups_duplicate_keys(self):
		result = update_column_preferences(
			json.dumps(
				[
					{"key": "priority", "width": 200},
					{"key": "priority", "width": 300},
				]
			)
		)
		priority_widths = [p["width"] for p in result["column_preferences"] if p["key"] == "priority"]
		self.assertEqual(priority_widths, [200])

	def test_oversize_payload_rejected(self):
		oversize = [{"key": "priority", "width": 130}] * (COLUMN_PREFS_MAX_ITEMS + 1)
		with self.assertRaises(frappe.exceptions.ValidationError):
			update_column_preferences(json.dumps(oversize))
