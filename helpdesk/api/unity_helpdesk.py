import html
import json
import re
from collections import defaultdict

import frappe
from frappe import _
from frappe.desk.form.assign_to import clear as clear_all_assignments
from frappe.utils import add_days, add_months, cstr, get_datetime, get_first_day, get_last_day, get_url, getdate, nowdate

from helpdesk.api.ticket import assign_ticket_to_agent
from helpdesk.helpdesk.doctype.hd_ticket.api import get_one as get_ticket_doc


TICKET_DOCTYPE = "HD Ticket"
OPEN_STATUSES = ["Open", "Replied"]
FINAL_STATUSES = ["Resolved", "Closed"]
STATUS_OPTIONS = ["Open", "Replied", "Resolved", "Closed"]
HELPDESK_USER_ROLE = "Helpdesk User"
HELPDESK_ADMIN_ROLE = "Helpdesk Admin"
HELPDESK_SUPER_ADMIN_ROLE = "Helpdesk Super Admin"
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
	# Indexed plain-text search fields (populated by edu_quality override)
	"custom_search_student_names",
	"custom_search_student_refs",
	"custom_search_guardian_emails",
]


def _session_user():
	return frappe.session.user


def _user_roles(user=None):
	return set(frappe.get_roles(user or _session_user()))


def _is_super_admin(user=None):
	user = user or _session_user()
	roles = _user_roles(user)
	return (
		user == "Administrator"
		or "System Manager" in roles
		or HELPDESK_SUPER_ADMIN_ROLE in roles
	)


def _get_capabilities(user=None):
	user = user or _session_user()
	roles = _user_roles(user)
	is_super_admin = _is_super_admin(user)
	is_helpdesk_admin = is_super_admin or HELPDESK_ADMIN_ROLE in roles
	is_helpdesk_user = (
		is_helpdesk_admin
		or HELPDESK_USER_ROLE in roles
		or "Agent" in roles
		or bool(frappe.db.exists("HD Agent", {"name": user}))
	)
	role = ""
	if is_super_admin:
		role = HELPDESK_SUPER_ADMIN_ROLE
	elif is_helpdesk_admin:
		role = HELPDESK_ADMIN_ROLE
	elif is_helpdesk_user:
		role = HELPDESK_USER_ROLE

	return frappe._dict(
		{
			"role": role,
			"can_view_my_tickets": bool(is_helpdesk_user),
			"can_view_all_tickets": bool(is_helpdesk_admin),
			"can_manage_agents": bool(is_super_admin),
			"can_view_dashboard": bool(is_helpdesk_user),
			"can_view_agent_dashboard": bool(is_helpdesk_admin),
			"can_manage_unity_settings": bool(is_super_admin),
		}
	)


def _require_unity_access():
	capabilities = _get_capabilities()
	if not capabilities.can_view_my_tickets:
		frappe.throw(_("You do not have access to Unity Helpdesk"), frappe.PermissionError)
	return capabilities


def _normalize_thread_layout(value):
	value = cstr(value or "").strip()
	if value in {"Detailed", "Classic"}:
		return "Classic"
	if value in {"Compact", "WhatsApp", "Chat Based", "ChatBased", "Chat Based Layout"}:
		return "Chat Based"
	return "Classic"


def _default_thread_layout():
	return _normalize_thread_layout(
		frappe.db.get_single_value("HD Settings", "unity_email_thread_layout")
	)


def _normalize_dashboard_agent(agent, capabilities):
	if not agent:
		return None
	if not capabilities.can_view_agent_dashboard:
		frappe.throw(_("You are not allowed to filter dashboard data by agent"), frappe.PermissionError)
	if not frappe.db.exists("HD Agent", agent):
		frappe.throw(_("Selected agent does not exist"))
	return agent


def _require_ticket_access(name, capabilities=None):
	capabilities = capabilities or _require_unity_access()
	if capabilities.can_view_all_tickets:
		return
	rows = frappe.get_all(
		TICKET_DOCTYPE,
		filters={"name": name, "_assign": ["like", f"%{_session_user()}%"]},
		fields=["name"],
		limit=1,
	)
	if not rows:
		frappe.throw(_("You do not have access to this ticket"), frappe.PermissionError)


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


def _normalize_search_text(value):
	text = html.unescape(cstr(value or ""))
	text = re.sub(r"<[^>]+>", " ", text)
	text = re.sub(r"&nbsp;", " ", text, flags=re.IGNORECASE)
	text = re.sub(r"\s+", " ", text)
	return text.strip().lower()


def _search_tokens(value):
	return [token for token in re.findall(r"[a-z0-9@._-]+", _normalize_search_text(value)) if token]


def _like_pattern(value):
	return f"%{cstr(value or '').strip()}%"


def _student_display_name(student):
	name = cstr(student.get("student_name") or "").strip()
	if name:
		return name
	parts = [
		cstr(student.get("first_name") or "").strip(),
		cstr(student.get("middle_name") or "").strip(),
		cstr(student.get("last_name") or "").strip(),
	]
	return " ".join([part for part in parts if part]).strip()


def _helpdesk_ui():
	return cstr(frappe.db.get_single_value("HD Settings", "helpdesk_ui") or "Default Helpdesk").strip()


def _use_unity_student_context():
	return _helpdesk_ui() == "Unity Helpdesk"


def _has_doctype(doctype):
	return bool(frappe.db.exists("DocType", doctype))


def _docstatus_label(value):
	labels = {0: "Draft", 1: "Submitted", 2: "Cancelled"}
	try:
		return labels.get(int(value), cstr(value))
	except Exception:
		return cstr(value)


def _student_context_dependencies_ready():
	return all(
		_has_doctype(doctype)
		for doctype in [
			"Student",
			"Guardian",
			"Student Guardian",
			"Guardian Student",
			"Program Enrollment",
			"Fees",
			"Academic Year",
		]
	)


def _group_by(items, key):
	grouped = defaultdict(list)
	for item in items:
		grouped[cstr(item.get(key) or "").strip()].append(frappe._dict(item))
	return grouped


