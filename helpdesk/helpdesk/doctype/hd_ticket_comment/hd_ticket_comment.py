# Copyright (c) 2022, Frappe Technologies and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from helpdesk.mixins.mentions import HasMentions
from helpdesk.utils import capture_event, publish_event
from helpdesk.api.unity_helpdesk import update_ticket_message_search_index


NOT_ALLOWED_MESSAGE = "You are not allowed to edit or delete this internal note"


def _user_can_access_ticket(ticket, user=None):
	"""Unity ticket-access rule for an explicit user (never switches the session)."""
	from helpdesk.api.unity_helpdesk import TICKET_DOCTYPE, _get_capabilities

	user = user or frappe.session.user
	if not ticket:
		return False
	capabilities = _get_capabilities(user)
	if not capabilities.can_view_my_tickets:
		return False
	row = frappe.db.get_value(TICKET_DOCTYPE, ticket, ["name", "_assign"], as_dict=True)
	if not row:
		return False
	if capabilities.can_view_all_tickets:
		return True
	assigned = frappe.parse_json(row.get("_assign") or "[]") or []
	return user in assigned


def _is_author_or_manager(commented_by, user, capabilities):
	return commented_by == user or bool(capabilities.can_view_all_tickets)


def _can_manage_comment(doc, user=None):
	"""True when `user` may edit/delete the persisted internal note `doc`.

	Author/reference are read from the database, never from the (possibly
	modified) in-memory doc, so a forged replacement cannot grant access.
	"""
	from helpdesk.api.unity_helpdesk import _get_capabilities

	user = user or frappe.session.user
	name = doc.get("name") if hasattr(doc, "get") else getattr(doc, "name", None)
	if not name:
		return False
	persisted = frappe.db.get_value(
		"HD Ticket Comment", name, ["commented_by", "reference_ticket"], as_dict=True
	)
	if not persisted:
		return False
	if not _user_can_access_ticket(persisted.reference_ticket, user):
		return False
	return _is_author_or_manager(persisted.commented_by, user, _get_capabilities(user))


def has_permission(doc, ptype=None, user=None):
	"""Veto-only controller permission: edits/deletes of existing notes."""
	if ptype not in ("write", "delete"):
		return None
	if doc.is_new():
		return None
	if _can_manage_comment(doc, user):
		return None
	return False


class HDTicketComment(HasMentions, Document):
	mentions_field = "content"

	def on_update(self):
		self.notify_mentions()
		update_ticket_message_search_index(self.reference_ticket)

	def after_insert(self):
		event = "helpdesk:new-ticket-comment"
		data = {"ticket_id": self.reference_ticket}
		telemetry_event = "ticket_comment_added"

		publish_event(event, data)
		capture_event(telemetry_event)
		update_ticket_message_search_index(self.reference_ticket)

	def after_delete(self):
		event = "helpdesk:delete-ticket-comment"
		data = {"ticket_id": self.reference_ticket}
		telemetry_event = "ticket_comment_deleted"

		publish_event(event, data)
		capture_event(telemetry_event)
		update_ticket_message_search_index(self.reference_ticket)
