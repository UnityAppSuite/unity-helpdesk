import json
from datetime import timedelta

import frappe
from frappe import _
from frappe.desk.form.assign_to import clear as clear_all_assignments
from frappe.utils import add_days, get_datetime, get_url, nowdate

from helpdesk.api.ticket import assign_ticket_to_agent


TICKET_DOCTYPE = "HD Ticket"
OPEN_STATUSES = ["Open", "Replied"]
FINAL_STATUSES = ["Resolved", "Closed"]
STATUS_OPTIONS = ["Open", "Replied", "Resolved", "Closed"]
PRIORITY_TARGETS = {
	"High": "Same day",
	"Medium": "1-2 days",
	"Low": "2-3 days",
}
UNITY_TICKET_FIELDS = [
	"name",
	"subject",
	"raised_by",
	"status",
	"priority",
	"ticket_type",
	"agent_group",
	"description",
	"creation",
	"modified",
	"_assign",
]
OPTIONAL_TICKET_FIELDS = [
	"custom_is_on_hold",
	"custom_hold_from",
	"custom_hold_to",
	"custom_hold_reason",
	"custom_list_of_student",
	"custom_all_fees_details_of_students",
	"custom_payment_schedule",
	"custom_student_remark",
	"custom_previous_ticket_details",
]


def _parse_json(value, fallback):
	if value in (None, ""):
		return fallback
	if isinstance(value, str):
		return frappe.parse_json(value)
	return value


def _has_field(doctype, fieldname):
	return frappe.get_meta(doctype).has_field(fieldname)


def _ticket_fields():
	fields = list(UNITY_TICKET_FIELDS)
	for field in OPTIONAL_TICKET_FIELDS:
		if _has_field(TICKET_DOCTYPE, field):
			fields.append(field)
	return fields


def _assignee_from_assign(assign_value):
	assignees = frappe.parse_json(assign_value or "[]")
	if not assignees:
		return None
	user = assignees[0]
	return frappe.db.get_value(
		"User",
		user,
		["name", "full_name", "user_image", "email"],
		as_dict=True,
	) or {"name": user, "full_name": user}


def _status_indicator(row):
	is_on_hold = bool(int(row.get("custom_is_on_hold") or 0))
	assignee = row.get("assignee")
	if is_on_hold:
		return {"label": "On Hold", "color": "yellow"}
	if row.status == "Closed":
		return {"label": "Closed", "color": "grey"}
	if row.status == "Resolved":
		return {"label": "Resolved", "color": "green"}
	if not assignee:
		return {"label": "Unassigned", "color": "pink"}
	return {"label": "Assigned", "color": "blue"}


def _decorate_ticket(row):
	row = frappe._dict(row)
	row.assignee = _assignee_from_assign(row.get("_assign"))
	row.status_indicator = _status_indicator(row)
	row.priority_target = PRIORITY_TARGETS.get(row.get("priority"), "")
	return row


def _search_or_filters(search):
	if not search:
		return []
	search = f"%{search}%"
	fields = ["name", "subject", "raised_by", "description"]
	for field in [
		"custom_list_of_student",
		"custom_all_fees_details_of_students",
		"custom_payment_schedule",
		"custom_student_remark",
		"custom_previous_ticket_details",
	]:
		if _has_field(TICKET_DOCTYPE, field):
			fields.append(field)
	return [[TICKET_DOCTYPE, field, "like", search] for field in fields]


