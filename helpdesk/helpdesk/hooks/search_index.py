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


def on_communication_after_insert(doc, method=None):
	"""Refresh the message-body index whenever a new email/communication lands on a ticket."""
	if doc.get("reference_doctype") != "HD Ticket":
		return
	ticket_name = doc.get("reference_name")
	if not ticket_name:
		return

	from helpdesk.api.unity_helpdesk import update_ticket_message_search_index

	_safe(
		"update_ticket_message_search_index (communication)",
		lambda: update_ticket_message_search_index(ticket_name),
	)


def on_comment_after_insert(doc, method=None):
	"""Refresh the message-body index when a comment is added."""
	ticket_name = doc.get("reference_ticket")
	if not ticket_name:
		return

	from helpdesk.api.unity_helpdesk import update_ticket_message_search_index

	_safe(
		"update_ticket_message_search_index (comment)",
		lambda: update_ticket_message_search_index(ticket_name),
	)


def on_communication_on_update(doc, method=None):
	"""Refresh the message-body index when an existing email/communication is edited."""
	if doc.get("reference_doctype") != "HD Ticket":
		return
	ticket_name = doc.get("reference_name")
	if not ticket_name:
		return

	from helpdesk.api.unity_helpdesk import update_ticket_message_search_index

	_safe(
		"update_ticket_message_search_index (communication on_update)",
		lambda: update_ticket_message_search_index(ticket_name),
	)


def on_comment_on_update(doc, method=None):
	"""Refresh the message-body index when an existing comment is edited."""
	ticket_name = doc.get("reference_ticket")
	if not ticket_name:
		return

	from helpdesk.api.unity_helpdesk import update_ticket_message_search_index

	_safe(
		"update_ticket_message_search_index (comment on_update)",
		lambda: update_ticket_message_search_index(ticket_name),
	)