def _pick_program_enrollment(rows):
	if not rows:
		return None, []

	sorted_rows = sorted(
		[frappe._dict(row) for row in rows],
		key=lambda row: (get_datetime(row.get("modified")), cstr(row.get("name"))),
		reverse=True,
	)
	submitted = [row for row in sorted_rows if int(row.get("docstatus") or 0) == 1]
	selected = submitted[0] if submitted else sorted_rows[0]
	status_messages = []
	if not submitted:
		status_messages.append("Current-year enrollment is not submitted yet")
	return selected, status_messages


def _pick_fee_record(rows, enrollment_name):
	if not rows:
		return None

	sorted_rows = sorted(
		[frappe._dict(row) for row in rows],
		key=lambda row: (get_datetime(row.get("modified")), cstr(row.get("name"))),
		reverse=True,
	)

	if enrollment_name:
		matching_enrollment = [
			row for row in sorted_rows if cstr(row.get("program_enrollment")) == cstr(enrollment_name)
		]
		submitted_matching = [
			row for row in matching_enrollment if int(row.get("docstatus") or 0) == 1
		]
		if submitted_matching:
			return submitted_matching[0]
		if matching_enrollment:
			return matching_enrollment[0]

	submitted_rows = [row for row in sorted_rows if int(row.get("docstatus") or 0) == 1]
	return submitted_rows[0] if submitted_rows else sorted_rows[0]


def _next_payment_schedule(payment_schedule_rows):
	if not payment_schedule_rows:
		return None

	rows = sorted(
		[frappe._dict(row) for row in payment_schedule_rows],
		key=lambda row: (
			getdate(row.get("due_date")) if row.get("due_date") else getdate("2099-12-31"),
			cstr(row.get("payment_term") or ""),
		),
	)
	outstanding_rows = [row for row in rows if float(row.get("outstanding") or 0) > 0]
	return (outstanding_rows or rows)[0]