def _build_filters(view="all", filters=None):
	filters = frappe._dict(_parse_json(filters, {}) or {})
	res = []

	if view == "my":
		res.append([TICKET_DOCTYPE, "_assign", "like", f"%{frappe.session.user}%"])

	if filters.get("status"):
		if filters.status == "On Hold" and _has_field(TICKET_DOCTYPE, "custom_is_on_hold"):
			res.append([TICKET_DOCTYPE, "custom_is_on_hold", "=", 1])
		else:
			res.append([TICKET_DOCTYPE, "status", "=", filters.status])

	if filters.get("priority"):
		res.append([TICKET_DOCTYPE, "priority", "=", filters.priority])

	if filters.get("assigned_to"):
		if filters.assigned_to == "Unassigned":
			res.append([TICKET_DOCTYPE, "_assign", "in", ["", "[]"]])
		else:
			res.append([TICKET_DOCTYPE, "_assign", "like", f"%{filters.assigned_to}%"])

	if filters.get("created_from"):
		res.append([TICKET_DOCTYPE, "creation", ">=", filters.created_from])
	if filters.get("created_to"):
		res.append([TICKET_DOCTYPE, "creation", "<=", filters.created_to])

	if _has_field(TICKET_DOCTYPE, "custom_hold_from") and filters.get("hold_from"):
		res.append([TICKET_DOCTYPE, "custom_hold_from", ">=", filters.hold_from])
	if _has_field(TICKET_DOCTYPE, "custom_hold_to") and filters.get("hold_to"):
		res.append([TICKET_DOCTYPE, "custom_hold_to", "<=", filters.hold_to])

	return res


@frappe.whitelist()
def get_tickets(view="all", filters=None, search=None, page_length=20, start=0):
	page_length = int(page_length or 20)
	start = int(start or 0)
	list_filters = _build_filters(view, filters)
	or_filters = _search_or_filters(search)
	fields = _ticket_fields()

	rows = frappe.get_list(
		TICKET_DOCTYPE,
		fields=fields,
		filters=list_filters,
		or_filters=or_filters,
		order_by="modified desc",
		limit_start=start,
		page_length=page_length,
	)
	all_rows = frappe.get_list(
		TICKET_DOCTYPE,
		fields=["name"],
		filters=list_filters,
		or_filters=or_filters,
		page_length=0,
	)

	return {
		"data": [_decorate_ticket(row) for row in rows],
		"total_count": len(all_rows),
		"row_count": len(rows),
		"start": start,
		"page_length": page_length,
	}


def _count(filters=None):
	return len(frappe.get_list(TICKET_DOCTYPE, fields=["name"], filters=filters or {}, page_length=0))


def _date_counts(from_date):
	rows = frappe.get_list(
		TICKET_DOCTYPE,
		fields=["name", "status", "creation"],
		filters={"creation": [">=", from_date]},
		page_length=0,
	)
	summary = {}
	for row in rows:
		day = str(get_datetime(row.creation).date())
		summary.setdefault(day, {"created": 0, "resolved": 0, "closed": 0})
		summary[day]["created"] += 1
		if row.status == "Resolved":
			summary[day]["resolved"] += 1
		if row.status == "Closed":
			summary[day]["closed"] += 1
	return [{"date": day, **values} for day, values in sorted(summary.items())]


@frappe.whitelist()
def get_dashboard_summary(range="week"):
	today = nowdate()
	from_date = add_days(today, -6 if range == "week" else 0)
	hold_filter = {"custom_is_on_hold": 1} if _has_field(TICKET_DOCTYPE, "custom_is_on_hold") else {"status": "Paused"}
	pending_filter = {"status": ["in", OPEN_STATUSES]}

	return {
		"cards": {
			"created": _count({"creation": [">=", from_date]}),
			"resolved": _count({"status": "Resolved", "modified": [">=", from_date]}),
			"closed": _count({"status": "Closed", "modified": [">=", from_date]}),
			"on_hold": _count(hold_filter),
			"pending": _count(pending_filter),
		},
		"series": _date_counts(from_date),
		"status": frappe.get_list(
			TICKET_DOCTYPE,
			fields=["count(name) as value", "status as name"],
			group_by="status",
		),
	}


@frappe.whitelist()
def get_users():
	users = frappe.get_list(
		"User",
		fields=["name", "full_name", "email", "user_image", "enabled"],
		filters={"enabled": 1, "user_type": "System User"},
		order_by="full_name asc",
		page_length=200,
	)
	agents = {
		row.name: row
		for row in frappe.get_all(
			"HD Agent",
			fields=["name", "agent_name", "user_image"],
			page_length=0,
		)
	}
	for user in users:
		agent = agents.get(user.name)
		user.is_agent = bool(agent)
		user.agent_name = agent.agent_name if agent else ""
	return users


