import json
from collections import defaultdict

import frappe
from frappe import _
from frappe.desk.form.assign_to import clear as clear_all_assignments
from frappe.utils import add_days, add_months, get_datetime, get_first_day, get_last_day, get_url, getdate, nowdate

from helpdesk.api.ticket import assign_ticket_to_agent
from helpdesk.helpdesk.doctype.hd_ticket.api import get_one as get_ticket_doc


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


def _assignee_from_assign(assign_value, ticket_name=None):
	assignees = frappe.parse_json(assign_value or "[]")
	if (not assignees) and ticket_name:
		try:
			from frappe.desk.form.assign_to import get

			assignments = get({"doctype": TICKET_DOCTYPE, "name": ticket_name})
			assignees = [row.owner for row in assignments if getattr(row, "owner", None)]
		except Exception:
			assignees = []
	if not assignees:
		return None
	user = assignees[0]
	return frappe.db.get_value(
		"User",
		user,
		["name", "full_name", "user_image", "email"],
		as_dict=True,
	) or {"name": user, "full_name": user}


def _assignment_value(row):
	row = frappe._dict(row)
	return row.get("_assign") or getattr(row, "_assign", None)


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
	row.assignee = _assignee_from_assign(_assignment_value(row), row.get("name"))
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


def _count(filters=None, or_filters=None):
	row = frappe.get_list(
		TICKET_DOCTYPE,
		fields=["count(name) as total_count"],
		filters=filters or {},
		or_filters=or_filters or [],
		page_length=1,
	)
	return int((row[0].total_count if row else 0) or 0)


def _agent_map():
	agents = frappe.get_all(
		"HD Agent",
		fields=["name", "user", "agent_name", "user_image", "is_active"],
		page_length=0,
		order_by="agent_name asc",
	)
	if not agents:
		return {}

	users = {
		row.name: row
		for row in frappe.get_all(
			"User",
			fields=["name", "full_name", "email", "user_image", "enabled"],
			filters={"name": ["in", [agent.user for agent in agents if agent.user]]},
			page_length=0,
		)
	}

	res = {}
	for agent in agents:
		user = users.get(agent.user) or {}
		res[agent.name] = frappe._dict(
			{
				"name": agent.name,
				"user": agent.user,
				"full_name": agent.agent_name or user.get("full_name") or agent.user,
				"agent_name": agent.agent_name or user.get("full_name") or agent.user,
				"email": user.get("email"),
				"user_image": agent.user_image or user.get("user_image"),
				"enabled": user.get("enabled", 1),
				"is_active": agent.is_active,
				"is_agent": True,
			}
		)
	return res


def _ticket_type_options():
	return frappe.get_all(
		"HD Ticket Type",
		fields=["name"],
		order_by="name asc",
		page_length=0,
	)


def _log_hold_reason(ticket_name, hold_reason):
	if not hold_reason:
		return
	frappe.get_doc(
		{
			"doctype": "HD Ticket Comment",
			"commented_by": frappe.session.user,
			"content": f"Hold Reason: {hold_reason}",
			"is_pinned": 0,
			"reference_ticket": ticket_name,
		}
	).insert(ignore_permissions=True)


def _agent_candidates():
	assigned_users = [row.user for row in frappe.get_all("HD Agent", fields=["user"], page_length=0) if row.user]
	filters = {"enabled": 1, "user_type": "System User"}
	if assigned_users:
		filters["name"] = ["not in", assigned_users]
	return frappe.get_all(
		"User",
		fields=["name", "full_name", "email", "user_image"],
		filters=filters,
		order_by="full_name asc",
		page_length=200,
	)


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

	return {
		"data": [_decorate_ticket(row) for row in rows],
		"total_count": _count(list_filters, or_filters),
		"row_count": len(rows),
		"start": start,
		"page_length": page_length,
	}