def get_student_context_for_ticket(ticket_name=None, raised_by=None):
	raised_by = cstr(raised_by or "").strip().lower()
	result = {
		"match_type": "unmatched",
		"matched_email": raised_by,
		"message": "This email is not in our records" if raised_by else "This email is not in our records",
		"current_academic_year": None,
		"siblings_present": False,
		"primary_students": [],
		"students": [],
		"lookup_meta": {
			"ticket_name": ticket_name,
			"primary_student_count": 0,
			"resolved_student_count": 0,
		},
	}

	if not raised_by:
		result["message"] = "This email is not in our records"
		return result

	if not _student_context_dependencies_ready():
		result["message"] = "Student data is unavailable"
		return result

	current_year = frappe.db.get_value(
		"Academic Year",
		{"custom_current_academic_year": 1},
		"name",
	)
	result["current_academic_year"] = current_year

	primary_student_ids = frappe.get_all(
		"Student",
		filters={"user": raised_by},
		pluck="name",
		page_length=0,
	)

	matched_guardians = []
	match_type = "student" if primary_student_ids else "unmatched"

	if not primary_student_ids:
		matched_guardians = {
			row.name: frappe._dict(row)
			for row in (
				frappe.get_all(
					"Guardian",
					fields=["name", "guardian_name", "email_address", "user"],
					filters={"email_address": raised_by},
					page_length=0,
				)
				+ frappe.get_all(
					"Guardian",
					fields=["name", "guardian_name", "email_address", "user"],
					filters={"user": raised_by},
					page_length=0,
				)
			)
		}
		if matched_guardians:
			match_type = "guardian"
			primary_student_ids = sorted(
				{
					*{
						cstr(row.student).strip()
						for row in frappe.get_all(
							"Guardian Student",
							fields=["student"],
							filters={
								"parenttype": "Guardian",
								"parent": ["in", list(matched_guardians.keys())],
							},
							page_length=0,
						)
						if cstr(row.student).strip()
					},
					*{
						cstr(row.parent).strip()
						for row in frappe.get_all(
							"Student Guardian",
							fields=["parent"],
							filters={
								"parenttype": "Student",
								"guardian": ["in", list(matched_guardians.keys())],
							},
							page_length=0,
						)
						if cstr(row.parent).strip()
					},
				}
			)

	if not primary_student_ids:
		return result

	primary_student_ids = sorted(set(primary_student_ids))
	primary_student_snapshot = {
		row.name: frappe._dict(row)
		for row in frappe.get_all(
			"Student",
			fields=["name", "is_sibling_in_school"],
			filters={"name": ["in", primary_student_ids]},
			page_length=0,
		)
	}
	primary_guardian_rows = frappe.get_all(
		"Student Guardian",
		fields=["parent", "guardian", "guardian_name", "email"],
		filters={"parenttype": "Student", "parent": ["in", primary_student_ids]},
		page_length=0,
	)
	primary_guardians_by_student = _group_by(primary_guardian_rows, "parent")
	guardian_ids = sorted(
		{
			cstr(row.guardian).strip()
			for row in primary_guardian_rows
			if cstr(row.guardian).strip()
		}
	)
	guardian_docs = {
		row.name: frappe._dict(row)
		for row in frappe.get_all(
			"Guardian",
			fields=["name", "guardian_name", "email_address", "user"],
			filters={"name": ["in", guardian_ids]} if guardian_ids else {"name": "__none"},
			page_length=0,
		)
	}
	guardian_students = frappe.get_all(
		"Guardian Student",
		fields=["parent", "student"],
		filters={"parenttype": "Guardian", "parent": ["in", guardian_ids]} if guardian_ids else {"name": "__none"},
		page_length=0,
	)
	students_by_guardian = _group_by(guardian_students, "parent")
	student_sibling_rows = frappe.get_all(
		"Student Guardian",
		fields=["parent", "guardian"],
		filters={"parenttype": "Student", "guardian": ["in", guardian_ids]} if guardian_ids else {"name": "__none"},
		page_length=0,
	)
	student_rows_by_guardian = _group_by(student_sibling_rows, "guardian")

	sibling_ids = set()
	for student_id in primary_student_ids:
		student_snapshot = primary_student_snapshot.get(student_id) or {}
		guardians_for_student = primary_guardians_by_student.get(student_id, [])
		has_guardian_linked_siblings = False
		for guardian_row in guardians_for_student:
			linked_students = students_by_guardian.get(cstr(guardian_row.get("guardian")), [])
			for linked_row in linked_students:
				linked_student_id = cstr(linked_row.get("student")).strip()
				if linked_student_id and linked_student_id != student_id:
					has_guardian_linked_siblings = True
					sibling_ids.add(linked_student_id)
			for sibling_row in student_rows_by_guardian.get(cstr(guardian_row.get("guardian")), []):
				linked_student_id = cstr(sibling_row.get("parent")).strip()
				if linked_student_id and linked_student_id != student_id:
					has_guardian_linked_siblings = True
					sibling_ids.add(linked_student_id)

		if has_guardian_linked_siblings and not int(student_snapshot.get("is_sibling_in_school") or 0):
			student_snapshot["is_sibling_in_school"] = 1


	all_student_ids = sorted(set(primary_student_ids) | sibling_ids)
	student_rows = frappe.get_all(
		"Student",
		fields=[
			"name",
			"student_name",
			"first_name",
			"middle_name",
			"last_name",
			"reference_number",
			"user",
			"school",
			"program",
			"custom_division",
			"student_status",
			"student_mobile_number",
			"primary_contact",
			"whatsapp_number",
			"is_sibling_in_school",
		],
		filters={"name": ["in", all_student_ids]},
		page_length=0,
	)
	students_by_id = {
		row.name: frappe._dict(row)
		for row in student_rows
	}

	all_guardian_rows = frappe.get_all(
		"Student Guardian",
		fields=["parent", "guardian", "guardian_name", "email"],
		filters={"parenttype": "Student", "parent": ["in", all_student_ids]},
		page_length=0,
	)
	guardians_by_student = _group_by(all_guardian_rows, "parent")
	all_guardian_ids = sorted(
		{
			cstr(row.guardian).strip()
			for row in all_guardian_rows
			if cstr(row.guardian).strip()
		}
	)
	if all_guardian_ids:
		for row in frappe.get_all(
			"Guardian",
			fields=["name", "guardian_name", "email_address", "user"],
			filters={"name": ["in", all_guardian_ids]},
			page_length=0,
		):
			guardian_docs[row.name] = frappe._dict(row)

	enrollment_rows = frappe.get_all(
		"Program Enrollment",
		fields=[
			"name",
			"student",
			"program",
			"custom_school",
			"academic_year",
			"payment_plan",
			"workflow_state",
			"docstatus",
			"modified",
		],
		filters={"student": ["in", all_student_ids], "academic_year": current_year}
		if current_year
		else {"name": "__none"},
		page_length=0,
		order_by="modified desc",
	)
	enrollments_by_student = _group_by(enrollment_rows, "student")

	fee_rows = frappe.get_all(
		"Fees",
		fields=[
			"name",
			"student",
			"student_name",
			"program_enrollment",
			"payment_plan",
			"grand_total",
			"outstanding_amount",
			"due_date",
			"academic_year",
			"docstatus",
			"modified",
		],
		filters={"student": ["in", all_student_ids], "academic_year": current_year}
		if current_year
		else {"name": "__none"},
		page_length=0,
		order_by="modified desc",
	)
	fees_by_student = _group_by(fee_rows, "student")
	payment_schedule_rows = frappe.get_all(
		"Payment Schedule",
		fields=[
			"parent",
			"payment_term",
			"description",
			"due_date",
			"payment_amount",
			"outstanding",
			"payment_status",
			"idx",
		],
		filters={"parent": ["in", [row.name for row in fee_rows]]} if fee_rows else {"name": "__none"},
		page_length=0,
	)
	payment_schedule_by_fee = _group_by(payment_schedule_rows, "parent")

	student_cards = []
	for student_id in all_student_ids:
		student = students_by_id.get(student_id)
		if not student:
			continue

		status_messages = []
		selected_enrollment, enrollment_messages = _pick_program_enrollment(
			enrollments_by_student.get(student_id, [])
		)
		status_messages.extend(enrollment_messages)
		if not selected_enrollment:
			status_messages.append("No current-year enrollment found")

		selected_fee = _pick_fee_record(
			fees_by_student.get(student_id, []),
			selected_enrollment.get("name") if selected_enrollment else None,
		)
		if not selected_fee:
			status_messages.append("No fees record found for current-year enrollment")
		elif (
			selected_enrollment
			and cstr(selected_fee.get("program_enrollment")).strip()
			and cstr(selected_fee.get("program_enrollment")).strip() != cstr(selected_enrollment.get("name")).strip()
		):
			status_messages.append("Fees record is not linked to the selected current-year enrollment")

		student_guardian_rows = guardians_by_student.get(student_id, [])
		student_guardian_ids = [
			cstr(row.get("guardian")).strip()
			for row in student_guardian_rows
			if cstr(row.get("guardian")).strip()
		]
		guardian_names = []
		guardian_emails = []
		for guardian_id in student_guardian_ids:
			guardian_doc = guardian_docs.get(guardian_id) or {}
			guardian_name = cstr(
				guardian_doc.get("guardian_name")
				or guardian_doc.get("name")
			).strip()
			if guardian_name:
				guardian_names.append(guardian_name)
			for email_value in [
				guardian_doc.get("email_address"),
				guardian_doc.get("user"),
			]:
				email_text = cstr(email_value).strip().lower()
				if email_text:
					guardian_emails.append(email_text)

		next_schedule = _next_payment_schedule(
			payment_schedule_by_fee.get(selected_fee.get("name"), []) if selected_fee else []
		)
		student_cards.append(
			{
				"student_id": student_id,
				"student_name": _student_display_name(student),
				"is_primary_match": student_id in primary_student_ids,
				"is_sibling": student_id not in primary_student_ids,
				"school": student.get("school"),
				"class_program": student.get("program"),
				"division": student.get("custom_division"),
				"student_status": student.get("student_status"),
				"student_mobile_number": student.get("student_mobile_number"),
				"primary_contact": student.get("primary_contact"),
				"whatsapp_number": student.get("whatsapp_number"),
				"is_sibling_in_school": bool(int(student.get("is_sibling_in_school") or 0)),
				"guardian_ids": student_guardian_ids,
				"guardian_names": sorted(set(guardian_names)),
				"guardian_emails": sorted(set(guardian_emails)),
				"reference_number": student.get("reference_number"),
				"enrollment": (
					{
						"name": selected_enrollment.get("name"),
						"program": selected_enrollment.get("program"),
						"school": selected_enrollment.get("custom_school") or student.get("school"),
						"academic_year": selected_enrollment.get("academic_year"),
						"payment_plan": selected_enrollment.get("payment_plan"),
						"workflow_state": selected_enrollment.get("workflow_state"),
						"docstatus": int(selected_enrollment.get("docstatus") or 0),
						"docstatus_label": _docstatus_label(selected_enrollment.get("docstatus")),
					}
					if selected_enrollment
					else None
				),
				"fees": (
					{
						"name": selected_fee.get("name"),
						"payment_plan": selected_fee.get("payment_plan")
						or (selected_enrollment.get("payment_plan") if selected_enrollment else ""),
						"grand_total": selected_fee.get("grand_total"),
						"outstanding_amount": selected_fee.get("outstanding_amount"),
						"paid_amount": float(selected_fee.get("grand_total") or 0)
						- float(selected_fee.get("outstanding_amount") or 0),
						"due_date": selected_fee.get("due_date"),
						"docstatus": int(selected_fee.get("docstatus") or 0),
						"docstatus_label": _docstatus_label(selected_fee.get("docstatus")),
						"academic_year": selected_fee.get("academic_year"),
						"link": get_url(f"/app/fees/{selected_fee.get('name')}"),
						"next_payment": (
							{
								"payment_term": next_schedule.get("payment_term"),
								"description": next_schedule.get("description"),
								"due_date": next_schedule.get("due_date"),
								"payment_amount": next_schedule.get("payment_amount"),
								"outstanding": next_schedule.get("outstanding"),
								"payment_status": next_schedule.get("payment_status"),
							}
							if next_schedule
							else None
						),
					}
					if selected_fee
					else None
				),
				"status_messages": status_messages,
			}
		)

	result.update(
		{
			"match_type": match_type,
			"message": None,
			"primary_students": primary_student_ids,
			"siblings_present": bool(sibling_ids),
			"students": student_cards,
			"lookup_meta": {
				"ticket_name": ticket_name,
				"primary_student_count": len(primary_student_ids),
				"resolved_student_count": len(student_cards),
			},
		}
	)
	return result


