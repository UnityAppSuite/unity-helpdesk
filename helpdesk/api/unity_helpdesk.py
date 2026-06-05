import functools
import html
import json
import re
from collections import defaultdict

import frappe
from frappe import _
from frappe.desk.form.assign_to import clear as clear_all_assignments
from frappe.query_builder import Case, Order
from frappe.query_builder.functions import Count, Sum
from frappe.utils import add_days, add_months, cstr, get_datetime, get_first_day, get_last_day, get_url, getdate, nowdate

from helpdesk.api.ticket import assign_ticket_to_agent
from helpdesk.helpdesk.doctype.hd_ticket.api import (
	get_one as get_ticket_doc,
	get_ticket_thread_components,
)


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
MAX_SEARCH_CANDIDATES = 400
# Top-N most-recently-assigned tickets we resolve via ToDo for the "My
# Tickets" / "Assigned: X" filters. Set high enough that no real human
# user can hit it (the typical agent has dozens to low-hundreds of open
# assignments). The result is still ordered by HD Ticket.modified for
# display, so this only bounds the candidate set, not the page that
# renders. Truncation here is preferable to falling back to the legacy
# `_assign LIKE '%user%'` full-table scan, which is what made the SPA's
# first paint hit 20–30 s.
MAX_ASSIGNED_LOOKUP = 25000
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
	"custom_primary_message_html",
	"custom_primary_message_text",
	"custom_search_message_body",
	# Set by helpdesk.api.unity_helpdesk_ext.create_ticket; SPA tints these rows green
	"custom_via_unity_portal",
	"custom_is_bulk_email",
	# Set by helpdesk.helpdesk.hooks.reply_link when an inbound reply chain
	# resolves back to a bulk audit ticket — used by SPA for color + banner.
	"custom_replied_to_ticket",
]


def _session_user():
	return frappe.session.user


def _request_cache():
	"""Per-request scratch dict on frappe.local, cleared automatically between
	requests. Used to memoize helpers (capabilities, roles, has-field checks)
	that get called 3–8 times per `get_tickets` request — each individually
	cheap, but together a measurable fraction of cold first-paint latency.
	"""
	cache = getattr(frappe.local, "_unity_request_cache", None)
	if cache is None:
		cache = {}
		frappe.local._unity_request_cache = cache
	return cache