def _dashboard_range(range_key="week", from_date=None, to_date=None):
	today = getdate(nowdate())
	range_key = (range_key or "week").lower()

	if range_key == "today":
		return today, today, "Today", "day"
	if range_key == "week":
		start = add_days(today, -6)
		return getdate(start), today, "This week", "day"
	if range_key == "month":
		start = get_first_day(today)
		return getdate(start), today, "This month", "day"
	if range_key == "quarter":
		quarter_start_month = ((today.month - 1) // 3) * 3 + 1
		start = getdate(f"{today.year}-{quarter_start_month:02d}-01")
		return start, today, "This quarter", "week"
	if range_key == "year":
		start = getdate(f"{today.year}-01-01")
		return start, today, "This year", "month"
	if range_key == "custom":
		start = getdate(from_date) if from_date else today
		end = getdate(to_date) if to_date else today
		if start > end:
			start, end = end, start
		span_days = (end - start).days + 1
		bucket = "day" if span_days <= 45 else "week" if span_days <= 180 else "month"
		return start, end, "Custom range", bucket

	return _dashboard_range("week", from_date, to_date)


def _dashboard_rows(from_date, to_date):
	filters = [
		[TICKET_DOCTYPE, "creation", ">=", str(from_date)],
		[TICKET_DOCTYPE, "creation", "<=", f"{to_date} 23:59:59"],
	]
	return frappe.get_list(
		TICKET_DOCTYPE,
		fields=["name", "status", "ticket_type", "creation", "modified", "custom_is_on_hold"],
		filters=filters,
		page_length=0,
		order_by="creation asc",
	)


def _week_bucket_start(date_obj):
	return add_days(date_obj, -date_obj.weekday())


def _month_bucket_start(date_obj):
	return getdate(f"{date_obj.year}-{date_obj.month:02d}-01")


def _bucket_key(date_obj, bucket):
	if bucket == "month":
		return _month_bucket_start(date_obj)
	if bucket == "week":
		return _week_bucket_start(date_obj)
	return date_obj


def _bucket_label(date_obj, bucket):
	if bucket == "month":
		return date_obj.strftime("%b %Y")
	if bucket == "week":
		end = add_days(date_obj, 6)
		return f"{date_obj.strftime('%d %b')} - {end.strftime('%d %b')}"
	return date_obj.strftime("%d %b")


def _bucket_sequence(from_date, to_date, bucket):
	sequence = []
	current = getdate(from_date)
	if bucket == "week":
		current = _week_bucket_start(current)
		while current <= to_date:
			sequence.append(current)
			current = add_days(current, 7)
		return sequence
	if bucket == "month":
		current = _month_bucket_start(current)
		while current <= to_date:
			sequence.append(current)
			current = getdate(add_months(current, 1))
		return sequence
	while current <= to_date:
		sequence.append(current)
		current = add_days(current, 1)
	return sequence


def _dashboard_cards(rows):
	created = len(rows)
	pending = 0
	on_hold = 0
	resolved = 0
	closed = 0
	replied = 0

	for row in rows:
		is_on_hold = bool(int(row.get("custom_is_on_hold") or 0))
		status = row.get("status")
		if is_on_hold:
			on_hold += 1
		if status in OPEN_STATUSES:
			pending += 1
		if status == "Replied":
			replied += 1
		if status == "Resolved":
			resolved += 1
		if status == "Closed":
			closed += 1

	return {
		"total": created,
		"created": created,
		"pending": pending,
		"on_hold": on_hold,
		"resolved": resolved,
		"closed": closed,
		"replied": replied,
	}


def _ticket_type_breakdown(rows):
	counts = defaultdict(int)
	for row in rows:
		counts[row.get("ticket_type") or "Not Set"] += 1
	return [
		{"name": name, "value": value}
		for name, value in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
	]


def _status_trend(rows, from_date, to_date, bucket):
	buckets = {
		key: {
			"key": str(key),
			"label": _bucket_label(key, bucket),
			"open": 0,
			"replied": 0,
			"on_hold": 0,
			"resolved": 0,
			"closed": 0,
		}
		for key in _bucket_sequence(from_date, to_date, bucket)
	}

	for row in rows:
		date_obj = get_datetime(row.creation).date()
		key = _bucket_key(date_obj, bucket)
		if key not in buckets:
			continue
		status = row.get("status")
		if bool(int(row.get("custom_is_on_hold") or 0)):
			buckets[key]["on_hold"] += 1
		elif status == "Closed":
			buckets[key]["closed"] += 1
		elif status == "Resolved":
			buckets[key]["resolved"] += 1
		elif status == "Replied":
			buckets[key]["replied"] += 1
		else:
			buckets[key]["open"] += 1

	return list(buckets.values())


@frappe.whitelist()
def get_dashboard_summary(range="week", from_date=None, to_date=None):
	from_date, to_date, range_label, bucket = _dashboard_range(range, from_date, to_date)
	rows = _dashboard_rows(from_date, to_date)

	return {
		"range": range,
		"range_label": range_label,
		"from_date": str(from_date),
		"to_date": str(to_date),
		"bucket": bucket,
		"cards": _dashboard_cards(rows),
		"ticket_type_breakdown": _ticket_type_breakdown(rows),
		"status_trend": _status_trend(rows, from_date, to_date, bucket),
	}


@frappe.whitelist()
def get_agents():
	return list(_agent_map().values())


@frappe.whitelist()
def get_sidebar_profile():
	user = frappe.get_doc("User", frappe.session.user)
	return {
		"name": user.name,
		"full_name": user.full_name,
		"email": user.email,
		"username": user.username,
		"user_image": user.user_image,
	}


@frappe.whitelist()
def get_ticket_types():
	return _ticket_type_options()


@frappe.whitelist()
def get_agent_candidates():
	return _agent_candidates()


@frappe.whitelist()
def get_ticket_detail(name):
	row = frappe.get_list(
		TICKET_DOCTYPE,
		fields=_ticket_fields(),
		filters={"name": name},
		page_length=1,
	)
	if not row:
		frappe.throw(_("Ticket not found"), frappe.DoesNotExistError)

	return {
		**get_ticket_doc(name),
		**_decorate_ticket(row[0]),
	}


@frappe.whitelist()
def create_agent(user):
	if not user:
		frappe.throw(_("Please select a user"))
	if frappe.db.exists("HD Agent", user):
		return frappe.get_doc("HD Agent", user)
	if not frappe.db.exists("User", user):
		frappe.throw(_("Selected user does not exist"))

	doc = frappe.get_doc(
		{
			"doctype": "HD Agent",
			"user": user,
			"is_active": 1,
		}
	).insert()
	return doc


@frappe.whitelist()
def create_ticket(subject, raised_by, message, priority=None, ticket_type=None, assignee=None):
	if not subject:
		frappe.throw(_("Please enter a subject"))
	if not raised_by:
		frappe.throw(_("Please enter customer email"))
	if not message:
		frappe.throw(_("Please enter email message"))

	doc = frappe.get_doc(
		{
			"doctype": TICKET_DOCTYPE,
			"subject": subject,
			"raised_by": raised_by,
			"description": message,
			"priority": priority or None,
			"ticket_type": ticket_type or None,
		}
	).insert()

	if assignee:
		try:
			assign_ticket_to_agent(doc.name, assignee)
			doc.reload()
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Unity Helpdesk create_ticket assign_agent")

	if priority is not None:
		frappe.db.set_value(
			TICKET_DOCTYPE,
			doc.name,
			"priority",
			priority or None,
			update_modified=False,
		)
	if ticket_type is not None:
		frappe.db.set_value(
			TICKET_DOCTYPE,
			doc.name,
			"ticket_type",
			ticket_type or None,
			update_modified=False,
		)

	try:
		doc.reply_via_agent(message=message)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Unity Helpdesk create_ticket reply_via_agent")

	try:
		row = frappe.get_list(TICKET_DOCTYPE, fields=_ticket_fields(), filters={"name": doc.name}, page_length=1)
		return _decorate_ticket(row[0]) if row else {"name": doc.name}
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Unity Helpdesk create_ticket response")
		return {"name": doc.name, "subject": doc.subject, "raised_by": doc.raised_by}


@frappe.whitelist()
def update_ticket(
	name,
	assignee=None,
	status=None,
	priority=None,
	ticket_type=None,
	is_on_hold=None,
	hold_from=None,
	hold_to=None,
	hold_reason=None,
):
	ticket = frappe.get_doc(TICKET_DOCTYPE, name)
	explicit_priority = priority if priority is not None else None
	explicit_ticket_type = ticket_type if ticket_type is not None else None
	on_hold_selected = status == "On Hold"
	previous_hold_reason = ticket.get("custom_hold_reason") if _has_field(TICKET_DOCTYPE, "custom_hold_reason") else None

	if assignee is not None:
		if assignee:
			assign_ticket_to_agent(name, assignee)
		else:
			clear_all_assignments(TICKET_DOCTYPE, name)

	if status:
		if on_hold_selected:
			if _has_field(TICKET_DOCTYPE, "custom_is_on_hold"):
				ticket.custom_is_on_hold = 1
			if ticket.status in FINAL_STATUSES or not ticket.status:
				ticket.status = "Open"
		elif status not in STATUS_OPTIONS:
			frappe.throw(_("Invalid ticket status"))
		else:
			ticket.status = status
			if _has_field(TICKET_DOCTYPE, "custom_is_on_hold"):
				ticket.custom_is_on_hold = 0

	if priority:
		ticket.priority = priority

	if ticket_type is not None:
		ticket.ticket_type = ticket_type or None

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
	if explicit_priority is not None:
		frappe.db.set_value(
			TICKET_DOCTYPE,
			name,
			"priority",
			explicit_priority or None,
			update_modified=False,
		)
	if explicit_ticket_type is not None:
		frappe.db.set_value(
			TICKET_DOCTYPE,
			name,
			"ticket_type",
			explicit_ticket_type or None,
			update_modified=False,
		)
	if hold_reason is not None and hold_reason != (previous_hold_reason or ""):
		_log_hold_reason(name, hold_reason)
	row = frappe.get_list(TICKET_DOCTYPE, fields=_ticket_fields(), filters={"name": name}, page_length=1)
	return _decorate_ticket(row[0]) if row else {}


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
