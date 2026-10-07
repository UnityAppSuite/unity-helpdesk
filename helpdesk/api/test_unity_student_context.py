# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt
"""Unit tests for the enrollment the STUDENT DETAILS panel resolves per student —
_pick_program_enrollment (the source of each card's academic_year) and
_transport_status (the card's Transport row, read off that same enrollment).

Guards the alumni-year picker: fall back to the latest ACADEMIC YEAR (not the
latest-modified row), don't let a cancelled current-year row pin an alumni to the
current year, and prefer a real enrollment over a cancelled one."""

from frappe.tests.utils import FrappeTestCase

from helpdesk.api.unity_helpdesk import (
	TRANSPORT_STATUS_MAMMA_CHILD,
	TRANSPORT_STATUS_SCHOOL_BUS,
	TRANSPORT_STATUS_UNKNOWN,
	_pick_program_enrollment,
	_transport_display,
	_transport_status,
)

CY = "2026-2027"


def _row(ay, docstatus=1, modified="2024-01-01 00:00:00", name=None):
	return {
		"name": name or f"PE-{ay}-{docstatus}",
		"academic_year": ay,
		"docstatus": docstatus,
		"modified": modified,
	}


def _enrollment(transport=None, ay=CY, **kwargs):
	"""_row() plus the transport flag. Omitting `transport` leaves the key ABSENT,
	which is exactly what the guarded get_all produces on a site without the
	edu_quality Custom Field."""
	row = _row(ay, **kwargs)
	if transport is not None:
		row["transport_service_required"] = transport
	return row


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


class TestTransportStatus(FrappeTestCase):
	"""The Transport row on the Student Details card.

	The flag is INVERTED (unchecked = no school bus = "Mamma Child") and it is
	tri-state, so the two things worth guarding are the inversion and the refusal to
	turn an absent flag into a Mamma Child."""

	def test_checked_is_school_bus(self):
		self.assertEqual(_transport_status(_enrollment(1)), TRANSPORT_STATUS_SCHOOL_BUS)

	def test_unchecked_is_mamma_child(self):
		# The whole point of the feature — the flag is inverted.
		self.assertEqual(_transport_status(_enrollment(0)), TRANSPORT_STATUS_MAMMA_CHILD)

	def test_no_enrollment_is_unknown_not_mamma_child(self):
		# The explicit product rule: a student with no enrollment is UNKNOWN, because a
		# missing enrollment is not evidence of "no bus".
		self.assertEqual(_transport_status(None), TRANSPORT_STATUS_UNKNOWN)

	def test_absent_column_is_unknown_not_mamma_child(self):
		# Site without the edu_quality Custom Field: the guarded get_all omitted the
		# column, so the key never reaches the row. Must not read as unchecked.
		self.assertEqual(_transport_status(_enrollment()), TRANSPORT_STATUS_UNKNOWN)

	def test_null_value_is_unknown(self):
		self.assertEqual(
			_transport_status({"transport_service_required": None}), TRANSPORT_STATUS_UNKNOWN
		)

	def test_truthiness_across_db_representations(self):
		# Rows can carry ints, bools or strings depending on driver / _dict round-trip.
		for checked in (1, True, "1"):
			self.assertEqual(
				_transport_status(_enrollment(checked)), TRANSPORT_STATUS_SCHOOL_BUS, checked
			)
		for unchecked in (0, False, "0"):
			self.assertEqual(
				_transport_status(_enrollment(unchecked)), TRANSPORT_STATUS_MAMMA_CHILD, unchecked
			)

	def test_flag_comes_from_the_matching_academic_year(self):
		# The two units composed: the picker's chosen row is the one whose flag the card
		# shows. Current year (no bus) must win over an older year (bus) — i.e. a student
		# who USED to take the bus shows as a Mamma Child today.
		rows = [
			_enrollment(0, ay=CY, name="current"),
			_enrollment(1, ay="2024-2025", name="old"),
		]
		selected, _ = _pick_program_enrollment(rows, CY)
		self.assertEqual(selected.get("name"), "current")
		self.assertEqual(_transport_status(selected), TRANSPORT_STATUS_MAMMA_CHILD)

	def test_alumni_flag_comes_from_their_latest_year(self):
		# No current-year row: the card shows the latest year's flag, which the card
		# header separately labels with that year.
		rows = [
			_enrollment(1, ay="2025-2026", name="latest"),
			_enrollment(0, ay="2023-2024", name="older"),
		]
		selected, _ = _pick_program_enrollment(rows, CY)
		self.assertEqual(selected.get("academic_year"), "2025-2026")
		self.assertEqual(_transport_status(selected), TRANSPORT_STATUS_SCHOOL_BUS)


class TestTransportDisplay(FrappeTestCase):
	"""The token the card shows, mirroring the permanent ID card.

	On transport -> the DROP route (permanent_id_card.html:367 uses doc.drop_bus).
	Off transport -> "M" + Student Batch Name.custom_batch_number (:369). The
	load-bearing case is the stale one: unchecking the box does NOT clear drop_bus,
	so a Mamma Child must never show a route number."""

	def test_on_transport_shows_drop_route(self):
		row = _enrollment(1)
		row["drop_bus"] = "8"
		self.assertEqual(_transport_display(row, TRANSPORT_STATUS_SCHOOL_BUS, {}), "8")

	def test_mamma_child_shows_m_prefixed_batch_number(self):
		row = _enrollment(0)
		row["student_group"] = "SG-4A"
		self.assertEqual(
			_transport_display(row, TRANSPORT_STATUS_MAMMA_CHILD, {"SG-4A": "12"}), "M12"
		)

	def test_stale_drop_route_suppressed_for_mamma_child(self):
		# Box unchecked but drop_bus still holds last year's route — the card must show
		# the batch number, never the stale bus.
		row = _enrollment(0)
		row["drop_bus"], row["student_group"] = "8", "SG-4A"
		self.assertEqual(
			_transport_display(row, TRANSPORT_STATUS_MAMMA_CHILD, {"SG-4A": "12"}), "M12"
		)

	def test_no_batch_number_yields_blank_not_bare_m(self):
		# Matches the ID card's `{{"M" if batch_num else ""}}` — no number, no "M".
		row = _enrollment(0)
		row["student_group"] = "SG-4A"
		self.assertEqual(_transport_display(row, TRANSPORT_STATUS_MAMMA_CHILD, {}), "")
		self.assertEqual(
			_transport_display(row, TRANSPORT_STATUS_MAMMA_CHILD, {"SG-4A": "  "}), ""
		)

	def test_unknown_status_and_missing_columns_yield_blank(self):
		# The SPA falls back to "-" / "Yes" / "No" on a blank token.
		self.assertEqual(_transport_display(None, TRANSPORT_STATUS_UNKNOWN, {}), "")
		self.assertEqual(
			_transport_display(_enrollment(1), TRANSPORT_STATUS_SCHOOL_BUS, {}), ""
		)

	def test_drop_route_whitespace_is_trimmed(self):
		row = _enrollment(1)
		row["drop_bus"] = "  7  "
		self.assertEqual(_transport_display(row, TRANSPORT_STATUS_SCHOOL_BUS, {}), "7")