def _user_roles(user=None):
	user = user or _session_user()
	cache = _request_cache().setdefault("_user_roles", {})
	if user not in cache:
		cache[user] = set(frappe.get_roles(user))
	return cache[user]


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
	cache = _request_cache().setdefault("_capabilities", {})
	if user in cache:
		return cache[user]
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

	capabilities = frappe._dict(
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
	cache[user] = capabilities
	return capabilities


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
	# Fetch (name, _assign) so we can tell apart a missing row from one with NULL _assign.
	row = frappe.db.get_value(TICKET_DOCTYPE, name, ["name", "_assign"], as_dict=True)
	if not row:
		frappe.throw(_("Ticket not found"), frappe.DoesNotExistError)
	assigned = frappe.parse_json(row.get("_assign") or "[]") or []
	if _session_user() not in assigned:
		frappe.throw(_("You do not have access to this ticket"), frappe.PermissionError)


def _parse_json(value, fallback):
	if value in (None, ""):
		return fallback
	if isinstance(value, str):
		return frappe.parse_json(value)
	return value


@functools.lru_cache(maxsize=256)
def _has_field(doctype, fieldname):
	# Schema doesn't change within a process — safe to cache forever.
	# `bench migrate` reloads the worker, so the cache is fresh after deploys.
	return frappe.get_meta(doctype).has_field(fieldname)


def _ticket_fields(extra=None):
	fields = list(UNITY_TICKET_FIELDS)
	seen = set(fields)
	for field in OPTIONAL_TICKET_FIELDS:
		if field not in seen and _has_field(TICKET_DOCTYPE, field):
			fields.append(field)
			seen.add(field)
	for field in extra or []:
		key = cstr(field or "").strip()
		if not key or key in seen:
			continue
		if _has_field(TICKET_DOCTYPE, key):
			fields.append(key)
			seen.add(key)
	return fields


# Columns the user can show/hide/reorder/resize in the Unity Helpdesk tickets list.
# `key` is the HD Ticket field name (or a virtual key handled by the SPA, e.g.
# "hold_summary"). `fixed: True` columns are always visible and can't be removed.
AVAILABLE_TICKET_COLUMNS = [
	{"key": "name", "label": "Ticket ID", "default": True, "fixed": True, "width": 110},
	{"key": "subject", "label": "Subject", "default": True, "fixed": True, "width": 280},
	{"key": "ticket_type", "label": "Ticket Type", "default": True, "fixed": False, "width": 150},
	{"key": "priority", "label": "Priority", "default": True, "fixed": False, "width": 130},
	{"key": "status", "label": "Status", "default": True, "fixed": False, "width": 140},
	{"key": "_assign", "label": "Assigned To", "default": True, "fixed": False, "width": 170},
	{"key": "creation", "label": "Created On", "default": True, "fixed": False, "width": 130},
	{"key": "custom_is_on_hold", "label": "Issues On Hold", "default": True, "fixed": False, "width": 140},
	{"key": "custom_hold_reason", "label": "Reason Of Hold", "default": True, "fixed": False, "width": 200},
	{"key": "raised_by", "label": "Raised By", "default": False, "fixed": False, "width": 220},
	{"key": "agent_group", "label": "Agent Group", "default": False, "fixed": False, "width": 150},
	{"key": "modified", "label": "Last Updated", "default": False, "fixed": False, "width": 130},
	{"key": "response_by", "label": "Response Due", "default": False, "fixed": False, "width": 140},
	{"key": "resolution_by", "label": "Resolution Due", "default": False, "fixed": False, "width": 150},
	{"key": "agreement_status", "label": "SLA Status", "default": False, "fixed": False, "width": 130},
	{"key": "first_responded_on", "label": "First Responded On", "default": False, "fixed": False, "width": 160},
	{"key": "resolution_date", "label": "Resolved On", "default": False, "fixed": False, "width": 140},
	{"key": "custom_hold_from", "label": "Hold From", "default": False, "fixed": False, "width": 130},
	{"key": "custom_hold_to", "label": "Hold To", "default": False, "fixed": False, "width": 130},
	{"key": "custom_primary_message_text", "label": "Mail Body", "default": False, "fixed": False, "width": 320},
]
AVAILABLE_TICKET_COLUMN_KEYS = {c["key"] for c in AVAILABLE_TICKET_COLUMNS}
COLUMN_PREFS_DEFAULT_KEY = "unity_helpdesk_columns"
COLUMN_WIDTH_MIN = 60
COLUMN_WIDTH_MAX = 1400
COLUMN_PREFS_MAX_ITEMS = 100


def _localized_available_columns():
	return [{**col, "label": _(col["label"])} for col in AVAILABLE_TICKET_COLUMNS]


def _default_column_preferences():
	return [
		{"key": col["key"], "width": col["width"]}
		for col in AVAILABLE_TICKET_COLUMNS
		if col["default"]
	]


def _load_column_preferences():
	raw = frappe.db.get_default(COLUMN_PREFS_DEFAULT_KEY, frappe.session.user)
	if not raw:
		return _default_column_preferences()
	# Older Frappe versions can wrap a single value in a 1-element list — unwrap.
	if isinstance(raw, list | tuple) and len(raw) == 1:
		raw = raw[0]
	try:
		stored = json.loads(raw) if isinstance(raw, str) else raw
	except (TypeError, ValueError):
		return _default_column_preferences()
	if not isinstance(stored, list):
		return _default_column_preferences()
	cleaned = []
	seen = set()
	for item in stored:
		if not isinstance(item, dict):
			continue
		key = cstr(item.get("key") or "").strip()
		if not key or key in seen or key not in AVAILABLE_TICKET_COLUMN_KEYS:
			continue
		seen.add(key)
		try:
			width = int(item.get("width") or 0)
		except (TypeError, ValueError):
			width = 0
		if width < COLUMN_WIDTH_MIN or width > COLUMN_WIDTH_MAX:
			width = next(
				(c["width"] for c in AVAILABLE_TICKET_COLUMNS if c["key"] == key),
				140,
			)
		cleaned.append({"key": key, "width": width})
	# Ensure fixed columns are always present (at the front, in defined order)
	fixed_keys = [c["key"] for c in AVAILABLE_TICKET_COLUMNS if c["fixed"]]
	for key in reversed(fixed_keys):
		if key in seen:
			continue
		width = next(c["width"] for c in AVAILABLE_TICKET_COLUMNS if c["key"] == key)
		cleaned.insert(0, {"key": key, "width": width})
		seen.add(key)
	return cleaned or _default_column_preferences()


def _selected_column_fields():
	# Returns the HD Ticket fieldnames a user's column choice depends on, so
	# get_tickets() can fetch them. Virtual keys (none currently) are skipped.
	return [pref["key"] for pref in _load_column_preferences()]


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


def _decorate_ticket_rows(rows):
	# Page-wide bulk fetch: collapses N+1 User lookups (and the ToDo fallback
	# for empty _assign) into one SELECT each. Used by the list endpoint —
	# single-row callers stay on _decorate_ticket().
	if not rows:
		return []

	users_needed = set()
	name_to_users = {}
	todo_lookup_names = []
	for row in rows:
		row_dict = frappe._dict(row)
		assignees = frappe.parse_json(_assignment_value(row_dict) or "[]")
		if assignees:
			name_to_users[row_dict.get("name")] = assignees
			users_needed.update(a for a in assignees if a)
		else:
			todo_lookup_names.append(row_dict.get("name"))

	# One ToDo lookup for the empty-_assign fallback (matches _assignee_from_assign).
	if todo_lookup_names:
		todos = frappe.get_all(
			"ToDo",
			filters={
				"reference_type": TICKET_DOCTYPE,
				"reference_name": ["in", todo_lookup_names],
				"status": "Open",
			},
			fields=["reference_name", "owner"],
			order_by="creation asc",
		)
		for t in todos:
			if not t.owner:
				continue
			name_to_users.setdefault(t.reference_name, []).append(t.owner)
			users_needed.add(t.owner)

	# One User lookup for everyone we'll display.
	user_map = {}
	if users_needed:
		user_rows = frappe.get_all(
			"User",
			filters={"name": ["in", list(users_needed)]},
			fields=["name", "full_name", "user_image", "email"],
		)
		user_map = {u.name: u for u in user_rows}

	decorated = []
	for row in rows:
		row_dict = frappe._dict(row)
		users = name_to_users.get(row_dict.get("name")) or []
		first = users[0] if users else None
		if first:
			row_dict.assignee = user_map.get(first) or {"name": first, "full_name": first}
		else:
			row_dict.assignee = None
		row_dict.status_indicator = _status_indicator(row_dict)
		row_dict.priority_target = PRIORITY_TARGETS.get(row_dict.get("priority"), "")
		decorated.append(row_dict)
	return decorated


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


def _ticket_message_search_fields():
	return [
		field
		for field in [
			"custom_primary_message_html",
			"custom_primary_message_text",
			"custom_search_message_body",
		]
		if frappe.db.has_column(TICKET_DOCTYPE, field)
	]


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


def _parse_class_number(program):
	# Program names follow "<class>-<school descriptor>", e.g. "4-Walnut School at Shivane"
	# or "PG-1-Walnut" — the class segment itself may contain hyphens. rsplit
	# strips only the final descriptor and preserves multi-segment class labels.
	raw = cstr(program or "").strip()
	if not raw:
		return None
	if "-" not in raw:
		return raw
	head, _sep, _rest = raw.rpartition("-")
	return head.strip() or raw


def _fetch_school_locations(students_by_id, enrollment_rows):
	if not _has_doctype("School"):
		return {}
	school_ids = set()
	for student in (students_by_id or {}).values():
		sid = cstr(student.get("school") or "").strip()
		if sid:
			school_ids.add(sid)
	for row in enrollment_rows or []:
		sid = cstr(row.get("custom_school") or "").strip()
		if sid:
			school_ids.add(sid)
	if not school_ids:
		return {}
	try:
		rows = frappe.get_all(
			"School",
			fields=["name", "location"],
			filters={"name": ["in", sorted(school_ids)]},
			page_length=10000,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "unity_helpdesk._fetch_school_locations")
		return {}
	return {row.get("name"): cstr(row.get("location") or "").strip() or None for row in rows}


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


@frappe.whitelist()
def get_student_context(ticket_name):
	"""Whitelisted wrapper around `get_student_context_for_ticket` so the SPA
	can fetch the student panel in parallel with `get_ticket_detail`.

	Previously the student context was bundled into the synchronous
	`get_ticket_detail` response, but its ~10+ Education-app `frappe.get_all`
	calls regularly pushed the combined response over the SPA's 20 s
	timeout (the ticket detail page rendered as a permanent skeleton).
	With this split, the ticket header / thread renders the moment the
	first response lands; the student panel fills in when the second
	resolves. A slow Education app no longer hangs the whole page.
	"""
	capabilities = _require_unity_access()
	_require_ticket_access(ticket_name, capabilities)
	raised_by = frappe.db.get_value(TICKET_DOCTYPE, ticket_name, "raised_by") or ""
	return get_student_context_for_ticket(ticket_name=ticket_name, raised_by=raised_by)


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
			fields=[
				"name",
				"guardian_name",
				"email_address",
				"user",
				"mobile_number",
				"alternate_number",
			],
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

	# Map of School name -> location. Used to render "<class>-<section>-<location>"
	# in the SPA's student-context sidebar. Best-effort: silently skips if the
	# School doctype is unavailable (e.g. edu_quality not installed).
	school_locations = _fetch_school_locations(students_by_id, enrollment_rows)

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
		guardian_cards = []
		for guardian_id in student_guardian_ids:
			guardian_doc = guardian_docs.get(guardian_id) or {}
			guardian_name = cstr(
				guardian_doc.get("guardian_name")
				or guardian_doc.get("name")
			).strip()
			if guardian_name:
				guardian_names.append(guardian_name)
			email_for_card = ""
			for email_value in [
				guardian_doc.get("email_address"),
				guardian_doc.get("user"),
			]:
				email_text = cstr(email_value).strip().lower()
				if email_text:
					guardian_emails.append(email_text)
					email_for_card = email_for_card or email_text
			guardian_cards.append(
				{
					"id": guardian_id,
					"name": guardian_name,
					"email": email_for_card,
					"mobile": cstr(guardian_doc.get("mobile_number") or "").strip(),
					"alternate_mobile": cstr(guardian_doc.get("alternate_number") or "").strip(),
				}
			)

		next_schedule = _next_payment_schedule(
			payment_schedule_by_fee.get(selected_fee.get("name"), []) if selected_fee else []
		)
		resolved_school = (
			(selected_enrollment.get("custom_school") if selected_enrollment else None)
			or student.get("school")
		)
		student_cards.append(
			{
				"student_id": student_id,
				"student_name": _student_display_name(student),
				"is_primary_match": student_id in primary_student_ids,
				"is_sibling": student_id not in primary_student_ids,
				"school": student.get("school"),
				"school_location": school_locations.get(resolved_school) if resolved_school else None,
				"class_program": student.get("program"),
				"class_number": _parse_class_number(student.get("program")),
				"division": student.get("custom_division"),
				"student_status": student.get("student_status"),
				"student_mobile_number": student.get("student_mobile_number"),
				"primary_contact": student.get("primary_contact"),
				"whatsapp_number": student.get("whatsapp_number"),
				"is_sibling_in_school": bool(int(student.get("is_sibling_in_school") or 0)),
				"guardian_ids": student_guardian_ids,
				"guardian_names": sorted(set(guardian_names)),
				"guardian_emails": sorted(set(guardian_emails)),
				"guardians": guardian_cards,
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
		ticket_name = cstr(ticket_doc.name if ticket_doc else ticket).strip()
		if ticket_name:
			update_ticket_message_search_index(ticket_name, ticket_doc=ticket_doc)
		return {}

	context = get_student_context_for_ticket(ticket_doc.name, ticket_doc.raised_by)
	search_update = {}
	# These three fields are Data type (VARCHAR 255) — truncate to stay within limit.
	_DATA_FIELD_MAX = 255
	if frappe.db.has_column(TICKET_DOCTYPE, "custom_search_student_names"):
		search_update["custom_search_student_names"] = ", ".join(
			sorted(
				{
					cstr(student.get("student_name")).strip()
					for student in context.get("students", [])
					if cstr(student.get("student_name")).strip()
				}
			)
		)[:_DATA_FIELD_MAX]
	if frappe.db.has_column(TICKET_DOCTYPE, "custom_search_student_refs"):
		search_update["custom_search_student_refs"] = ", ".join(
			sorted(
				{
					cstr(student.get("reference_number")).strip()
					for student in context.get("students", [])
					if cstr(student.get("reference_number")).strip()
				}
			)
		)[:_DATA_FIELD_MAX]
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
		search_update["custom_search_guardian_emails"] = ", ".join(sorted(emails))[:_DATA_FIELD_MAX]
	search_update.update(_build_ticket_message_search_field_update(ticket_doc.name, ticket_doc=ticket_doc))
	if search_update:
		frappe.db.set_value(TICKET_DOCTYPE, ticket_doc.name, search_update, update_modified=False)
		_invalidate_communication_cache(ticket_doc.name)
	return context


SEARCH_BODY_MAX = 12000
SEARCH_HEAD_BUDGET = 2000
SEARCH_TAIL_BUDGET = 9500
SEARCH_BODY_SEPARATOR = " · "


def _truncate_search_text(text, max_chars=SEARCH_BODY_MAX):
	text = cstr(text or "").strip()
	if len(text) <= max_chars:
		return text
	return text[:max_chars].rsplit(" ", 1)[0].strip() or text[:max_chars]


def _pack_chunks(chunks, budget):
	"""Pack normalized text chunks into a single string, stopping when the budget
	is hit. Returns the packed string (with single-space separators between
	chunks). Long final chunks are clipped on a word boundary if there's room
	for >200 chars; otherwise dropped to keep things tidy."""
	if budget <= 0:
		return ""
	out = []
	used = 0
	for chunk in chunks:
		chunk = cstr(chunk or "").strip()
		if not chunk:
			continue
		needed = len(chunk) + (1 if out else 0)
		if used + needed <= budget:
			out.append(chunk)
			used += needed
			continue
		# Final chunk doesn't fully fit. Clip it if there's >200 chars of room left.
		remaining = budget - used - (1 if out else 0)
		if remaining > 200:
			clipped = chunk[:remaining].rsplit(" ", 1)[0].strip()
			if clipped:
				out.append(clipped)
		break
	return " ".join(out)


def _assemble_search_body(subject, opening_parts, recent_parts):
	"""Build the searchable body: subject + head (opening complaint, 2KB) +
	tail (recent thread, 9.5KB newest-first). Caller is responsible for
	ordering recent_parts newest-first."""
	subject = cstr(subject or "").strip()
	head = _pack_chunks(opening_parts, SEARCH_HEAD_BUDGET)
	tail = _pack_chunks(recent_parts, SEARCH_TAIL_BUDGET)
	pieces = [p for p in (subject, head, tail) if p]
	if not pieces:
		return ""
	return _truncate_search_text(SEARCH_BODY_SEPARATOR.join(pieces))


def _primary_message_values(ticket_name, ticket_doc=None, communication_rows=None):
	ticket_doc = ticket_doc or frappe.get_cached_doc(TICKET_DOCTYPE, ticket_name)
	communication_rows = communication_rows or []

	for row in communication_rows:
		if cstr(row.get("sent_or_received")).strip() != "Received":
			continue
		content_html = cstr(row.get("content") or "").strip()
		if not content_html:
			continue
		return content_html, _normalize_search_text(content_html)

	# Fallback to description regardless of whether other (Sent) comms exist.
	# Without this, tickets where only agent comms exist show no primary message.
	description_html = cstr(ticket_doc.get("description") or "").strip() if ticket_doc else ""
	if description_html:
		return description_html, _normalize_search_text(description_html)

	return "", ""


def _build_ticket_message_search_values(ticket_name, ticket_doc=None):
	ticket_doc = ticket_doc or frappe.get_cached_doc(TICKET_DOCTYPE, ticket_name)
	thread_components = get_ticket_thread_components(ticket_name)
	communication_rows = thread_components.communications
	primary_message_html, primary_message_text = _primary_message_values(
		ticket_name,
		ticket_doc=ticket_doc,
		communication_rows=communication_rows,
	)

	subject_text = _normalize_search_text(ticket_doc.get("subject")) if ticket_doc else ""

	# Head: the opening complaint that defines what the ticket is about.
	# Kept in a small 2KB budget so recent replies always have room in the tail.
	opening_parts = []
	if primary_message_text:
		opening_parts.append(primary_message_text)
	if ticket_doc:
		opening_parts.append(_normalize_search_text(ticket_doc.get("description")))

	# Tail: communications + comments newest-first, packed into 9.5KB. Ensures
	# the latest agent reply is always present in the indexed body even for
	# long threads.
	recent_parts = []
	for row in reversed(communication_rows):
		recent_parts.append(_normalize_search_text(row.subject))
		recent_parts.append(_normalize_search_text(row.content))
	for row in reversed(thread_components.comments):
		recent_parts.append(_normalize_search_text(row.content))

	combined = _assemble_search_body(subject_text, opening_parts, recent_parts)
	return primary_message_html, primary_message_text, combined


def _build_ticket_message_search_field_update(ticket_name, ticket_doc=None):
	field_names = _ticket_message_search_fields()
	if not field_names:
		return {}
	primary_message_html, primary_message_text, search_text = _build_ticket_message_search_values(
		ticket_name,
		ticket_doc=ticket_doc,
	)
	search_update = {}
	if "custom_primary_message_html" in field_names:
		search_update["custom_primary_message_html"] = primary_message_html
	if "custom_primary_message_text" in field_names:
		search_update["custom_primary_message_text"] = primary_message_text
	if "custom_search_message_body" in field_names:
		search_update["custom_search_message_body"] = search_text
	return search_update


def _build_ticket_message_search_text(ticket_name, ticket_doc=None):
	return _build_ticket_message_search_values(ticket_name, ticket_doc=ticket_doc)[2]


def update_ticket_message_search_index(ticket_name, ticket_doc=None):
	ticket_name = cstr(ticket_name).strip()
	if not ticket_name:
		return ""
	search_update = _build_ticket_message_search_field_update(ticket_name, ticket_doc=ticket_doc)
	search_text = cstr(search_update.get("custom_search_message_body") or "").strip()
	if search_update:
		frappe.db.set_value(
			TICKET_DOCTYPE,
			ticket_name,
			search_update,
			update_modified=False,
		)
	_invalidate_communication_cache(ticket_name)
	return search_text


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
		message_body_text = _normalize_search_text(row.get("custom_search_message_body"))

		# Always include raised_by in guardian_emails so exact-email search (Tier 2) works
		if raised_by and raised_by not in guardian_emails:
			guardian_emails.append(raised_by)

		legacy_parts = [_normalize_search_text(row.get(field)) for field in legacy_content_fields]
		subject_text = _normalize_search_text(row.get("subject"))
		content_text = " ".join(
			part
			for part in [subject_text, message_body_text, *legacy_parts]
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


def _rank_ticket_document(document, query, tokens, family_terms=None):
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

	# Family-aware match: when the user searched a guardian email, accept any
	# ticket whose identity links to the same family (even if the literal
	# query string isn't anywhere on the ticket). Treated as Tier-2 — same
	# tier as a direct email match — so it ranks above content matches.
	if family_terms:
		doc_emails_lc = {e.lower() for e in identity_emails if e}
		fam_emails_lc = {e.lower() for e in family_terms.get("emails") or []}
		if doc_emails_lc & fam_emails_lc:
			return (2, 0)
		raised_by = (document.get("raised_by") or "").lower()
		for sid in family_terms.get("student_ids") or []:
			if sid and f"{sid.lower()}@" in raised_by:
				return (2, 0)
		fam_refs_lc = {r.lower() for r in family_terms.get("student_refs") or []}
		if {r.lower() for r in student_refs if r} & fam_refs_lc:
			return (2, 0)
		fam_names_lc = {n.lower() for n in family_terms.get("student_names") or []}
		if {n.lower() for n in student_names if n} & fam_names_lc:
			return (2, 0)

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


def _ranked_ticket_ids(ticket_rows, search, family_terms=None):
	query = _normalize_search_text(search)
	tokens = _search_tokens(search)
	documents = _ticket_search_documents(ticket_rows)
	ranked = []
	for row in ticket_rows:
		document = documents.get(row.name)
		rank = (
			_rank_ticket_document(document, query, tokens, family_terms=family_terms)
			if document
			else None
		)
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


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _looks_like_email(value):
	return bool(_EMAIL_RE.match(cstr(value or "").strip()))


def _expand_email_to_family_search_terms(email):
	"""Given a guardian email, return every search term that identifies the
	whole family: every guardian's email + the students' login emails + the
	students' reference numbers + the students' display names + the student
	IDs themselves. Searching by one parent's email then surfaces every
	ticket associated with the family, including ones raised by the student
	directly or indexed only by student ref/name."""
	email = cstr(email or "").strip().lower()
	terms = {
		"emails": {email} if email else set(),
		"student_refs": set(),
		"student_names": set(),
		"student_ids": set(),
	}
	if not email:
		return terms
	if not (_has_doctype("Guardian") and _has_doctype("Student Guardian")):
		return terms

	guardian_ids = set()
	for row in frappe.get_all(
		"Guardian",
		fields=["name"],
		filters={"email_address": email},
		page_length=0,
	):
		guardian_ids.add(row.name)
	for row in frappe.get_all(
		"Guardian",
		fields=["name"],
		filters={"user": email},
		page_length=0,
	):
		guardian_ids.add(row.name)
	if not guardian_ids:
		return terms

	student_ids = set()
	for row in frappe.get_all(
		"Student Guardian",
		fields=["parent"],
		filters={"parenttype": "Student", "guardian": ["in", list(guardian_ids)]},
		page_length=0,
	):
		if row.parent:
			student_ids.add(row.parent)
	if not student_ids:
		return terms

	family_guardian_ids = set()
	for row in frappe.get_all(
		"Student Guardian",
		fields=["guardian"],
		filters={"parenttype": "Student", "parent": ["in", list(student_ids)]},
		page_length=0,
	):
		if row.guardian:
			family_guardian_ids.add(row.guardian)
	if family_guardian_ids:
		for row in frappe.get_all(
			"Guardian",
			fields=["email_address", "user"],
			filters={"name": ["in", list(family_guardian_ids)]},
			page_length=0,
		):
			for value in (row.email_address, row.user):
				text = cstr(value or "").strip().lower()
				if text:
					terms["emails"].add(text)

	# Pull each student's identifiers: id, ref, name, login email.
	if _has_doctype("Student"):
		for row in frappe.get_all(
			"Student",
			fields=["name", "first_name", "last_name", "reference_number", "user"],
			filters={"name": ["in", list(student_ids)]},
			page_length=0,
		):
			terms["student_ids"].add(cstr(row.name).strip())
			ref = cstr(row.get("reference_number") or "").strip()
			if ref:
				terms["student_refs"].add(ref)
			full_name = " ".join(
				p
				for p in (cstr(row.get("first_name") or ""), cstr(row.get("last_name") or ""))
				if p.strip()
			).strip()
			if full_name:
				terms["student_names"].add(full_name)
			user_email = cstr(row.get("user") or "").strip().lower()
			if user_email:
				terms["emails"].add(user_email)

	return terms


def _expand_email_to_family_emails(email):
	"""Backward-compat shim — kept so external callers still work."""
	return _expand_email_to_family_search_terms(email)["emails"]


def _search_candidate_ticket_names(base_filters, search):
	query = cstr(search or "").strip()
	if not query:
		return set()

	candidate_names = set()

	# Fast path: user typed an exact ticket ID.
	if frappe.db.exists(TICKET_DOCTYPE, query):
		_append_ticket_names(
			candidate_names,
			frappe.get_list(
				TICKET_DOCTYPE,
				fields=["name"],
				filters=_merge_filters(base_filters, [[TICKET_DOCTYPE, "name", "=", query]]),
				page_length=1,
			),
		)
		if candidate_names:
			return candidate_names

	# Build the searchable column list dynamically — Data fields are indexed, the
	# message body is a Small Text full-scan fallback.
	search_fields = ["name", "subject", "raised_by"]
	for field in [
		"custom_search_student_names",
		"custom_search_student_refs",
		"custom_search_guardian_emails",
		"custom_search_message_body",
	]:
		if _has_field(TICKET_DOCTYPE, field):
			search_fields.append(field)

	# Guardian-email family expansion: if the query is a single email, surface
	# every ticket associated with the family. We expand to:
	#   - every guardian's email + student login emails
	#   - every student id / reference number / display name
	# and OR-search across raised_by and the indexed Data fields. This is robust
	# to historical tickets that were indexed before all guardians were linked
	# (e.g. ticket raised by the student themselves with only raised_by populated).
	if _looks_like_email(query):
		terms = _expand_email_to_family_search_terms(query)
		family_emails = terms["emails"]
		family_refs = terms["student_refs"]
		family_names = terms["student_names"]
		family_ids = terms["student_ids"]
		# Only enter the expanded path when expansion actually found something
		# beyond the literal query (otherwise fall through to normal token search).
		expanded = (
			len(family_emails) > 1
			or family_refs
			or family_names
			or family_ids
		)
		if expanded:
			or_filters = []
			for email in family_emails:
				or_filters.append([TICKET_DOCTYPE, "raised_by", "=", email])
				if _has_field(TICKET_DOCTYPE, "custom_search_guardian_emails"):
					or_filters.append(
						[
							TICKET_DOCTYPE,
							"custom_search_guardian_emails",
							"like",
							f"%{email}%",
						]
					)
				# Some tickets are raised by the student's own login (waca78@…) —
				# those won't be in custom_search_guardian_emails, so the raised_by
				# equality covers them.
			if _has_field(TICKET_DOCTYPE, "custom_search_student_refs"):
				for ref in family_refs:
					or_filters.append(
						[TICKET_DOCTYPE, "custom_search_student_refs", "like", f"%{ref}%"]
					)
			if _has_field(TICKET_DOCTYPE, "custom_search_student_names"):
				for sname in family_names:
					or_filters.append(
						[TICKET_DOCTYPE, "custom_search_student_names", "like", f"%{sname}%"]
					)
			# Many tickets have raised_by = "<student-id>@<domain>" — a substring
			# match on the local part lets us catch them even if the domain
			# isn't in the family email set.
			for student_id in family_ids:
				or_filters.append(
					[TICKET_DOCTYPE, "raised_by", "like", f"%{student_id}@%"]
				)
			_append_ticket_names(
				candidate_names,
				frappe.get_list(
					TICKET_DOCTYPE,
					fields=["name"],
					filters=base_filters,
					or_filters=or_filters,
					order_by="modified desc",
					page_length=MAX_SEARCH_CANDIDATES,
				),
			)
			return candidate_names

	# Tokenize the query the same way the index was tokenized (HTML-stripped,
	# lowercase, alphanumerics + @._-). This makes pasted chunks of email body
	# match even though the index has normalized whitespace and stripped tags.
	# Pasting the entire customer mail used to fail because LIKE %<200 chars>%
	# could not span the indexed/normalized whitespace boundaries.
	tokens = _search_tokens(query)
	# Drop very short tokens (1-2 chars) — they explode candidate sets and rarely help.
	# Keep "to", "of"-length only when the *entire* query is short, so single-letter
	# searches like "AC" still work.
	if len(tokens) > 1:
		tokens = [t for t in tokens if len(t) >= 3]
	# Cap to keep the worst-case at MAX_SEARCH_TOKENS DB round-trips.
	MAX_SEARCH_TOKENS = 8
	tokens = tokens[:MAX_SEARCH_TOKENS]

	if not tokens:
		# Fall back to substring match on the raw query so users with quoted
		# punctuation/short queries that tokenize to nothing still get results.
		or_filters = [
			[TICKET_DOCTYPE, field, "like", _like_pattern(query)] for field in search_fields
		]
		_append_ticket_names(
			candidate_names,
			frappe.get_list(
				TICKET_DOCTYPE,
				fields=["name"],
				filters=base_filters,
				or_filters=or_filters,
				order_by="modified desc",
				page_length=MAX_SEARCH_CANDIDATES,
			),
		)
		return candidate_names

	# For long pasted queries (≥4 tokens or >60 chars), run FULLTEXT first
	# when the index is available. The AND-of-OR LIKE path scans the whole
	# 90K-row table and cliff-edges to zero whenever any one of the 8
	# capped tokens is missing from the head/tail-truncated indexed body —
	# trying it first for a long query is doubled wasted work. FULLTEXT
	# scores partial matches and ignores stopwords automatically.
	# Short queries like "TA16" stay on the legacy path (direct ticket-ID
	# + Data-field LIKE handles them already).
	is_long_query = len(tokens) >= 4 or len(cstr(query)) > 60
	if is_long_query and _fulltext_index_available():
		candidate_names = _fulltext_candidates(query, base_filters)
		if candidate_names:
			return candidate_names

	# Multi-token AND-of-OR — primary path for short queries, and the
	# fallback for long queries whose FULLTEXT search returned empty.
	# Single SQL so the LIMIT applies after the AND filter, not per token
	# — otherwise common tokens like "the" or "and" cap their per-token
	# result at the 400 most-recently-modified rows and silently drop
	# older tickets that do contain all the tokens.
	return _multi_token_candidates(tokens, search_fields, base_filters)


def _multi_token_candidates(tokens, search_fields, base_filters):
	"""Return ticket names matching ALL tokens across ANY of search_fields,
	ordered by modified desc and capped at MAX_SEARCH_CANDIDATES — in a single
	SQL so the LIMIT doesn't truncate older candidates per token."""
	from pypika.terms import Criterion

	if not tokens:
		return set()

	QBTicket = frappe.qb.DocType(TICKET_DOCTYPE)

	# Build (field1 LIKE %tok% OR field2 LIKE %tok% OR ...) AND (... next token ...) AND ...
	and_groups = []
	for token in tokens:
		pattern = f"%{token}%"
		field_conditions = [QBTicket[field].like(pattern) for field in search_fields]
		if field_conditions:
			and_groups.append(Criterion.any(field_conditions))
	if not and_groups:
		return set()

	query = (
		frappe.qb.from_(QBTicket)
		.select(QBTicket.name)
		.where(Criterion.all(and_groups))
	)

	# base_filters is the same shape as the list passed to frappe.get_list — fold
	# them into the qb query as additional WHERE clauses. We only handle the few
	# operators _build_filters actually emits (=, in, is set/not set).
	for filt in base_filters or []:
		if not isinstance(filt, (list, tuple)) or len(filt) < 3:
			continue
		# Each filt is [doctype, field, operator, value] or [field, operator, value]
		if len(filt) == 4:
			_doctype, field, op, value = filt
		else:
			field, op, value = filt
		col = QBTicket[field]
		op_norm = cstr(op).strip().lower()
		if op_norm == "=":
			query = query.where(col == value)
		elif op_norm == "in":
			query = query.where(col.isin(list(value or [])))
		elif op_norm == "not in":
			query = query.where(col.notin(list(value or [])))
		elif op_norm in ("is", "is not"):
			# "is", "set" / "is", "not set"
			if cstr(value).strip().lower() in ("set", "not set"):
				if cstr(value).strip().lower() == "set":
					query = query.where(col.notnull())
				else:
					query = query.where(col.isnull())
		# Other operators (rare in base_filters) are skipped — base_filters always
		# already passed into get_list elsewhere so user-permission scoping is preserved
		# by the column-level filters above.

	query = query.orderby(QBTicket.modified, order=Order.desc).limit(MAX_SEARCH_CANDIDATES)
	rows = query.run(as_dict=True)
	return {row["name"] for row in rows}


# Columns covered by the FULLTEXT index added in
# helpdesk/patches/unity_ticket_search_fulltext.py. Must match exactly
# (MariaDB only uses a FULLTEXT index when the MATCH column list is
# byte-identical to the index column list).
_FULLTEXT_COLUMNS = (
	"custom_search_message_body",
	"subject",
	"custom_search_student_names",
	"custom_search_student_refs",
	"custom_search_guardian_emails",
)
_FULLTEXT_INDEX_NAME = "search_body_ft_idx"


@functools.lru_cache(maxsize=1)
def _fulltext_index_available():
	"""One-shot check: does the search_body_ft_idx FULLTEXT INDEX exist on
	tabHD Ticket? Cached for the worker's lifetime — schema doesn't change
	without a migrate + worker restart, and the cache avoids a per-query
	"try MATCH AGAINST → catch 1191 → log_error" overhead when the index
	isn't there (older deploys, fresh installs before the patch runs,
	or sites whose MariaDB version refused the ALTER TABLE).
	"""
	try:
		rows = frappe.db.sql(
			"""SELECT 1 FROM information_schema.STATISTICS
			   WHERE table_schema = DATABASE()
			     AND table_name = 'tabHD Ticket'
			     AND index_name = %s
			   LIMIT 1""",
			(_FULLTEXT_INDEX_NAME,),
		)
		return bool(rows)
	except Exception:
		return False


def _fulltext_candidates(query, base_filters):
	"""Relevance-ranked candidate set via MariaDB FULLTEXT INDEX. Used as a
	fallback when the AND-of-OR LIKE path returns nothing for a long pasted
	query. Tolerates a missing index (returns empty set) so the rest of the
	search keeps working on a site where the patch hasn't run yet.
	"""
	# Skip entirely if the FULLTEXT index isn't on this site — saves the
	# round-trip + 1191 exception cost on every "no AND match" query.
	if not _fulltext_index_available():
		return set()
	# FULLTEXT in InnoDB ignores words shorter than
	# innodb_ft_min_token_size (default 3) and the stopword list; strip
	# explicitly so the query stays short and predictable.
	cleaned_tokens = [t for t in _search_tokens(query) if len(t) >= 4]
	if not cleaned_tokens:
		return set()
	# Bound the query string length — long pastes still tokenise to dozens
	# of unique 4+-char words; 200 chars is plenty for relevance ranking.
	cleaned = " ".join(cleaned_tokens)[:200]

	col_list = ", ".join(f"`{c}`" for c in _FULLTEXT_COLUMNS)
	sql = (
		f"SELECT name FROM `tabHD Ticket` "
		f"WHERE MATCH({col_list}) AGAINST (%s IN NATURAL LANGUAGE MODE) "
		f"ORDER BY MATCH({col_list}) AGAINST (%s IN NATURAL LANGUAGE MODE) DESC "
		f"LIMIT %s"
	)
	try:
		rows = frappe.db.sql(sql, (cleaned, cleaned, MAX_SEARCH_CANDIDATES))
	except Exception as exc:
		# 1191 = "Can't find FULLTEXT index matching the column list" — the
		# patch hasn't run on this site. Log once and degrade silently;
		# the legacy search path still returns its (empty) result.
		frappe.log_error(
			title="unity search FULLTEXT fallback failed",
			message=f"{type(exc).__name__}: {exc}",
		)
		return set()

	candidate_names = {row[0] for row in rows if row and row[0]}
	if not candidate_names or not base_filters:
		return candidate_names

	# Re-apply base_filters (view + permission_query scope) via a narrow
	# IN(...) query — the raw SQL above bypassed Frappe's permission layer.
	filtered = frappe.get_list(
		TICKET_DOCTYPE,
		fields=["name"],
		filters=_merge_filters(
			base_filters,
			[[TICKET_DOCTYPE, "name", "in", list(candidate_names)]],
		),
		page_length=len(candidate_names),
	)
	return {row.name for row in filtered}


@frappe.whitelist()
def backfill_ticket_message_search_fields(ticket_names=None, limit=500):
	capabilities = _require_unity_access()
	if not capabilities.can_manage_unity_settings:
		frappe.throw(_("You are not allowed to backfill Unity search fields"), frappe.PermissionError)
	names = [cstr(name).strip() for name in (_parse_json(ticket_names, []) or []) if cstr(name).strip()]
	if names:
		target_names = names
	else:
		fields = ["name"]
		if frappe.db.has_column(TICKET_DOCTYPE, "custom_search_message_body"):
			fields.append("custom_search_message_body")
		if frappe.db.has_column(TICKET_DOCTYPE, "custom_primary_message_text"):
			fields.append("custom_primary_message_text")
		rows = frappe.get_all(
			TICKET_DOCTYPE,
			fields=fields,
			order_by="modified desc",
			page_length=int(limit or 500),
		)
		target_names = [
			cstr(row.name).strip()
			for row in rows
			if cstr(row.name).strip()
			and (
				not cstr(row.get("custom_search_message_body") or "").strip()
				or not cstr(row.get("custom_primary_message_text") or "").strip()
			)
		]

	updated = 0
	for ticket_name in target_names:
		update_ticket_message_search_index(ticket_name)
		updated += 1
	if updated:
		frappe.db.commit()

	return {"updated": updated, "ticket_names": target_names}


@frappe.whitelist()
def backfill_ticket_message_search_index(ticket_names=None, limit=500):
	return backfill_ticket_message_search_fields(ticket_names=ticket_names, limit=limit)


@frappe.whitelist()
def diagnose_ticket_thread_and_search(name, text=None):
	capabilities = _require_unity_access()
	if not capabilities.can_manage_unity_settings:
		frappe.throw(_("You are not allowed to run Unity Helpdesk diagnostics"), frappe.PermissionError)

	ticket_name = cstr(name).strip()
	if not ticket_name:
		frappe.throw(_("Ticket name is required"))

	ticket_fields = ["name", "status", "modified", "modified_by", "subject", "raised_by"]
	if frappe.db.has_column(TICKET_DOCTYPE, "custom_search_message_body"):
		ticket_fields.append("custom_search_message_body")
	ticket = frappe.db.get_value(TICKET_DOCTYPE, ticket_name, ticket_fields, as_dict=True)
	if not ticket:
		frappe.throw(_("Ticket not found"), frappe.DoesNotExistError)

	thread_components = get_ticket_thread_components(ticket_name)
	search_text = cstr(ticket.get("custom_search_message_body") or "")
	needle = _normalize_search_text(text)

	return {
		"ticket": {
			"name": ticket.name,
			"status": ticket.status,
			"modified": ticket.modified,
			"modified_by": ticket.modified_by,
			"subject": ticket.subject,
			"raised_by": ticket.raised_by,
		},
		"counts": {
			"linked_communications": frappe.db.count(
				"Communication",
				{"reference_doctype": TICKET_DOCTYPE, "reference_name": ticket_name},
			),
			"hd_ticket_comments": frappe.db.count("HD Ticket Comment", {"reference_ticket": ticket_name}),
			"native_communications": len(thread_components.communications),
			"native_comments": len(thread_components.comments),
			"native_thread": len(thread_components.thread),
			"unity_communications": len(thread_components.communications),
			"unity_comments": len(thread_components.comments),
			"unity_thread": len(thread_components.thread),
		},
		"communication_names": [row.name for row in thread_components.communications],
		"comment_names": [row.name for row in thread_components.comments],
		"search": {
			"text": text or "",
			"normalized_text": needle,
			"index_length": len(search_text),
			"text_found_in_index": bool(needle and needle in search_text),
		},
	}


def _assigned_ticket_names(user):
	"""Return the set of HD Ticket names currently assigned to `user`, resolved
	via the indexed `tabToDo` table instead of `_assign LIKE '%user%'`.

	The old LIKE filter was a guaranteed full-table scan on a 90K-row HD Ticket
	table (leading-wildcard means no B-tree index can be used). ToDo has
	composite indexes on (reference_type, reference_name) and on (owner), so
	this lookup is O(matched-rows) — milliseconds even for the heaviest-loaded
	agent.

	Truncated to the most-recently-assigned MAX_ASSIGNED_LOOKUP names — well
	above any realistic per-user assignment count. We deliberately don't fall
	back to the LIKE filter when over the cap, because the LIKE is exactly
	the regression this whole rewrite eliminates.
	"""
	if not user:
		return set()
	cache = _request_cache().setdefault("_assigned_ticket_names", {})
	if user in cache:
		return cache[user]
	rows = frappe.get_all(
		"ToDo",
		filters={
			"reference_type": TICKET_DOCTYPE,
			"owner": user,
			"status": "Open",
		},
		fields=["reference_name"],
		# Most-recent assignments first, so silent truncation at MAX still
		# returns the rows a user would actually be looking at.
		order_by="creation desc",
		page_length=MAX_ASSIGNED_LOOKUP,
	)
	names = {row.reference_name for row in rows if row.reference_name}
	cache[user] = names
	return names


def _apply_assignee_filter(res, user):
	"""Translate "tickets assigned to <user>" into a `name IN (...)` filter via
	the indexed ToDo table. Mutates `res` (the filter list); caller continues
	building other filters normally. When the user has zero open assignments,
	emits a sentinel filter that yields an empty result without scanning the
	table.
	"""
	names = _assigned_ticket_names(user)
	if not names:
		# Sentinel that can't be a valid ticket name — short-circuits to an
		# empty result without touching tabHD Ticket. Cheaper than running the
		# query and getting 0 rows back.
		res.append([TICKET_DOCTYPE, "name", "in", ["__unity_no_assignments__"]])
		return
	res.append([TICKET_DOCTYPE, "name", "in", list(names)])


def _build_filters(view="all", filters=None, assigned_agent=None):
	filters = frappe._dict(_parse_json(filters, {}) or {})
	res = []

	if assigned_agent:
		_apply_assignee_filter(res, assigned_agent)
	elif view == "my":
		_apply_assignee_filter(res, _session_user())

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
			_apply_assignee_filter(res, filters.assigned_to)

	if filters.get("created_from"):
		res.append([TICKET_DOCTYPE, "creation", ">=", filters.created_from])
	if filters.get("created_to"):
		# Expand a date-only "to" filter to end-of-day so tickets created at
		# any time on the selected day are included. If the caller passes a
		# full datetime string we leave it as-is.
		to_value = cstr(filters.created_to).strip()
		if len(to_value) == 10:  # bare YYYY-MM-DD from the SPA date input
			to_value = f"{to_value} 23:59:59"
		res.append([TICKET_DOCTYPE, "creation", "<=", to_value])

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


def _apply_ticket_filters_to_query(query, doctype_ref, filters_list):
	"""Apply a Unity-style filter list (list of `[doctype, field, op, value]`)
	to a `frappe.qb` SELECT query. Supports the operators we actually produce
	in `_build_filters`: `=`, `!=`, `like`, `in`, `not in`, `>=`, `<=`, `>`, `<`."""
	if not filters_list:
		return query
	for entry in filters_list:
		if not entry or len(entry) < 4:
			continue
		_, field, op, value = entry[0], entry[1], entry[2], entry[3]
		col = doctype_ref[field]
		op_norm = (op or "=").lower()
		if op_norm == "=":
			query = query.where(col == value)
		elif op_norm == "!=":
			query = query.where(col != value)
		elif op_norm == "like":
			query = query.where(col.like(value))
		elif op_norm == "not like":
			query = query.where(col.not_like(value))
		elif op_norm == "in":
			query = query.where(col.isin(value if isinstance(value, (list, tuple)) else [value]))
		elif op_norm == "not in":
			query = query.where(col.notin(value if isinstance(value, (list, tuple)) else [value]))
		elif op_norm == ">=":
			query = query.where(col >= value)
		elif op_norm == "<=":
			query = query.where(col <= value)
		elif op_norm == ">":
			query = query.where(col > value)
		elif op_norm == "<":
			query = query.where(col < value)
		else:
			# Unknown op — fall back to equality so we don't silently drop a filter.
			query = query.where(col == value)
	return query


def _dashboard_cards_for_filters(filters=None):
	"""Compute dashboard card counts.

	Two paths:
	- **No filters** (the common case for `/tickets/all` and `/tickets/my`
	  without a status/priority/type filter applied): six narrow
	  `SELECT COUNT(*) WHERE status=...` queries. Each uses a specific
	  covering index:
	    - `total` → clustered index on `name` (PRIMARY)
	    - `replied/resolved/closed/pending` → `status_modified_unity_idx`
	      composite from `unity_ticket_list_indexes`
	    - `on_hold` → `on_hold_modified_unity_idx` composite
	  Index-only scans run in milliseconds even on a 90K-row table; the
	  six together sum to ~100 ms even on cold InnoDB buffer pool. The
	  previous single `SUM(CASE)` aggregate forced a full table scan
	  every call, which dominated the SPA's list-page first paint at 10+ s.
	- **Filters present**: keep the single `SUM(CASE)` aggregate.
	  Filter combinations can be arbitrary; multiplying that across six
	  COUNTs would be N×6 round trips with worse plans than one
	  aggregate that gets filtered down once.
	"""
	has_on_hold = _has_field(TICKET_DOCTYPE, "custom_is_on_hold")

	if not filters:
		# Fast path — leverage the per-status / per-on_hold composite
		# indexes added by unity_ticket_list_indexes for index-only scans.
		sql = frappe.db.sql
		try:
			total = int(sql("SELECT COUNT(*) FROM `tabHD Ticket`")[0][0] or 0)
			replied = int(
				sql("SELECT COUNT(*) FROM `tabHD Ticket` WHERE status='Replied'")[0][0] or 0
			)
			resolved = int(
				sql("SELECT COUNT(*) FROM `tabHD Ticket` WHERE status='Resolved'")[0][0] or 0
			)
			closed = int(
				sql("SELECT COUNT(*) FROM `tabHD Ticket` WHERE status='Closed'")[0][0] or 0
			)
			pending = int(
				sql(
					"SELECT COUNT(*) FROM `tabHD Ticket` WHERE status IN ('Open','Replied')"
				)[0][0]
				or 0
			)
			on_hold = 0
			if has_on_hold:
				on_hold = int(
					sql("SELECT COUNT(*) FROM `tabHD Ticket` WHERE custom_is_on_hold=1")[0][0]
					or 0
				)
		except Exception:
			# Any per-COUNT failure (DB hiccup, missing column) drops us
			# back to the existing aggregate path; the legacy function
			# also has its own fallback.
			frappe.log_error("unity dashboard cards indexed COUNTs failed; falling back")
			return _dashboard_cards_for_filters_legacy(filters)
		return {
			"total": total,
			"created": total,
			"pending": pending,
			"on_hold": on_hold,
			"resolved": resolved,
			"closed": closed,
			"replied": replied,
		}

	# Filtered path — single SUM(CASE) aggregate as before.
	HDT = frappe.qb.DocType(TICKET_DOCTYPE)
	q = frappe.qb.from_(HDT).select(
		Count(HDT.name).as_("total"),
		Sum(Case().when(HDT.status == "Replied", 1).else_(0)).as_("replied"),
		Sum(Case().when(HDT.status == "Resolved", 1).else_(0)).as_("resolved"),
		Sum(Case().when(HDT.status == "Closed", 1).else_(0)).as_("closed"),
		Sum(Case().when(HDT.status.isin(OPEN_STATUSES), 1).else_(0)).as_("pending"),
	)
	if has_on_hold:
		q = q.select(Sum(Case().when(HDT.custom_is_on_hold == 1, 1).else_(0)).as_("on_hold"))
	q = _apply_ticket_filters_to_query(q, HDT, filters)
	try:
		rows = q.run(as_dict=True)
	except Exception:
		frappe.log_error("unity dashboard cards aggregate failed; falling back")
		return _dashboard_cards_for_filters_legacy(filters)
	row = rows[0] if rows else {}
	total = int(row.get("total") or 0)
	return {
		"total": total,
		"created": total,
		"pending": int(row.get("pending") or 0),
		"on_hold": int(row.get("on_hold") or 0) if has_on_hold else 0,
		"resolved": int(row.get("resolved") or 0),
		"closed": int(row.get("closed") or 0),
		"replied": int(row.get("replied") or 0),
	}


def _dashboard_cards_for_filters_legacy(filters=None):
	"""Original per-card-count implementation. Kept as a safe fallback if the
	aggregate query ever fails (e.g. dialect mismatch on a non-MariaDB site)."""
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
	# Always-on lookup used by every SPA user via get_ticket_types(). Include
	# `custom_color` when the field exists so TicketDetailView can render the
	# Previous-Tickets type dot without a second round-trip. _has_field is
	# memoised, so the existence check is cheap.
	fields = ["name"]
	if frappe.db.has_column("HD Ticket Type", "custom_color"):
		fields.append("custom_color")
	return frappe.get_all(
		"HD Ticket Type",
		fields=fields,
		order_by="name asc",
		page_length=0,
	)


def _log_hold_reason(ticket_name, hold_reason):
	if not hold_reason:
		return
	safe_reason = frappe.utils.escape_html(cstr(hold_reason).strip())
	frappe.get_doc(
		{
			"doctype": "HD Ticket Comment",
			"commented_by": frappe.session.user,
			"content": f"Hold Reason: {safe_reason}",
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


SUGGESTION_LIMIT = 8
SUGGESTION_MIN_QUERY = 2
SUGGESTION_CANDIDATE_CAP = 60


@frappe.whitelist()
def get_ticket_suggestions(search=None, view="all", limit=SUGGESTION_LIMIT):
	"""Lightweight as-you-type suggestions for the SPA search box.

	Reuses the same candidate-resolver as get_tickets (so family-email expansion
	and permission scoping behave consistently), but caps candidates at 60 rows
	(vs 400 for get_tickets) and skips ranker + decoration for keystroke speed.
	"""
	capabilities = _require_unity_access()
	query = cstr(search or "").strip()
	if len(query) < SUGGESTION_MIN_QUERY:
		return {"data": [], "query": query}

	try:
		limit = int(limit or SUGGESTION_LIMIT)
	except (TypeError, ValueError):
		limit = SUGGESTION_LIMIT
	limit = max(1, min(limit, SUGGESTION_LIMIT))

	effective_view = "all" if view == "all" and capabilities.can_view_all_tickets else "my"
	list_filters = _build_filters(
		"all",  # search reach mirrors get_tickets — permission is enforced via assigned_agent
		None,
		assigned_agent=_session_user() if not capabilities.can_view_all_tickets else None,
	)
	candidate_names = _search_candidate_ticket_names(list_filters, query)
	if not candidate_names:
		return {"data": [], "query": query, "view": effective_view}

	# Pull a bounded slice with the lightweight fields the dropdown needs.
	rows = frappe.get_list(
		TICKET_DOCTYPE,
		fields=["name", "subject", "raised_by", "status", "modified", "custom_is_on_hold"],
		filters=[[TICKET_DOCTYPE, "name", "in", list(candidate_names)]],
		order_by="modified desc",
		page_length=SUGGESTION_CANDIDATE_CAP,
	)

	# Cheap in-Python rank — no full ranker call.
	q_lower = query.lower()
	tokens = _search_tokens(query)

	def _score(row):
		name_lower = cstr(row.get("name")).lower()
		subject_lower = cstr(row.get("subject")).lower()
		if name_lower == q_lower:
			return (3, row.get("modified"))
		if subject_lower.startswith(q_lower):
			return (2, row.get("modified"))
		if tokens and all(t in subject_lower or t in name_lower for t in tokens):
			return (1, row.get("modified"))
		return (0, row.get("modified"))

	rows.sort(key=_score, reverse=True)
	return {"data": rows[:limit], "query": query, "view": effective_view}


def _resolve_ticket_context(view, filters, search, message_body, page_length, start):
	"""Compute the shared context that get_tickets_page and get_tickets_summary
	both need: capabilities, effective view, base filter list, cleaned search
	text, and (when searching) the candidate-name set + family expansion. The
	result is cached on `frappe.local` so the two parallel endpoints don't
	repeat the work twice within a single request — only relevant for the
	back-compat `get_tickets` wrapper, but harmless otherwise.
	"""
	# Cache key embeds the inputs so different filter combinations don't
	# collide within the same request.
	cache_key = (
		"_ticket_context",
		view,
		json.dumps(filters or {}, sort_keys=True, default=str) if filters else "",
		cstr(search or ""),
		cstr(message_body or ""),
	)
	cache = _request_cache()
	cached = cache.get(cache_key)
	if cached is not None:
		# Update pagination on each call so the same context object can be
		# reused with different `start` / `page_length` values cheaply.
		cached = dict(cached)
		cached["page_length"] = int(page_length or 20)
		cached["start"] = int(start or 0)
		return cached

	capabilities = _require_unity_access()
	page_length = int(page_length or 20)
	start = int(start or 0)
	search = cstr(search or message_body or "").strip()
	effective_view = "all" if view == "all" and capabilities.can_view_all_tickets else "my"
	list_filters = _build_filters(
		"all" if search else effective_view,
		filters,
		assigned_agent=_session_user() if not capabilities.can_view_all_tickets else None,
	)
	fields = _ticket_fields(extra=_selected_column_fields())
	search_candidate_names = None
	search_family_terms = None
	if search:
		search_candidate_names = _search_candidate_ticket_names(list_filters, search)
		# When the search is a guardian email, we expanded to the whole family.
		# Pass the expansion to the ranker so it accepts family matches as
		# Tier-2 hits even when the literal email isn't on the ticket.
		if _looks_like_email(search):
			expanded = _expand_email_to_family_search_terms(search)
			if (
				len(expanded.get("emails") or set()) > 1
				or expanded.get("student_refs")
				or expanded.get("student_names")
				or expanded.get("student_ids")
			):
				search_family_terms = expanded

	context = {
		"capabilities": capabilities,
		"effective_view": effective_view,
		"list_filters": list_filters,
		"fields": fields,
		"search": search,
		"search_candidate_names": search_candidate_names,
		"search_family_terms": search_family_terms,
		"page_length": page_length,
		"start": start,
	}
	cache[cache_key] = context
	return context


def _fetch_candidate_rows(context):
	"""For the search path, fetch the candidate-ranked rows once. The candidate
	set is bounded at MAX_SEARCH_CANDIDATES so this is a single small query.
	Result is cached on the context dict for the page + summary endpoints to
	share within the same request."""
	if "candidate_rows" in context:
		return context["candidate_rows"]
	candidate_names = context["search_candidate_names"]
	if not candidate_names:
		context["candidate_rows"] = []
		return []
	rows = frappe.get_list(
		TICKET_DOCTYPE,
		fields=context["fields"],
		filters=_merge_filters(
			context["list_filters"],
			[[TICKET_DOCTYPE, "name", "in", list(candidate_names)]],
		),
		order_by="modified desc",
		page_length=min(max(len(candidate_names), 1), MAX_SEARCH_CANDIDATES),
	)
	context["candidate_rows"] = rows
	return rows


def _compute_tickets_page(context):
	"""Return just the paginated rows for the current view + filters. Drops
	the cards/total_count which the summary endpoint is responsible for —
	keeps this endpoint's response small and its work minimal so the SPA
	can paint the list as soon as it lands.
	"""
	page_length = context["page_length"]
	start = context["start"]
	candidate_names = context["search_candidate_names"]
	search = context["search"]
	fields = context["fields"]
	list_filters = context["list_filters"]
	effective_view = context["effective_view"]

	if candidate_names is not None:
		candidate_rows = _fetch_candidate_rows(context)
		ranked_ids = (
			_ranked_ticket_ids(
				candidate_rows, search, family_terms=context["search_family_terms"]
			)
			if search
			else [
				row.name
				for row in sorted(
					candidate_rows,
					key=lambda row: get_datetime(row.modified),
					reverse=True,
				)
			]
		)
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
	else:
		rows = frappe.get_list(
			TICKET_DOCTYPE,
			fields=fields,
			filters=list_filters,
			order_by="modified desc",
			limit_start=start,
			page_length=page_length,
		)

	return {
		"data": _decorate_ticket_rows(rows),
		"row_count": len(rows),
		"start": start,
		"page_length": page_length,
		"view": effective_view,
	}


_SUMMARY_CACHE_TTL_SECS = 30
_SUMMARY_CACHE_PREFIX = "unity:tickets:summary"


def _summary_cache_key(context):
	"""Per-(user, view, filters) cache key for the dashboard cards.
	Search-path summaries (where candidate_names is set) are not cached —
	they'd diverge per keystroke and the cache would churn. Only the
	empty-search list-page summary is keyed reliably.
	"""
	import hashlib

	filter_json = json.dumps(context.get("list_filters") or [], sort_keys=True, default=str)
	raw = "|".join(
		[
			_SUMMARY_CACHE_PREFIX,
			cstr(context.get("effective_view") or ""),
			filter_json,
			cstr(frappe.session.user or ""),
		]
	)
	return f"{_SUMMARY_CACHE_PREFIX}:{hashlib.md5(raw.encode('utf-8')).hexdigest()}"


def _compute_tickets_summary(context):
	"""Return just total_count + cards for the current view + filters. The
	search path replays the candidate ranking so the cards reflect matches,
	not the full filter set; the empty-search path uses the single SQL
	aggregate in _dashboard_cards_for_filters.

	The empty-search result is Redis-cached for _SUMMARY_CACHE_TTL_SECS
	seconds — the aggregate is a full-table scan on a 90K-row HD Ticket
	table, which is the dominant cost of the list-page first paint when
	the InnoDB buffer pool is cold. KPI cards don't need real-time
	freshness; 30 s of staleness is acceptable, and the first visitor
	within each TTL window primes the cache for the rest.
	"""
	candidate_names = context["search_candidate_names"]
	search = context["search"]
	list_filters = context["list_filters"]

	# Cache only the non-search path: the search path's candidate_rows
	# depend on the query string and would either need per-query cache
	# keys (which churn) or risk stale results.
	cache_key = None
	if candidate_names is None and not search:
		cache_key = _summary_cache_key(context)
		try:
			cached_raw = frappe.cache().get_value(cache_key)
		except Exception:
			cached_raw = None
		if cached_raw:
			try:
				return json.loads(cached_raw)
			except (TypeError, ValueError):
				# Cache value was malformed; fall through to recompute.
				pass

	if candidate_names is not None:
		candidate_rows = _fetch_candidate_rows(context)
		ranked_ids = (
			_ranked_ticket_ids(
				candidate_rows, search, family_terms=context["search_family_terms"]
			)
			if search
			else [row.name for row in candidate_rows]
		)
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
		cards = _dashboard_cards_for_filters(list_filters)
		total_count = int(cards.get("total") or 0)

	result = {"total_count": total_count, "cards": cards}
	if cache_key:
		try:
			frappe.cache().set_value(
				cache_key,
				json.dumps(result, default=str),
				expires_in_sec=_SUMMARY_CACHE_TTL_SECS,
			)
		except Exception:
			# Cache write failures are non-fatal — the response is correct,
			# we just lose the speed-up on the next call.
			pass
	return result


@frappe.whitelist()
def get_tickets_page(view="all", filters=None, search=None, message_body=None, page_length=20, start=0):
	"""Paginated ticket rows for the current view + filters. Companion to
	`get_tickets_summary`. The Unity SPA fires both in parallel so the list
	paints as soon as the page response lands — typically tens of
	milliseconds — without waiting for the dashboard-cards aggregate."""
	context = _resolve_ticket_context(view, filters, search, message_body, page_length, start)
	return _compute_tickets_page(context)


@frappe.whitelist()
def get_tickets_summary(view="all", filters=None, search=None, message_body=None):
	"""Dashboard-card counts (total/pending/on_hold/resolved/closed/replied)
	for the current view + filters. Companion to `get_tickets_page`. Issues
	a single aggregate SQL via `_dashboard_cards_for_filters` (empty-search
	path) or replays the candidate-rank set (search path)."""
	context = _resolve_ticket_context(view, filters, search, message_body, page_length=1, start=0)
	return _compute_tickets_summary(context)


@frappe.whitelist()
def get_tickets(view="all", filters=None, search=None, message_body=None, page_length=20, start=0):
	"""Back-compat wrapper that returns the same shape the previous monolithic
	endpoint did — `{data, total_count, cards, row_count, start, page_length, view}`.
	Internally calls the same helpers as `get_tickets_page` and
	`get_tickets_summary`, sharing the candidate-rows fetch via the
	per-request cache so no extra DB work is paid for back-compat.
	"""
	context = _resolve_ticket_context(view, filters, search, message_body, page_length, start)
	page = _compute_tickets_page(context)
	summary = _compute_tickets_summary(context)
	return {**page, **summary}


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
		# Same ToDo-based fast path the list page uses — avoids the
		# `_assign LIKE '%user%'` full-table scan that previously dominated
		# the dashboard query budget for filtered-by-agent views.
		_apply_assignee_filter(filters, assigned_agent)
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


# Keep in sync with hd_ticket._KEYWORD_CACHE_KEY — the matcher reads the cached
# list and we have to invalidate it when admins edit keywords from the SPA.
_TICKET_TYPE_KEYWORD_CACHE_KEY = "hd_ticket_type_keywords"
_MAX_KEYWORDS_PER_TYPE = 50
_MAX_KEYWORD_LEN = 60


def _parse_keyword_input(keywords):
	# Accept either a list[str] or a comma-separated string. Normalise to a
	# lowercase, deduped, trimmed list capped at _MAX_KEYWORDS_PER_TYPE.
	if isinstance(keywords, str):
		raw = keywords.split(",")
	elif isinstance(keywords, (list, tuple)):
		raw = list(keywords)
	elif keywords is None:
		raw = []
	else:
		raw = _parse_json(keywords, []) or []

	seen = set()
	cleaned = []
	for entry in raw:
		token = cstr(entry or "").strip().lower()
		if not token or token in seen:
			continue
		if len(token) > _MAX_KEYWORD_LEN:
			frappe.throw(
				_("Keyword '{0}' exceeds {1} characters").format(token[:20] + "…", _MAX_KEYWORD_LEN)
			)
		seen.add(token)
		cleaned.append(token)
		if len(cleaned) >= _MAX_KEYWORDS_PER_TYPE:
			break
	return cleaned


@frappe.whitelist()
def list_ticket_types_with_keywords():
	capabilities = _require_unity_access()
	if not capabilities.can_manage_unity_settings:
		frappe.throw(_("You are not allowed to manage ticket types"), frappe.PermissionError)
	fields = ["name", "description", "priority", "keywords"]
	if frappe.db.has_column("HD Ticket Type", "custom_color"):
		fields.append("custom_color")
	rows = frappe.get_all(
		"HD Ticket Type",
		fields=fields,
		order_by="name asc",
	)
	for row in rows:
		row["keywords"] = [
			k.strip().lower()
			for k in cstr(row.get("keywords") or "").split(",")
			if k.strip()
		]
	return rows


@frappe.whitelist()
def update_ticket_type_color(name, color=None):
	"""Set the SPA-displayed colour on an HD Ticket Type. Accepts any value
	that the Frappe Color field accepts (e.g. '#3b82f6'); pass empty string
	to clear. Admins only — same gate as update_ticket_type_keywords.
	"""
	capabilities = _require_unity_access()
	if not capabilities.can_manage_unity_settings:
		frappe.throw(_("You are not allowed to manage ticket types"), frappe.PermissionError)
	name = cstr(name or "").strip()
	if not name:
		frappe.throw(_("Ticket type name is required"))
	if not frappe.db.exists("HD Ticket Type", name):
		frappe.throw(_("Ticket type {0} not found").format(name), frappe.DoesNotExistError)
	color_value = cstr(color or "").strip()
	# Basic shape check; the Frappe Color field stores hex strings.
	# Don't be strict — admins may paste a 3-char or 8-char hex.
	if color_value and not (color_value.startswith("#") and 4 <= len(color_value) <= 9):
		frappe.throw(_("Color must be a hex string like #3b82f6 (got {0})").format(color_value))
	if not frappe.db.has_column("HD Ticket Type", "custom_color"):
		# Silent no-op when the column hasn't been added yet (the schema
		# patch hasn't applied on this site). End users see "Saved" in the
		# SPA instead of a developer-facing 'run `bench migrate`' message;
		# the SPA's loaded ticket-type list will also lack `custom_color`,
		# so the Color column stays hidden until the column actually
		# exists. Server-side log lets the admin diagnose.
		frappe.logger().warning(
			"update_ticket_type_color: custom_color column missing on tabHD Ticket Type; "
			"skipped colour update for %s",
			name,
		)
		return {"name": name, "custom_color": None, "skipped": True}
	frappe.db.set_value("HD Ticket Type", name, "custom_color", color_value or None)
	return {"name": name, "custom_color": color_value or None}


@frappe.whitelist()
def update_ticket_type_keywords(name, keywords=None):
	capabilities = _require_unity_access()
	if not capabilities.can_manage_unity_settings:
		frappe.throw(_("You are not allowed to manage ticket types"), frappe.PermissionError)
	name = cstr(name or "").strip()
	if not name:
		frappe.throw(_("Ticket type name is required"))
	if not frappe.db.exists("HD Ticket Type", name):
		frappe.throw(_("Ticket type {0} not found").format(name), frappe.DoesNotExistError)
	cleaned = _parse_keyword_input(keywords)
	joined = ", ".join(cleaned)
	# set_value bypasses HDTicketType.on_update — invalidate the matcher cache
	# directly so the next ticket pickup sees the new mapping.
	frappe.db.set_value("HD Ticket Type", name, "keywords", joined)
	frappe.cache().delete_value(_TICKET_TYPE_KEYWORD_CACHE_KEY)
	return {"name": name, "keywords": cleaned}


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
	summary_fields = [
		"name",
		"subject",
		"creation",
		"status",
		"ticket_type",
		"custom_is_bulk_email",
		"custom_via_unity_portal",
		"custom_replied_to_ticket",
	]
	if capabilities.can_view_all_tickets:
		return frappe.get_list(
			TICKET_DOCTYPE,
			fields=summary_fields,
			filters={"name": ["in", names]},
			page_length=max(len(names), 1),
		)
	current_user = _session_user()
	rows = frappe.get_list(
		TICKET_DOCTYPE,
		fields=summary_fields + ["_assign"],
		filters={"name": ["in", names]},
		page_length=max(len(names), 1),
	)
	return [
		{k: v for k, v in row.items() if k != "_assign"}
		for row in rows
		if current_user in frappe.parse_json(row.get("_assign") or "[]")
	]


@frappe.whitelist()
def get_bulk_emails_received_by(email):
	"""Return bulk-email audit tickets whose recipient list includes `email`.
	Used by the ticket detail view to surface "we sent this person a bulk email"
	rows alongside their own previous tickets."""
	_require_unity_access()
	email = cstr(email or "").strip().lower()
	if not email or not _has_field(TICKET_DOCTYPE, "custom_bulk_email_recipients"):
		return []
	# LIKE %email% is a full table scan on the Long Text. For the volumes this
	# handles (a parent's history surface — never more than a handful of bulk
	# emails per recipient) the cost is acceptable. If it grows hot we can move
	# to a join table.
	rows = frappe.get_all(
		TICKET_DOCTYPE,
		filters=[
			["custom_is_bulk_email", "=", 1],
			["custom_bulk_email_recipients", "like", f"%{email}%"],
		],
		fields=[
			"name",
			"subject",
			"creation",
			"status",
			"ticket_type",
		],
		order_by="creation desc",
		page_length=50,
	)
	return rows


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


ALLOWED_BULK_FIELDS = {"status", "priority", "_assign", "ticket_type", "agent_group"}
# Fields whose changes don't trigger controller side effects (activity log,
# search index rebuild, SLA recalculation). We can skip `doc.save()` for these
# and do a raw column update — orders of magnitude faster at bulk scale.
BULK_FAST_PATH_FIELDS = {"priority", "ticket_type", "agent_group"}
BULK_UPDATE_MAX = 500


@frappe.whitelist()
def bulk_update_tickets(names, field, value=None):
	"""Apply a single-field update to many HD Tickets in one request.

	Matches the ergonomics of Frappe's bulk-edit but tailored for the Unity
	SPA: synchronous up to `BULK_UPDATE_MAX`, returns structured
	`{updated, failed}` so the UI can render an exact result count instead of
	leaving the user with a `msgprint`.

	For fields in `BULK_FAST_PATH_FIELDS` we issue a raw `db.set_value` per
	ticket — skipping `doc.save()` avoids the per-row search-index rebuild
	(see `HDTicket.on_update`), which is the dominant cost at 500-row scale."""
	capabilities = _require_unity_access()
	field_name = cstr(field or "").strip()
	if field_name not in ALLOWED_BULK_FIELDS:
		frappe.throw(_("Field {0} is not allowed for bulk edit").format(field_name or "<empty>"))
	if isinstance(names, str):
		names = _parse_json(names, [])
	if not isinstance(names, list) or not names:
		frappe.throw(_("Select at least one ticket"))
	if len(names) > BULK_UPDATE_MAX:
		frappe.throw(_("Bulk edit supports up to {0} tickets at a time").format(BULK_UPDATE_MAX))

	if field_name == "status":
		# Allow "On Hold" as a virtual status — mirrors `update_ticket`.
		if value and value not in STATUS_OPTIONS and value != "On Hold":
			frappe.throw(_("Invalid ticket status"))

	clean_value = value if value not in ("",) else None
	updated, failed = [], []
	for name in names:
		try:
			_require_ticket_access(name, capabilities)
			# `_require_ticket_access` only verifies existence for non-admins;
			# admins get an early-return. Verify here so the fast-path
			# `frappe.db.set_value` (which silently no-ops on missing rows)
			# can't mask a bad ticket name as a success.
			if not frappe.db.exists(TICKET_DOCTYPE, name):
				raise frappe.DoesNotExistError(_("Ticket not found"))
			if field_name == "_assign":
				if clean_value:
					assign_ticket_to_agent(name, clean_value)
				else:
					clear_all_assignments(TICKET_DOCTYPE, name)
			elif field_name == "status":
				ticket = frappe.get_doc(TICKET_DOCTYPE, name)
				if clean_value == "On Hold":
					if _has_field(TICKET_DOCTYPE, "custom_is_on_hold"):
						ticket.custom_is_on_hold = 1
					if ticket.status in FINAL_STATUSES or not ticket.status:
						ticket.status = "Open"
				else:
					ticket.status = clean_value
					if _has_field(TICKET_DOCTYPE, "custom_is_on_hold"):
						ticket.custom_is_on_hold = 0
				ticket.save()
			elif field_name in BULK_FAST_PATH_FIELDS:
				# Raw column update — bypasses `on_update` which rebuilds the
				# search index on every save. None of these fields participate
				# in search/SLA, so it's safe to skip the controller.
				frappe.db.set_value(TICKET_DOCTYPE, name, field_name, clean_value)
			else:
				ticket = frappe.get_doc(TICKET_DOCTYPE, name)
				ticket.set(field_name, clean_value)
				ticket.save()
			updated.append(name)
		except frappe.PermissionError:
			failed.append({"name": name, "reason": "permission_denied"})
		except Exception as exc:
			frappe.log_error(f"bulk_update_tickets failed for {name}: {exc}", "bulk_update_tickets")
			failed.append({"name": name, "reason": cstr(exc)[:200] or "error"})

	frappe.db.commit()
	return {"updated": updated, "failed": failed}


@frappe.whitelist()
def reply(name, message, cc=None, bcc=None, attachments=None):
	capabilities = _require_unity_access()
	_require_ticket_access(name, capabilities)
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
	base_filters = _reminder_ticket_filter(reminder_after_days)
	_BATCH_SIZE = 200
	start = 0

	while True:
		tickets = frappe.get_list(
			TICKET_DOCTYPE,
			fields=["name", "subject", "raised_by", "_assign"],
			filters=base_filters,
			page_length=_BATCH_SIZE,
			limit_start=start,
			order_by="modified asc",
		)
		if not tickets:
			break

		for ticket in tickets:
			for assignee in frappe.parse_json(ticket._assign or "[]"):
				if not assignee:
					continue
				safe_name = frappe.utils.escape_html(cstr(ticket.name))
				frappe.sendmail(
					recipients=[assignee],
					subject=f"Reminder: Ticket #{safe_name} is still open",
					message=(
						f"<p>Ticket <b>#{safe_name}</b> has been open for at least "
						f"{reminder_after_days} days.</p>"
						f"<p><b>Subject:</b> {frappe.utils.escape_html(ticket.subject or '')}</p>"
						f"<p><a href='{get_url('/unity-helpdesk/tickets/' + cstr(ticket.name))}'>Open ticket</a></p>"
					),
					delayed=True,
					reference_doctype=TICKET_DOCTYPE,
					reference_name=ticket.name,
				)

		start += _BATCH_SIZE
		if len(tickets) < _BATCH_SIZE:
			break


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
			"column_preferences": _load_column_preferences(),
		},
		"available_columns": _localized_available_columns(),
	}


@frappe.whitelist()
def update_column_preferences(column_preferences):
	# Per-user; gated on basic Unity access (not super-admin).
	_require_unity_access()
	try:
		parsed = json.loads(column_preferences) if isinstance(column_preferences, str) else column_preferences
	except (TypeError, ValueError):
		frappe.throw(_("Invalid column preferences payload"))
	if not isinstance(parsed, list):
		frappe.throw(_("Invalid column preferences payload"))
	if len(parsed) > COLUMN_PREFS_MAX_ITEMS:
		frappe.throw(_("Too many column preferences (max {0})").format(COLUMN_PREFS_MAX_ITEMS))
	cleaned = []
	seen = set()
	for item in parsed:
		if not isinstance(item, dict):
			continue
		key = cstr(item.get("key") or "").strip()
		if not key or key in seen or key not in AVAILABLE_TICKET_COLUMN_KEYS:
			continue
		seen.add(key)
		try:
			width = int(item.get("width") or 0)
		except (TypeError, ValueError):
			width = 0
		if width < COLUMN_WIDTH_MIN:
			width = COLUMN_WIDTH_MIN
		elif width > COLUMN_WIDTH_MAX:
			width = COLUMN_WIDTH_MAX
		cleaned.append({"key": key, "width": width})
	# Force fixed columns to be present.
	fixed_keys = [c["key"] for c in AVAILABLE_TICKET_COLUMNS if c["fixed"]]
	for key in reversed(fixed_keys):
		if key in seen:
			continue
		width = next(c["width"] for c in AVAILABLE_TICKET_COLUMNS if c["key"] == key)
		cleaned.insert(0, {"key": key, "width": width})
		seen.add(key)
	if not cleaned:
		cleaned = _default_column_preferences()
	frappe.defaults.set_user_default(COLUMN_PREFS_DEFAULT_KEY, json.dumps(cleaned))
	return {"column_preferences": cleaned}


@frappe.whitelist()
def update_unity_settings(unity_email_thread_layout=None):
	capabilities = _require_unity_access()
	if not capabilities.can_manage_unity_settings:
		frappe.throw(_("You are not allowed to manage Unity Helpdesk settings"), frappe.PermissionError)

	layout = _normalize_thread_layout(unity_email_thread_layout or _default_thread_layout())
	if layout not in {"Classic", "Chat Based"}:
		frappe.throw(_("Invalid email thread layout"))

	# Set only the Unity field via db, instead of a full HD Settings doc save:
	# a full save validates unrelated required fields (e.g. default_ticket_status,
	# ticket_reopen_status) that can be unset on sites migrated from older Helpdesk.
	frappe.db.set_single_value("HD Settings", "unity_email_thread_layout", layout)
	return {"unity_email_thread_layout": layout}


@frappe.whitelist()
def search_contacts(query):
	"""Search guardians/contacts by name or email for bulk email recipient picker."""
	import re as _re
	q = cstr(query or "").strip()
	if len(q) < 2:
		return []
	_email_re = _re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
	results = []
	seen = set()

	def _add(email, name):
		e = cstr(email or "").strip().lower()
		if e and _email_re.match(e) and e not in seen:
			seen.add(e)
			results.append({"email": e, "name": name or e})

	# Search Guardian (parents) by name
	for g in frappe.get_all(
		"Guardian",
		filters=[["guardian_name", "like", f"%{q}%"]],
		fields=["guardian_name", "email_address"],
		limit_page_length=10,
	):
		_add(g.email_address, g.guardian_name)

	# Search Guardian by email
	for g in frappe.get_all(
		"Guardian",
		filters=[["email_address", "like", f"%{q}%"]],
		fields=["guardian_name", "email_address"],
		limit_page_length=5,
	):
		_add(g.email_address, g.guardian_name)

	# Search Contact by name
	for c in frappe.get_all(
		"Contact",
		filters=[["full_name", "like", f"%{q}%"]],
		fields=["full_name", "email_id"],
		limit_page_length=8,
	):
		_add(c.email_id, c.full_name)

	# Search Contact by email
	for c in frappe.get_all(
		"Contact",
		filters=[["email_id", "like", f"%{q}%"]],
		fields=["full_name", "email_id"],
		limit_page_length=5,
	):
		_add(c.email_id, c.full_name)

	return results[:15]


# Bound the worst-case row fetch when looking up guardians. A real school
# has a few thousand Student Guardian rows total; a misconfigured site (or
# a query with thousands of student ids) shouldn't be allowed to
# materialise an unbounded result set into worker memory. Above this we
# log and truncate.
_GUARDIAN_LOOKUP_HARD_CAP = 5000


def _guardian_emails_for_student_ids(student_ids):
	"""Return {student_name: [guardian_email, ...]} for the given Student ids.

	Mirrors the Student → Student Guardian → Guardian join used by
	get_student_context_for_ticket (around line 620). Prefers Guardian.email_address
	over the denormalized Student Guardian.email row. Per-student dedupe + lowercase;
	guardians with no email are skipped. Cross-student dedupe is the caller's job.

	Per-request cached on `frappe.local` — the bulk-email composer triggers
	this on every BCC keystroke for repeated student id sets; the cache
	collapses N consecutive identical lookups into one DB round-trip.
	"""
	import re as _re

	ids = sorted({cstr(sid).strip() for sid in (student_ids or []) if cstr(sid).strip()})
	if not ids:
		return {}

	# Per-request memoization keyed on the sorted id tuple. Cleared by Frappe
	# between requests via frappe.local lifecycle.
	cache = _request_cache().setdefault("_guardian_emails_for_student_ids", {})
	cache_key = tuple(ids)
	if cache_key in cache:
		return cache[cache_key]

	_email_re = _re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

	rows = frappe.get_all(
		"Student Guardian",
		fields=["parent", "guardian", "email"],
		filters={"parenttype": "Student", "parent": ["in", ids]},
		# Hard cap (vs. previous page_length=0). For a real school the join
		# is in the low thousands at most; anything above this is a sign of
		# misconfiguration and we'd rather log and truncate than OOM the worker.
		page_length=_GUARDIAN_LOOKUP_HARD_CAP,
	)
	if len(rows) >= _GUARDIAN_LOOKUP_HARD_CAP:
		frappe.logger().warning(
			f"_guardian_emails_for_student_ids: Student Guardian rows hit cap "
			f"({_GUARDIAN_LOOKUP_HARD_CAP}) for {len(ids)} student ids; "
			f"results may be truncated"
		)
	guardian_ids = sorted({cstr(r.guardian).strip() for r in rows if cstr(r.guardian).strip()})
	guardian_email_by_id = {}
	if guardian_ids:
		for g in frappe.get_all(
			"Guardian",
			fields=["name", "email_address"],
			filters={"name": ["in", guardian_ids]},
			page_length=len(guardian_ids) + 1,
		):
			guardian_email_by_id[g.name] = cstr(g.email_address or "").strip().lower()

	result = {}
	for row in rows:
		sid = cstr(row.parent).strip()
		gid = cstr(row.guardian).strip()
		email = guardian_email_by_id.get(gid) or cstr(row.email or "").strip().lower()
		if not email or not _email_re.match(email):
			continue
		bucket = result.setdefault(sid, [])
		if email not in bucket:
			bucket.append(email)
	cache[cache_key] = result
	return result


@frappe.whitelist()
def get_student_guardian_emails(student_emails):
	"""Look up guardian emails for a list of student emails.

	Used by the bulk-email composer to auto-populate BCC with each student's
	guardian emails when a recipient matches a Student.student_email_id.

	Response shape (changed from a bare {email: [guardians]} dict):

	    {
	      "mapping": {student_email: [guardian_email, ...], ...},
	      "diagnostic": {
	        "input_count":            <int>,
	        "students_matched":       <int>,  # rows where student_email_id matched
	        "students_with_guardians": <int>, # students that yielded guardian emails
	        "unmatched_emails":       [...],  # input emails with no Student record
	      },
	    }

	The diagnostic block lets the SPA surface a non-blocking warning when
	the auto-fill silently produces nothing (e.g. wrong student_email_id
	on the env, missing Student Guardian rows). Previously the empty
	result was indistinguishable from "no guardians on file" and
	swallowed silently in App.vue:996.
	"""
	_require_unity_access()

	import re as _re

	raw = _parse_json(student_emails, []) or []
	if isinstance(raw, str):
		raw = [raw]
	if not isinstance(raw, (list, tuple)):
		return {"mapping": {}, "diagnostic": {
			"input_count": 0,
			"students_matched": 0,
			"students_with_guardians": 0,
			"unmatched_emails": [],
		}}

	_email_re = _re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
	normalized = []
	seen = set()
	for value in raw:
		email = cstr(value or "").strip().lower()
		if not email or not _email_re.match(email) or email in seen:
			continue
		seen.add(email)
		normalized.append(email)

	def _empty(unmatched=None):
		return {
			"mapping": {},
			"diagnostic": {
				"input_count": len(normalized),
				"students_matched": 0,
				"students_with_guardians": 0,
				"unmatched_emails": list(unmatched or normalized),
			},
		}

	if not normalized:
		return _empty()

	students = frappe.get_all(
		"Student",
		fields=["name", "student_email_id"],
		filters={"student_email_id": ["in", normalized]},
		page_length=len(normalized) + 1,
	)
	if not students:
		return _empty()

	email_by_student_id = {
		s.name: cstr(s.student_email_id or "").strip().lower() for s in students
	}
	matched_emails = set(email_by_student_id.values())
	unmatched_emails = [e for e in normalized if e not in matched_emails]
	guardians_by_student_id = _guardian_emails_for_student_ids(list(email_by_student_id.keys()))

	mapping = {}
	for student_id, student_email in email_by_student_id.items():
		guardians = guardians_by_student_id.get(student_id) or []
		if not guardians:
			continue
		mapping[student_email] = guardians
	return {
		"mapping": mapping,
		"diagnostic": {
			"input_count": len(normalized),
			"students_matched": len(students),
			"students_with_guardians": len(mapping),
			"unmatched_emails": unmatched_emails,
		},
	}


@frappe.whitelist()
def get_csrf_token():
	"""Return the current session CSRF token. Callers must be authenticated;
	the SPA refreshes the token on 403 mid-session."""
	return frappe.sessions.get_csrf_token()


@frappe.whitelist()
def enqueue_auto_assign_ticket_types():
	"""Enqueue a background job to bulk-assign ticket types by keyword matching.

	Dedupes via job_id + deduplicate=True so concurrent clicks don't queue
	two racing workers against the same rows.
	"""
	frappe.only_for("System Manager")
	frappe.enqueue(
		"helpdesk.api.unity_helpdesk._bulk_auto_assign_ticket_types",
		queue="long",
		timeout=3600,
		is_async=True,
		job_id="auto_assign_ticket_types",
		deduplicate=True,
	)
	return {"queued": True}


def _bulk_auto_assign_ticket_types():
	"""Process tickets with empty/Unspecified ticket_type. Snapshot names upfront so
	matched rows leaving the filter set don't shift pagination and skip unmatched ones."""
	from helpdesk.helpdesk.doctype.hd_ticket.hd_ticket import (
		_get_ticket_type_keyword_map,
		_match_ticket_type_by_keywords,
	)

	keyword_map = _get_ticket_type_keyword_map()
	if not keyword_map:
		return

	names = frappe.get_all(
		"HD Ticket",
		filters=[["ticket_type", "in", ["", "Unspecified"]]],
		pluck="name",
		order_by="creation asc",
	)

	batch_size = 200
	for i in range(0, len(names), batch_size):
		batch = names[i : i + batch_size]
		rows = frappe.get_all(
			"HD Ticket",
			filters=[["name", "in", batch]],
			fields=["name", "subject", "description"],
		)
		for t in rows:
			text = f"{t.subject or ''} {t.description or ''}"
			match = _match_ticket_type_by_keywords(text, keyword_map)
			if match:
				frappe.db.set_value("HD Ticket", t.name, "ticket_type", match[0])
		frappe.db.commit()