def populate_ticket_student_search_fields(ticket):
	ticket_doc = frappe.get_doc(TICKET_DOCTYPE, ticket) if isinstance(ticket, str) else ticket
	if not ticket_doc or not cstr(ticket_doc.get("raised_by")).strip():
		return {}

	context = get_student_context_for_ticket(ticket_doc.name, ticket_doc.raised_by)
	search_update = {}
	if frappe.db.has_column(TICKET_DOCTYPE, "custom_search_student_names"):
		search_update["custom_search_student_names"] = ", ".join(
			sorted(
				{
					cstr(student.get("student_name")).strip()
					for student in context.get("students", [])
					if cstr(student.get("student_name")).strip()
				}
			)
		)
	if frappe.db.has_column(TICKET_DOCTYPE, "custom_search_student_refs"):
		search_update["custom_search_student_refs"] = ", ".join(
			sorted(
				{
					cstr(student.get("reference_number")).strip()
					for student in context.get("students", [])
					if cstr(student.get("reference_number")).strip()
				}
			)
		)
	if frappe.db.has_column(TICKET_DOCTYPE, "custom_search_guardian_emails"):
		emails = {
			cstr(ticket_doc.get("raised_by")).strip().lower()
			for _ in [0]
			if cstr(ticket_doc.get("raised_by")).strip()
		}
		for student in context.get("students", []):
			for email_value in student.get("guardian_emails", []):
				email_text = cstr(email_value).strip().lower()
				if email_text:
					emails.add(email_text)
		search_update["custom_search_guardian_emails"] = ", ".join(sorted(emails))
	if search_update:
		frappe.db.set_value(TICKET_DOCTYPE, ticket_doc.name, search_update, update_modified=False)
	return context


