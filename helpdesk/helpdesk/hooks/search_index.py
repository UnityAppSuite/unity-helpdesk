"""
doc_events that keep the Unity Helpdesk search index in sync regardless of
any override another app may install on HD Ticket / Communication / HD Ticket
Comment. Each function is defensive: it logs and swallows errors so a stale
index never blocks ticket creation or email ingestion.
"""
import frappe


def _safe(call_kind, fn):
	try:
		fn()
	except Exception:
		frappe.log_error(
			title=f"Unity search index hook: {call_kind}",
			message=frappe.get_traceback(),
		)


def on_ticket_after_insert(doc, method=None):
	"""Populate student-identity + message-body search fields on a new ticket."""
	from helpdesk.api.unity_helpdesk import (
		populate_ticket_student_search_fields,
		update_ticket_message_search_index,
	)

	_safe(
		"populate_ticket_student_search_fields",
		lambda: populate_ticket_student_search_fields(doc),
	)
	_safe(
		"update_ticket_message_search_index (after_insert)",
		lambda: update_ticket_message_search_index(doc.name, ticket_doc=doc),
	)


def _enqueue_search_index(ticket_name):
	"""Rebuild the message-body search index OFF the request.

	The reply/note write path used to refresh the index synchronously (and these
	doc-event hooks fired it again), so a busy ticket's whole-thread rebuild
	— get_ticket_thread_components fetches every Communication + per-comm
	attachments/avatars — ran 2-3x on EVERY reply/note, adding seconds of latency.

	Enqueue it instead, on the `short` queue, **deduplicated per ticket** (so the
	after_insert + on_update + any sibling events for one ticket coalesce into a
	single job) and **after_commit** (so the new Communication/comment is visible
	to the worker). A stale index for a couple of seconds never blocks the write.
	Falls back to a direct sync update only if enqueue itself fails (no worker)."""
	if not ticket_name:
		return
	from helpdesk.api.unity_helpdesk import update_ticket_message_search_index

	try:
		frappe.enqueue(
			"helpdesk.api.unity_helpdesk.update_ticket_message_search_index",
			ticket_name=ticket_name,
			queue="short",
			job_id=f"unity_search_idx::{ticket_name}",
			deduplicate=True,
			enqueue_after_commit=True,
		)
	except Exception:
		_safe(
			"update_ticket_message_search_index (sync fallback)",
			lambda: update_ticket_message_search_index(ticket_name),
		)


def on_communication_after_insert(doc, method=None):
	"""Refresh the message-body index whenever a new email/communication lands on a ticket."""
	if doc.get("reference_doctype") != "HD Ticket":
		return
	_enqueue_search_index(doc.get("reference_name"))


def on_comment_after_insert(doc, method=None):
	"""Refresh the message-body index when a comment is added."""
	_enqueue_search_index(doc.get("reference_ticket"))


def on_communication_on_update(doc, method=None):
	"""Refresh the message-body index when an existing email/communication is edited."""
	if doc.get("reference_doctype") != "HD Ticket":
		return
	_enqueue_search_index(doc.get("reference_name"))


def on_comment_on_update(doc, method=None):
	"""Refresh the message-body index when an existing comment is edited."""
	_enqueue_search_index(doc.get("reference_ticket"))
