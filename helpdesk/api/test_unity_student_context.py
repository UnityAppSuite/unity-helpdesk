# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt
"""Unit tests for _pick_program_enrollment — the enrollment the STUDENT DETAILS
panel resolves per student (and the source of each card's academic_year).

Guards the alumni-year picker: fall back to the latest ACADEMIC YEAR (not the
latest-modified row), don't let a cancelled current-year row pin an alumni to the
current year, and prefer a real enrollment over a cancelled one."""

from frappe.tests.utils import FrappeTestCase

from helpdesk.api.unity_helpdesk import _pick_program_enrollment

CY = "2026-2027"


def _row(ay, docstatus=1, modified="2024-01-01 00:00:00", name=None):
	return {
		"name": name or f"PE-{ay}-{docstatus}",
		"academic_year": ay,
		"docstatus": docstatus,
		"modified": modified,
	}


class TestPickProgramEnrollment(FrappeTestCase):
	def test_current_year_submitted_preferred(self):
		selected, msgs = _pick_program_enrollment([_row(CY, 1), _row("2024-2025", 1)], CY)
		self.assertEqual(selected.get("academic_year"), CY)
		self.assertEqual(msgs, [])

	def test_cancelled_latest_year_is_still_shown(self):
		# SHLD43 case: the student's LATEST year (2025-2026) is CANCELLED and an older
		# year is submitted. Show the latest year — the Status column shows "Cancelled",
		# so the year must not silently drop to the older 2024-2025.
		selected, _ = _pick_program_enrollment([_row("2025-2026", 2), _row("2024-2025", 1)], CY)
		self.assertEqual(selected.get("academic_year"), "2025-2026")

	def test_no_current_year_picks_latest_year_not_latest_modified(self):
		# An older year re-saved more recently must NOT win over the true latest year.
		rows = [
			_row("2023-2024", 1, modified="2026-05-01 00:00:00"),  # re-saved recently
			_row("2025-2026", 1, modified="2025-06-01 00:00:00"),  # true latest year
		]
		selected, _ = _pick_program_enrollment(rows, CY)
		self.assertEqual(selected.get("academic_year"), "2025-2026")

	def test_within_fallback_year_submitted_beats_draft(self):
		rows = [
			_row("2025-2026", 0, name="draft", modified="2026-01-01 00:00:00"),
			_row("2025-2026", 1, name="submitted", modified="2025-01-01 00:00:00"),
		]
		selected, _ = _pick_program_enrollment(rows, CY)
		self.assertEqual(selected.get("name"), "submitted")

	def test_no_current_year_arg_picks_latest_year(self):
		selected, _ = _pick_program_enrollment([_row("2024-2025", 1), _row("2025-2026", 1)], None)
		self.assertEqual(selected.get("academic_year"), "2025-2026")

	def test_empty_rows(self):
		selected, msgs = _pick_program_enrollment([], CY)
		self.assertIsNone(selected)
		self.assertEqual(msgs, [])