def _identity_bundle_for_tickets(ticket_rows):
	ticket_rows = [frappe._dict(row) for row in ticket_rows]
	raised_bys = sorted(
		{
			cstr(row.get("raised_by") or "").strip()
			for row in ticket_rows
			if cstr(row.get("raised_by") or "").strip()
		}
	)
	if not raised_bys:
		return {}

	student_fields = [
		"name",
		"user",
		"reference_number",
		"student_name",
		"first_name",
		"middle_name",
		"last_name",
	]
	students_by_id = {}
	email_to_student_ids = defaultdict(set)

	for student in frappe.get_all(
		"Student",
		fields=student_fields,
		filters={"user": ["in", raised_bys]},
		page_length=0,
	):
		student = frappe._dict(student)
		students_by_id[student.name] = student
		if student.get("user"):
			email_to_student_ids[cstr(student.user).strip().lower()].add(student.name)

	guardians = frappe.get_all(
		"Guardian",
		fields=["name", "email_address"],
		filters={"email_address": ["in", raised_bys]},
		page_length=0,
	)
	guardian_email_by_id = {
		row.name: cstr(row.email_address or "").strip().lower()
		for row in guardians
		if cstr(row.email_address or "").strip()
	}
	guardian_names = list(guardian_email_by_id)

	student_guardian_rows = []
	if guardian_names:
		student_guardian_rows.extend(
			frappe.get_all(
				"Student Guardian",
				fields=["parent", "guardian"],
				filters={"parenttype": "Student", "guardian": ["in", guardian_names]},
				page_length=0,
			)
		)
		for row in student_guardian_rows:
			guardian_email = guardian_email_by_id.get(row.guardian)
			if guardian_email:
				email_to_student_ids[guardian_email].add(row.parent)

	student_ids = sorted({student_id for ids in email_to_student_ids.values() for student_id in ids})
	missing_student_ids = [student_id for student_id in student_ids if student_id not in students_by_id]
	if missing_student_ids:
		for student in frappe.get_all(
			"Student",
			fields=student_fields,
			filters={"name": ["in", missing_student_ids]},
			page_length=0,
		):
			students_by_id[student.name] = frappe._dict(student)

	all_student_ids = sorted(students_by_id)
	if all_student_ids:
		student_guardian_rows = frappe.get_all(
			"Student Guardian",
			fields=["parent", "guardian"],
			filters={"parenttype": "Student", "parent": ["in", all_student_ids]},
			page_length=0,
		)
		missing_guardians = sorted({row.guardian for row in student_guardian_rows if row.guardian not in guardian_email_by_id})
		if missing_guardians:
			for guardian in frappe.get_all(
				"Guardian",
				fields=["name", "email_address"],
				filters={"name": ["in", missing_guardians]},
				page_length=0,
			):
				guardian_email_by_id[guardian.name] = cstr(guardian.email_address or "").strip().lower()

	student_to_guardian_emails = defaultdict(set)
	for row in student_guardian_rows:
		guardian_email = guardian_email_by_id.get(row.guardian)
		if guardian_email:
			student_to_guardian_emails[row.parent].add(guardian_email)

	result = {}
	for email in raised_bys:
		email_key = email.strip().lower()
		student_ids_for_email = sorted(email_to_student_ids.get(email_key, set()))
		students = [students_by_id[student_id] for student_id in student_ids_for_email if student_id in students_by_id]
		student_refs = []
		student_names = []
		guardian_emails = set()
		for student in students:
			reference_number = cstr(student.get("reference_number") or "").strip()
			if reference_number:
				student_refs.append(reference_number)
			display_name = _student_display_name(student)
			if display_name:
				student_names.append(display_name)
			guardian_emails.update(student_to_guardian_emails.get(student.name, set()))
		if email_key:
			guardian_emails.add(email_key)
		result[email_key] = {
			"student_refs": sorted(set(student_refs)),
			"student_names": sorted(set(student_names)),
			"guardian_emails": sorted(set(guardian_emails)),
		}
	return result


def _communication_text_map(ticket_names):
	"""Return a map of ticket_name → normalized combined communication text.

	Results are cached in Redis for 5 minutes (TTL matches Frappe's prompt cache window).
	Individual ticket entries are cached separately so a single new reply only invalidates
	that ticket's entry rather than the whole batch.
	"""
	if not ticket_names:
		return {}

	result = {}
	missing = []
	for name in ticket_names:
		cache_key = f"helpdesk_comm_text:{name}"
		cached = frappe.cache().get_value(cache_key)
		if cached is not None:
			result[name] = cached
		else:
			missing.append(name)

	if not missing:
		return result

	content_map = defaultdict(list)
	for row in frappe.get_all(
		"Communication",
		fields=["reference_name", "subject", "content"],
		filters={"reference_doctype": TICKET_DOCTYPE, "reference_name": ["in", missing]},
		page_length=0,
		order_by="creation desc",
	):
		content_map[row.reference_name].append(_normalize_search_text(row.subject))
		content_map[row.reference_name].append(_normalize_search_text(row.content))

	for row in frappe.get_all(
		"HD Ticket Comment",
		fields=["reference_ticket", "content"],
		filters={"reference_ticket": ["in", missing]},
		page_length=0,
		order_by="creation desc",
	):
		content_map[row.reference_ticket].append(_normalize_search_text(row.content))

	for name in missing:
		text = " ".join(part for part in content_map.get(name, []) if part)
		frappe.cache().set_value(f"helpdesk_comm_text:{name}", text, expires_in_sec=300)
		result[name] = text

	return result


def _invalidate_communication_cache(ticket_name):
	"""Call this whenever a new Communication or Comment is added to a ticket."""
	frappe.cache().delete_value(f"helpdesk_comm_text:{ticket_name}")


def _split_search_field(raw):
	"""Split a comma-separated plain-text search index field into a normalized list."""
	if not raw:
		return []
	return [_normalize_search_text(v) for v in cstr(raw).split(",") if v.strip()]


def _ticket_search_documents(ticket_rows):
	ticket_rows = [frappe._dict(row) for row in ticket_rows]
	communication_map = _communication_text_map([row.name for row in ticket_rows])

	# Legacy HTML blob fields still contribute to full-text content_text ranking
	# (Tier 6), but student/guardian identity now comes from the pre-computed
	# indexed fields so we skip the expensive _identity_bundle_for_tickets() call.
	legacy_content_fields = [
		field
		for field in [
			"custom_list_of_student",
			"custom_all_fees_details_of_students",
			"custom_payment_schedule",
			"custom_student_remark",
			"custom_previous_ticket_details",
		]
		if ticket_rows and field in ticket_rows[0].keys()
	]

	documents = {}
	for row in ticket_rows:
		raised_by = cstr(row.get("raised_by") or "").strip().lower()

		# Read pre-computed plain-text identity data directly from the ticket row.
		# Falls back to empty list when the column is not yet present (before migration).
		student_refs = _split_search_field(row.get("custom_search_student_refs"))
		student_names = _split_search_field(row.get("custom_search_student_names"))
		guardian_emails = _split_search_field(row.get("custom_search_guardian_emails"))

		# Always include raised_by in guardian_emails so exact-email search (Tier 2) works
		if raised_by and raised_by not in guardian_emails:
			guardian_emails.append(raised_by)

		legacy_parts = [_normalize_search_text(row.get(field)) for field in legacy_content_fields]
		subject_text = _normalize_search_text(row.get("subject"))
		description_text = _normalize_search_text(row.get("description"))
		content_text = " ".join(
			part
			for part in [subject_text, description_text, *legacy_parts, communication_map.get(row.name, "")]
			if part
		)
		documents[row.name] = {
			"name": row.name,
			"modified": row.get("modified"),
			"ticket_id": _normalize_search_text(row.name),
			"raised_by": raised_by,
			"student_refs": student_refs,
			"student_names": student_names,
			"guardian_emails": guardian_emails,
			"subject_text": subject_text,
			"content_text": content_text,
		}
	return documents


