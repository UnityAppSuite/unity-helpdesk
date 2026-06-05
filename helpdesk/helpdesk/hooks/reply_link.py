"""
On Communication.after_insert: when an inbound email's In-Reply-To chain
resolves to a Sent Communication on an outgoing-initiated ticket (bulk-email
audit ticket, custom_is_bulk_email=1), record the link on the new ticket via
custom_replied_to_ticket.

We only follow ONE hop — the in_reply_to parent — and only mark when the
parent ticket differs from the current ticket. If Frappe already threaded the
reply INTO the bulk audit ticket, no link is set (it's the same ticket).

Defensive: errors are logged, never raised — incoming email ingestion must
not break if linking fails.
"""
import frappe


MAX_HOPS = 5


def on_communication_after_insert(doc, method=None):
	try:
		_maybe_link_to_outgoing(doc)
	except Exception:
		frappe.log_error(
			title="Unity reply-link hook",
			message=frappe.get_traceback(),
		)


def _maybe_link_to_outgoing(comm):
	if comm.get("reference_doctype") != "HD Ticket":
		return
	if comm.get("sent_or_received") != "Received":
		return
	current_ticket = comm.get("reference_name")
	if not current_ticket:
		return

	source_ticket = _resolve_outgoing_source(comm.get("in_reply_to"))
	if not source_ticket or source_ticket == current_ticket:
		return

	# Mark if the source is one we sent — bulk-email audit OR portal-created.
	flags = frappe.db.get_value(
		"HD Ticket",
		source_ticket,
		["custom_is_bulk_email", "custom_via_unity_portal"],
		as_dict=True,
	) or {}
	if not (flags.get("custom_is_bulk_email") or flags.get("custom_via_unity_portal")):
		return

	existing = frappe.db.get_value(
		"HD Ticket", current_ticket, "custom_replied_to_ticket"
	)
	if existing == source_ticket:
		return

	frappe.db.set_value(
		"HD Ticket",
		current_ticket,
		"custom_replied_to_ticket",
		source_ticket,
		update_modified=False,
	)


def _resolve_outgoing_source(parent_comm_name, hops=0):
	"""Walk in_reply_to chain. Return ref_name of the first Sent Communication
	on an HD Ticket. Bounded to MAX_HOPS to defend against malformed loops."""
	if not parent_comm_name or hops >= MAX_HOPS:
		return None
	parent = frappe.db.get_value(
		"Communication",
		parent_comm_name,
		["sent_or_received", "reference_doctype", "reference_name", "in_reply_to"],
		as_dict=True,
	)
	if not parent:
		return None
	if (
		parent.sent_or_received == "Sent"
		and parent.reference_doctype == "HD Ticket"
		and parent.reference_name
	):
		return parent.reference_name
	return _resolve_outgoing_source(parent.in_reply_to, hops + 1)
