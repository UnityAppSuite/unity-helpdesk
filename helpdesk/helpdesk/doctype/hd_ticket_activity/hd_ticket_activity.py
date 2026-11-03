# Copyright (c) 2022, Frappe Technologies and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class HDTicketActivity(Document):
	pass


def log_ticket_activity(ticket, action, comment=None, previous_content=None, new_content=None):
	return frappe.get_doc(
		{
			"doctype": "HD Ticket Activity",
			"ticket": ticket,
			"action": action,
			"comment": comment,
			"previous_content": previous_content,
			"new_content": new_content,
		}
	).insert(ignore_permissions=True)