@frappe.whitelist()
def get_profile():
	user = frappe.get_doc("User", frappe.session.user)
	return {
		"name": user.name,
		"full_name": user.full_name,
		"email": user.email,
		"username": user.username,
		"user_image": user.user_image,
		"roles": [role.role for role in user.roles],
	}


@frappe.whitelist()
def update_ticket(
	name,
	assignee=None,
	status=None,
	priority=None,
	is_on_hold=None,
	hold_from=None,
	hold_to=None,
	hold_reason=None,
):
	ticket = frappe.get_doc(TICKET_DOCTYPE, name)

	if assignee is not None:
		if assignee:
			assign_ticket_to_agent(name, assignee)
		else:
			clear_all_assignments(TICKET_DOCTYPE, name)

	if status:
		if status not in STATUS_OPTIONS:
			frappe.throw(_("Invalid ticket status"))
		ticket.status = status
		if status in FINAL_STATUSES and _has_field(TICKET_DOCTYPE, "custom_is_on_hold"):
			ticket.custom_is_on_hold = 0

	if priority:
		ticket.priority = priority

	if is_on_hold is not None and _has_field(TICKET_DOCTYPE, "custom_is_on_hold"):
		ticket.custom_is_on_hold = int(bool(int(is_on_hold)))
	if hold_from is not None and _has_field(TICKET_DOCTYPE, "custom_hold_from"):
		ticket.custom_hold_from = hold_from
	if hold_to is not None and _has_field(TICKET_DOCTYPE, "custom_hold_to"):
		ticket.custom_hold_to = hold_to
	if hold_reason is not None and _has_field(TICKET_DOCTYPE, "custom_hold_reason"):
		ticket.custom_hold_reason = hold_reason
	if ticket.status in FINAL_STATUSES and _has_field(TICKET_DOCTYPE, "custom_is_on_hold"):
		ticket.custom_is_on_hold = 0

	ticket.save()
	rows = frappe.get_list(TICKET_DOCTYPE, fields=_ticket_fields(), filters={"name": name}, page_length=1)
	return _decorate_ticket(rows[0]) if rows else {}


@frappe.whitelist()
def reply(name, message, cc=None, bcc=None, attachments=None):
	if not message:
		frappe.throw(_("Please enter a reply"))
	ticket = frappe.get_doc(TICKET_DOCTYPE, name)
	ticket.reply_via_agent(message=message, cc=cc, bcc=bcc, attachments=_parse_json(attachments, []))
	return {"ok": True}


def _reminder_ticket_filter(reminder_after_days):
	cutoff = add_days(nowdate(), -reminder_after_days)
	filters = {
		"status": ["in", OPEN_STATUSES],
		"creation": ["<=", cutoff],
	}
	return filters


def send_open_ticket_reminders():
	try:
		enabled = frappe.db.get_single_value("HD Settings", "enable_unity_ticket_reminders")
	except Exception:
		return
	if not int(enabled or 0):
		return

	reminder_after_days = int(frappe.db.get_single_value("HD Settings", "unity_reminder_after_days") or 3)
	tickets = frappe.get_list(
		TICKET_DOCTYPE,
		fields=["name", "subject", "raised_by", "_assign"],
		filters=_reminder_ticket_filter(reminder_after_days),
		page_length=0,
	)

	for ticket in tickets:
		for assignee in json.loads(ticket._assign or "[]"):
			if not assignee:
				continue
			frappe.sendmail(
				recipients=[assignee],
				subject=f"Reminder: Ticket #{ticket.name} is still open",
				message=(
					f"<p>Ticket <b>#{ticket.name}</b> has been open for at least "
					f"{reminder_after_days} days.</p>"
					f"<p><b>Subject:</b> {frappe.utils.escape_html(ticket.subject or '')}</p>"
					f"<p><a href='{get_url('/unity-helpdesk/tickets/' + str(ticket.name))}'>Open ticket</a></p>"
				),
				delayed=True,
				reference_doctype=TICKET_DOCTYPE,
				reference_name=ticket.name,
			)