def _rank_ticket_document(document, query, tokens):
	if not query:
		return None

	def prefix_rank(values):
		lengths = [len(value) for value in values if value and value.startswith(query)]
		return min(lengths) if lengths else None

	ticket_id = document.get("ticket_id")
	student_refs = document.get("student_refs", [])
	identity_emails = [document.get("raised_by")] + document.get("guardian_emails", [])
	identity_emails = [value for value in identity_emails if value]
	student_names = document.get("student_names", [])
	subject_text = document.get("subject_text", "")
	content_text = document.get("content_text", "")

	if ticket_id == query:
		return (0, 0)
	if query in student_refs:
		return (1, 0)
	if query in identity_emails:
		return (2, 0)

	prefix_lengths = [
		value
		for value in [
			prefix_rank([ticket_id] if ticket_id else []),
			prefix_rank(student_refs),
			prefix_rank(identity_emails),
		]
		if value is not None
	]
	if prefix_lengths:
		return (3, min(prefix_lengths))

	if tokens and student_names:
		name_scores = []
		for student_name in student_names:
			if all(token in student_name for token in tokens):
				name_scores.append(len(student_name))
		if name_scores:
			return (4, min(name_scores))

	if query and (query in subject_text or query in content_text):
		location_score = 0 if query in subject_text else 1
		return (5, location_score)

	if tokens and all(token in content_text for token in tokens):
		return (6, len(content_text))

	return None


def _ranked_ticket_ids(ticket_rows, search):
	query = _normalize_search_text(search)
	tokens = _search_tokens(search)
	documents = _ticket_search_documents(ticket_rows)
	ranked = []
	for row in ticket_rows:
		document = documents.get(row.name)
		rank = _rank_ticket_document(document, query, tokens) if document else None
		if rank is None:
			continue
		ranked.append((rank, get_datetime(row.modified), row.name))

	ranked.sort(key=lambda item: (item[0][0], item[0][1], -item[1].timestamp(), item[2]))
	return [name for _, __, name in ranked]


def _merge_filters(base_filters, extra_filters=None):
	return list(base_filters or []) + list(extra_filters or [])


def _append_ticket_names(target, rows):
	for row in rows or []:
		name = cstr((row.get("name") if isinstance(row, dict) else getattr(row, "name", None)) or "").strip()
		if name:
			target.add(name)


def _related_emails_for_search(search):
	query = cstr(search or "").strip()
	if not query:
		return set()

	student_filters = [
		["Student", "reference_number", "=", query],
		["Student", "reference_number", "like", _like_pattern(query)],
		["Student", "student_name", "like", _like_pattern(query)],
		["Student", "first_name", "like", _like_pattern(query)],
		["Student", "middle_name", "like", _like_pattern(query)],
		["Student", "last_name", "like", _like_pattern(query)],
	]
	students = frappe.get_all(
		"Student",
		fields=["name", "user"],
		filters=[],
		or_filters=student_filters,
		page_length=200,
	)
	student_ids = [row.name for row in students if row.name]
	emails = {
		cstr(row.user).strip().lower()
		for row in students
		if cstr(row.user or "").strip()
	}

	guardian_names = set()
	if student_ids:
		for row in frappe.get_all(
			"Student Guardian",
			fields=["guardian"],
			filters={"parenttype": "Student", "parent": ["in", student_ids]},
			page_length=0,
		):
			if row.guardian:
				guardian_names.add(row.guardian)

	guardian_filters = [
		["Guardian", "email_address", "=", query],
		["Guardian", "email_address", "like", _like_pattern(query)],
	]
	for guardian in frappe.get_all(
		"Guardian",
		fields=["name", "email_address"],
		filters=[],
		or_filters=guardian_filters,
		page_length=200,
	):
		if guardian.name:
			guardian_names.add(guardian.name)
		if cstr(guardian.email_address or "").strip():
			emails.add(cstr(guardian.email_address).strip().lower())

	if guardian_names:
		for guardian in frappe.get_all(
			"Guardian",
			fields=["name", "email_address"],
			filters={"name": ["in", list(guardian_names)]},
			page_length=0,
		):
			if cstr(guardian.email_address or "").strip():
				emails.add(cstr(guardian.email_address).strip().lower())
		for row in frappe.get_all(
			"Student Guardian",
			fields=["parent"],
			filters={"parenttype": "Student", "guardian": ["in", list(guardian_names)]},
			page_length=0,
		):
			if row.parent:
				student_ids.append(row.parent)

	if student_ids:
		for student in frappe.get_all(
			"Student",
			fields=["user"],
			filters={"name": ["in", list(set(student_ids))]},
			page_length=0,
		):
			if cstr(student.user or "").strip():
				emails.add(cstr(student.user).strip().lower())

	return emails


