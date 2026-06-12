# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt
"""Unit tests for the Unity ticket-search routing primitives — the pure functions
behind the query-shape cascade (student-code / email / phone classification, the
FULLTEXT BOOLEAN builder, and recipient-email extraction). These don't touch the
DB, so they're fast and protect the routing logic from regressions independently
of the live data."""

from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from helpdesk.api import unity_helpdesk as uh


class TestQueryClassification(FrappeTestCase):
	def test_student_code_shapes(self):
		# Student name codes (4 letters + 2 digits) and refs (2 letters + 2 digits).
		for code in ("BFOA01", "WACB39", "OA01", "CB39", "ME84", "shjc88"):
			self.assertTrue(uh._looks_like_student_code(code), f"{code} should be a code")

	def test_ordinary_words_are_not_codes(self):
		# Words without trailing digits must NOT be treated as student codes, so
		# "fees"/"ledger" fall through to content search instead of a Student lookup.
		for word in ("fees", "ledger", "transport", "bus route", "AC", "fee", ""):
			self.assertFalse(uh._looks_like_student_code(word), f"{word} should NOT be a code")

	def test_email_classification(self):
		self.assertTrue(uh._looks_like_email("parent@school.edu"))
		self.assertTrue(uh._looks_like_email("a.b+c@gmail.com"))
		self.assertFalse(uh._looks_like_email("BFOA01"))
		self.assertFalse(uh._looks_like_email("not an email"))

	def test_phone_classification(self):
		self.assertTrue(uh._looks_like_phone("9167975083"))  # 10 digit
		self.assertTrue(uh._looks_like_phone("+91 91679 75083"))  # 12 digit w/ cc
		self.assertFalse(uh._looks_like_phone("12345"))  # too short
		self.assertFalse(uh._looks_like_phone("BFOA01"))


class TestFulltextBooleanQuery(FrappeTestCase):
	def test_single_token_is_prefix_matched(self):
		# As-you-type: "transp" must still find "transport".
		self.assertEqual(uh._fulltext_boolean_query("transp"), "+transp*")

	def test_short_tokens_dropped(self):
		# Tokens below the InnoDB min size (3) aren't indexed — dropped.
		self.assertEqual(uh._fulltext_boolean_query("a bc"), "")
		# A lone >=3 char token survives as a single prefix match.
		self.assertEqual(uh._fulltext_boolean_query("a bc def"), "+def*")

	def test_multiword_requires_distinctive_tokens_exactly(self):
		q = uh._fulltext_boolean_query("fee payment receipt received")
		# 4 tokens -> require the top 3 by length, EXACT (no trailing *), drop the rest.
		self.assertIn("+received", q)
		self.assertIn("+payment", q)
		self.assertIn("+receipt", q)
		self.assertNotIn("*", q)  # multi-word required tokens are exact, not prefix
		self.assertNotIn("fee", q)  # shortest token dropped (only top-3 required)

	def test_punctuation_never_reaches_boolean_parser(self):
		# @ . - are BOOLEAN operators; the builder re-tokenises on \w+ so they can't
		# flip a token into exclude/phrase.
		q = uh._fulltext_boolean_query("ta-16@x.edu")
		self.assertNotIn("@", q)
		self.assertNotIn("-", q)


class TestRecipientEmailExtraction(FrappeTestCase):
	def _rows(self, *specs):
		import frappe

		return [frappe._dict(s) for s in specs]

	def test_extracts_dedupes_and_strips_support_inboxes(self):
		rows = self._rows(
			{"recipients": "feedback@walnutedu.in, Parent Name <parent@gmail.com>", "cc": "agent@x.com"},
			{"recipients": "parent@gmail.com", "cc": ""},  # duplicate parent — dropped
		)
		with patch.object(uh, "_support_inbox_emails", return_value=frozenset({"feedback@walnutedu.in"})):
			out = uh._build_ticket_recipient_emails(rows)
		emails = [e.strip() for e in out.split(",") if e.strip()]
		self.assertIn("parent@gmail.com", emails)
		self.assertIn("agent@x.com", emails)
		self.assertNotIn("feedback@walnutedu.in", emails)  # support inbox stripped
		self.assertEqual(len(emails), len(set(emails)), "recipients must be deduped")

	def test_empty_when_no_recipients(self):
		with patch.object(uh, "_support_inbox_emails", return_value=frozenset()):
			self.assertEqual(uh._build_ticket_recipient_emails([]), "")
			self.assertEqual(uh._build_ticket_recipient_emails(self._rows({"recipients": "", "cc": ""})), "")