def _search_candidate_ticket_names(base_filters, search):
	query = cstr(search or "").strip()
	if not query:
		return set()

	candidate_names = set()

	# Core ticket fields + indexed plain-text search fields (fast — B-tree indexed).
	# Prefer the indexed custom_search_* fields over the HTML Long Text blobs so the DB
	# can use its index for prefix scans and avoid full-table scans on large text columns.
	search_fields = ["name", "subject", "raised_by", "description"]
	for field in [
		"custom_search_student_names",   # indexed Small Text — student display names
		"custom_search_student_refs",    # indexed Small Text — student reference numbers
		"custom_search_guardian_emails", # indexed Small Text — guardian emails
	]:
		if _has_field(TICKET_DOCTYPE, field):
			search_fields.append(field)

	or_filters = [[TICKET_DOCTYPE, field, "like", _like_pattern(query)] for field in search_fields]

	_append_ticket_names(
		candidate_names,
		frappe.get_list(
			TICKET_DOCTYPE,
			fields=["name"],
			filters=base_filters,
			or_filters=or_filters,
			order_by="modified desc",
			page_length=500,
		),
	)

	# Search inside email body (Communication) and ticket comments.
	# These cannot be indexed the same way, so we search them separately and
	# merge the results into the candidate set.
	communication_names = {
		cstr(row.reference_name or "").strip()
		for row in frappe.get_all(
			"Communication",
			fields=["reference_name"],
			filters={"reference_doctype": TICKET_DOCTYPE, "content": ["like", _like_pattern(query)]},
			page_length=300,
			order_by="creation desc",
		)
		if cstr(row.reference_name or "").strip()
	}
	comment_names = {
		cstr(row.reference_ticket or "").strip()
		for row in frappe.get_all(
			"HD Ticket Comment",
			fields=["reference_ticket"],
			filters={"content": ["like", _like_pattern(query)]},
			page_length=300,
			order_by="creation desc",
		)
		if cstr(row.reference_ticket or "").strip()
	}
	external_names = list(communication_names | comment_names)
	if external_names:
		_append_ticket_names(
			candidate_names,
			frappe.get_list(
				TICKET_DOCTYPE,
				fields=["name"],
				filters=_merge_filters(base_filters, [[TICKET_DOCTYPE, "name", "in", external_names]]),
				order_by="modified desc",
				page_length=500,
			),
		)

	return candidate_names


def _build_filters(view="all", filters=None, assigned_agent=None):
	filters = frappe._dict(_parse_json(filters, {}) or {})
	res = []

	if assigned_agent:
		res.append([TICKET_DOCTYPE, "_assign", "like", f"%{assigned_agent}%"])
	elif view == "my":
		res.append([TICKET_DOCTYPE, "_assign", "like", f"%{_session_user()}%"])

	if filters.get("status"):
		if filters.status == "On Hold" and _has_field(TICKET_DOCTYPE, "custom_is_on_hold"):
			res.append([TICKET_DOCTYPE, "custom_is_on_hold", "=", 1])
		else:
			res.append([TICKET_DOCTYPE, "status", "=", filters.status])

	if filters.get("priority"):
		res.append([TICKET_DOCTYPE, "priority", "=", filters.priority])

	if filters.get("ticket_type"):
		res.append([TICKET_DOCTYPE, "ticket_type", "=", filters.ticket_type])

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


def _dashboard_cards_for_filters(filters=None):
	total = _count(filters)
	on_hold = (
		_count(_merge_filters(filters, [[TICKET_DOCTYPE, "custom_is_on_hold", "=", 1]]))
		if _has_field(TICKET_DOCTYPE, "custom_is_on_hold")
		else 0
	)
	replied = _count(_merge_filters(filters, [[TICKET_DOCTYPE, "status", "=", "Replied"]]))
	resolved = _count(_merge_filters(filters, [[TICKET_DOCTYPE, "status", "=", "Resolved"]]))
	closed = _count(_merge_filters(filters, [[TICKET_DOCTYPE, "status", "=", "Closed"]]))
	pending = _count(
		_merge_filters(filters, [[TICKET_DOCTYPE, "status", "in", OPEN_STATUSES]])
	)
	return {
		"total": total,
		"created": total,
		"pending": pending,
		"on_hold": on_hold,
		"resolved": resolved,
		"closed": closed,
		"replied": replied,
	}


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
	capabilities = _require_unity_access()
	page_length = int(page_length or 20)
	start = int(start or 0)
	effective_view = "all" if view == "all" and capabilities.can_view_all_tickets else "my"
	list_filters = _build_filters(
		"all" if search else effective_view,
		filters,
		assigned_agent=_session_user() if not capabilities.can_view_all_tickets else None,
	)
	fields = _ticket_fields()
	search = cstr(search or "").strip()

	if search:
		candidate_names = _search_candidate_ticket_names(list_filters, search)
		candidate_rows = (
			frappe.get_list(
				TICKET_DOCTYPE,
				fields=fields,
				filters=_merge_filters(list_filters, [[TICKET_DOCTYPE, "name", "in", list(candidate_names)]]),
				order_by="modified desc",
				page_length=max(len(candidate_names), 1),
			)
			if candidate_names
			else []
		)
		ranked_ids = _ranked_ticket_ids(candidate_rows, search)
		paginated_ids = ranked_ids[start : start + page_length]
		row_map = {}
		if paginated_ids:
			row_map = {
				row.name: row
				for row in frappe.get_list(
					TICKET_DOCTYPE,
					fields=fields,
					filters={"name": ["in", paginated_ids]},
					page_length=len(paginated_ids),
				)
			}
		rows = [row_map[name] for name in paginated_ids if name in row_map]
		matched_ids = set(ranked_ids)
		summary_rows = [
			{
				"name": row.name,
				"status": row.status,
				"custom_is_on_hold": row.get("custom_is_on_hold"),
			}
			for row in candidate_rows
			if row.name in matched_ids
		]
		total_count = len(ranked_ids)
		cards = _dashboard_cards(summary_rows)
	else:
		rows = frappe.get_list(
			TICKET_DOCTYPE,
			fields=fields,
			filters=list_filters,
			order_by="modified desc",
			limit_start=start,
			page_length=page_length,
		)
		total_count = _count(list_filters)
		cards = _dashboard_cards_for_filters(list_filters)

	return {
		"data": [_decorate_ticket(row) for row in rows],
		"total_count": total_count,
		"cards": cards,
		"row_count": len(rows),
		"start": start,
		"page_length": page_length,
		"view": effective_view,
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


def _dashboard_rows(from_date, to_date, assigned_agent=None):
	filters = [
		[TICKET_DOCTYPE, "creation", ">=", str(from_date)],
		[TICKET_DOCTYPE, "creation", "<=", f"{to_date} 23:59:59"],
	]
	if assigned_agent:
		filters.append([TICKET_DOCTYPE, "_assign", "like", f"%{assigned_agent}%"])
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
def get_dashboard_summary(range="week", from_date=None, to_date=None, agent=None):
	capabilities = _require_unity_access()
	from_date, to_date, range_label, bucket = _dashboard_range(range, from_date, to_date)
	selected_agent = _session_user() if not capabilities.can_view_all_tickets else _normalize_dashboard_agent(agent, capabilities)
	rows = _dashboard_rows(from_date, to_date, assigned_agent=selected_agent)

	return {
		"range": range,
		"range_label": range_label,
		"from_date": str(from_date),
		"to_date": str(to_date),
		"bucket": bucket,
		"selected_agent": selected_agent,
		"cards": _dashboard_cards(rows),
		"ticket_type_breakdown": _ticket_type_breakdown(rows),
		"status_trend": _status_trend(rows, from_date, to_date, bucket),
	}


@frappe.whitelist()
def get_agents():
	_require_unity_access()
	return list(_agent_map().values())


@frappe.whitelist()
def get_sidebar_profile():
	user = frappe.get_doc("User", _session_user())
	capabilities = _require_unity_access()
	return {
		"name": user.name,
		"full_name": user.full_name,
		"email": user.email,
		"username": user.username,
		"user_image": user.user_image,
		"capabilities": capabilities,
	}


@frappe.whitelist()
def get_ticket_types():
	_require_unity_access()
	return _ticket_type_options()


@frappe.whitelist()
def create_ticket_type(name, description=None, priority=None):
	capabilities = _require_unity_access()
	if not capabilities.can_manage_unity_settings:
		frappe.throw(_("You are not allowed to manage ticket types"), frappe.PermissionError)

	name = cstr(name or "").strip()
	if not name:
		frappe.throw(_("Please enter a ticket type name"))

	if frappe.db.exists("HD Ticket Type", name):
		return frappe.get_doc("HD Ticket Type", name).as_dict()

	doc = frappe.get_doc(
		{
			"doctype": "HD Ticket Type",
			"name": name,
			"description": description or None,
			"priority": priority or None,
		}
	).insert(ignore_permissions=True)
	return doc.as_dict()


@frappe.whitelist()
def get_agent_candidates():
	capabilities = _require_unity_access()
	if not capabilities.can_manage_agents:
		frappe.throw(_("You are not allowed to manage agents"), frappe.PermissionError)
	return _agent_candidates()


@frappe.whitelist()
def get_ticket_detail(name):
	capabilities = _require_unity_access()
	_require_ticket_access(name, capabilities)
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
def get_accessible_ticket_summaries(names):
	capabilities = _require_unity_access()
	names = _parse_json(names, []) or []
	if not names:
		return []
	filters = [[TICKET_DOCTYPE, "name", "in", names]]
	if not capabilities.can_view_all_tickets:
		filters.append([TICKET_DOCTYPE, "_assign", "like", f"%{_session_user()}%"])
	return frappe.get_list(
		TICKET_DOCTYPE,
		fields=["name", "subject", "creation", "status"],
		filters=filters,
		page_length=max(len(names), 1),
	)


@frappe.whitelist()
def create_agent(user):
	capabilities = _require_unity_access()
	if not capabilities.can_manage_agents:
		frappe.throw(_("You are not allowed to manage agents"), frappe.PermissionError)
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
def create_ticket(
	subject,
	raised_by,
	message,
	priority=None,
	ticket_type=None,
	assignee=None,
	attachments=None,
):
	_require_unity_access()
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

	email_sent = False
	warning = ""
	try:
		doc.reply_via_agent(message=message, attachments=_parse_json(attachments, []))
		email_sent = True
	except Exception as exc:
		warning = _("Ticket created, but the email could not be sent: {0}").format(exc)
		frappe.log_error(frappe.get_traceback(), "Unity Helpdesk create_ticket reply_via_agent")

	try:
		row = frappe.get_list(TICKET_DOCTYPE, fields=_ticket_fields(), filters={"name": doc.name}, page_length=1)
		ticket = _decorate_ticket(row[0]) if row else {"name": doc.name}
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Unity Helpdesk create_ticket response")
		ticket = {"name": doc.name, "subject": doc.subject, "raised_by": doc.raised_by}

	return {
		"ticket": ticket,
		"email_sent": email_sent,
		"warning": warning,
	}


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
	capabilities = _require_unity_access()
	_require_ticket_access(name, capabilities)
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
	capabilities = _require_unity_access()
	_require_ticket_access(name, capabilities)
	if not message:
		frappe.throw(_("Please enter a reply"))
	ticket = frappe.get_doc(TICKET_DOCTYPE, name)
	ticket.reply_via_agent(message=message, cc=cc, bcc=bcc, attachments=_parse_json(attachments, []))
	# Invalidate the communication text cache so subsequent searches see the new reply
	_invalidate_communication_cache(name)
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


@frappe.whitelist()
def get_profile():
	user = frappe.get_doc("User", _session_user())
	capabilities = _require_unity_access()
	return {
		"name": user.name,
		"full_name": user.full_name,
		"email": user.email,
		"username": user.username,
		"user_image": user.user_image,
		"roles": sorted(_user_roles(user.name)),
		"capabilities": capabilities,
		"settings": {
			"unity_email_thread_layout": _default_thread_layout(),
		},
	}


@frappe.whitelist()
def update_unity_settings(unity_email_thread_layout=None):
	capabilities = _require_unity_access()
	if not capabilities.can_manage_unity_settings:
		frappe.throw(_("You are not allowed to manage Unity Helpdesk settings"), frappe.PermissionError)

	layout = _normalize_thread_layout(unity_email_thread_layout or _default_thread_layout())
	if layout not in {"Classic", "Chat Based"}:
		frappe.throw(_("Invalid email thread layout"))

	settings = frappe.get_doc("HD Settings")
	settings.unity_email_thread_layout = layout
	settings.save(ignore_permissions=True)
	return {"unity_email_thread_layout": settings.unity_email_thread_layout}
