import contextlib
import functools
import html
import json
import re
from collections import defaultdict

import frappe
from frappe import _
from frappe.desk.form.assign_to import add as assign_to_add, clear as clear_all_assignments
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
# Candidate ticket rows fetched and ranked per search. Raised from 400 → 1000 so
# broad/common queries are far less likely to silently drop the target ticket
# below the cap. Ranking happens in Python over this bounded set, so the cost is
# one indexed fetch + an O(n log n) sort — still fast at 1000.
MAX_SEARCH_CANDIDATES = 1000
# Top-N most-recently-assigned tickets we resolve via ToDo for the "My
# Tickets" / "Assigned: X" filters. Set high enough that no real human
# user can hit it (the typical agent has dozens to low-hundreds of open
# assignments). The result is still ordered by HD Ticket.modified for
# display, so this only bounds the candidate set, not the page that
# renders. Truncation here is preferable to falling back to the legacy
# `_assign LIKE '%user%'` full-table scan, which is what made the SPA's
# first paint hit 20–30 s.
MAX_ASSIGNED_LOOKUP = 25000
# Hard cap on rows returned by the ticket list endpoints — matches the largest
# page-size option in the SPA and protects the query from unbounded fetches.
MAX_TICKET_PAGE_LENGTH = 500
UNITY_TICKET_FIELDS = [
	"name",
	"subject",
	"raised_by",
	# HD Ticket.owner = the user who created the ticket. Fetched so the "Created By"
	# list column can resolve it to the creator's full name (_decorate_ticket_rows).
	"owner",
	"status",
	"priority",
	"ticket_type",
	"agent_group",
	# `description` is the full ticket HTML (often tens of KB of student/fee
	# tables). It's fetched because the single-ticket / detail callers of
	# _ticket_fields() (TicketDetailView renders it for bulk-email tickets) need
	# it — but it's stripped from the *list* response via
	# LIST_RESPONSE_EXCLUDED_FIELDS, since the ticket list never renders it and
	# shipping it per row made a guardian-email search return ~1 MB/page and blow
	# the SPA's 20 s search timeout.
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

# Fields fetched for the candidate set because search ranking reads them
# (`legacy_content_fields` in _ticket_search_documents) or because they are the
# raw/HTML twin of a lighter field the SPA does use — but which the ticket *list*
# never renders. They are stripped from the page response, after ranking has
# already consumed them, so a search matching hundreds of tickets doesn't ship
# megabytes of unused HTML to the browser (the root cause of the search timeout).
# The detail view fetches its own copy of these via the single-ticket endpoint.
# Anything here MUST stay out of AVAILABLE_TICKET_COLUMNS (none are user columns).
LIST_RESPONSE_EXCLUDED_FIELDS = (
	# Full ticket HTML — only the detail view (_decorate_ticket, singular) renders
	# it; the list never does. Biggest single contributor to the search payload.
	"description",
	"custom_list_of_student",
	"custom_all_fees_details_of_students",
	"custom_payment_schedule",
	"custom_student_remark",
	"custom_previous_ticket_details",
	# Heavy HTML twin of custom_primary_message_text (which the SPA *does* use).
	"custom_primary_message_html",
	# Ranking-only index fields the list SPA never reads (it uses
	# custom_search_student_names for the student chips, nothing else here).
	"custom_search_message_body",
	"custom_search_student_refs",
	"custom_search_guardian_emails",
)


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
	# Virtual column rendered by the SPA from subject + ticket_type + _assign +
	# creation (all already fetched). `virtual` => never queried as a HD Ticket field.
	{"key": "summary", "label": "Summary", "default": False, "fixed": False, "width": 360, "virtual": True},
	{"key": "ticket_type", "label": "Ticket Type", "default": True, "fixed": False, "width": 150},
	{"key": "priority", "label": "Priority", "default": True, "fixed": False, "width": 130},
	{"key": "status", "label": "Status", "default": True, "fixed": False, "width": 140},
	{"key": "_assign", "label": "Assigned To", "default": True, "fixed": False, "width": 170},
	{"key": "creation", "label": "Created On", "default": True, "fixed": False, "width": 130},
	# Relative-time twin of the column above ("2 months ago"), worded like the
	# upstream Helpdesk list. Virtual: it's rendered client-side from `creation`,
	# which is always fetched (UNITY_TICKET_FIELDS), so `virtual` keeps it out of
	# _selected_column_fields() and — the reason it matters — out of the SPA's
	# columnNeedsFetch() reload path. There's no HD Ticket field to backfill, so
	# adding this column must never trigger a refetch. Same for `modified_age`.
	{"key": "creation_age", "label": "Created", "default": True, "fixed": False, "width": 130, "virtual": True},
	# HD Ticket.owner (creator). The SPA renders the resolved full name from
	# row.created_by (see _decorate_ticket_rows), falling back to the raw owner email.
	{"key": "owner", "label": "Created By", "default": True, "fixed": False, "width": 180},
	{"key": "custom_is_on_hold", "label": "Issues On Hold", "default": True, "fixed": False, "width": 140},
	{"key": "custom_hold_reason", "label": "Reason Of Hold", "default": True, "fixed": False, "width": 200},
	{"key": "raised_by", "label": "Raised By", "default": False, "fixed": False, "width": 220},
	{"key": "agent_group", "label": "Agent Group", "default": False, "fixed": False, "width": 150},
	{"key": "modified", "label": "Last Updated", "default": False, "fixed": False, "width": 130},
	# Relative-time twin of "Last Updated" — `modified` is likewise always fetched.
	{"key": "modified_age", "label": "Last Modified", "default": True, "fixed": False, "width": 130, "virtual": True},
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

# Default columns introduced AFTER users already had saved column preferences. We
# inject each ONCE (guarded by a per-user flag) so it appears for existing users
# without their manual action — while still letting them hide it afterwards (the
# flag stops us re-adding it on every load). Users with no saved prefs already get
# these via _default_column_preferences() since they're marked default:True.
NEWLY_DEFAULTED_COLUMNS = ["owner", "creation_age", "modified_age"]
COLUMN_AUTOADD_DEFAULT_KEY = "unity_helpdesk_columns_autoadded"


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
	# One-time injection of columns added after this user saved their preferences.
	cleaned = _inject_new_default_columns(cleaned, seen)
	return cleaned or _default_column_preferences()


def _inject_new_default_columns(cleaned, seen):
	"""Append any NEWLY_DEFAULTED_COLUMNS the user hasn't seen yet, once. Guarded by a
	per-user flag so hiding the column afterwards sticks. Best-effort: never raises."""
	pending = [
		k for k in NEWLY_DEFAULTED_COLUMNS
		if k in AVAILABLE_TICKET_COLUMN_KEYS and k not in seen
	]
	if not pending:
		return cleaned
	already = set()
	try:
		raw = frappe.db.get_default(COLUMN_AUTOADD_DEFAULT_KEY, frappe.session.user)
		if raw:
			val = json.loads(raw) if isinstance(raw, str) else raw
			if isinstance(val, list | tuple):
				already = {cstr(x) for x in val}
	except (TypeError, ValueError):
		already = set()
	to_add = [k for k in pending if k not in already]
	if not to_add:
		return cleaned
	for key in to_add:
		width = next((c["width"] for c in AVAILABLE_TICKET_COLUMNS if c["key"] == key), 160)
		cleaned.append({"key": key, "width": width})
		seen.add(key)
	try:
		frappe.db.set_default(
			COLUMN_AUTOADD_DEFAULT_KEY,
			json.dumps(sorted(already | set(to_add))),
			frappe.session.user,
		)
	except Exception:
		pass
	return cleaned


def _selected_column_fields():
	# Returns the HD Ticket fieldnames a user's column choice depends on, so
	# get_tickets() can fetch them. Virtual keys (e.g. "summary") are composed by
	# the SPA from already-fetched fields, so they're skipped here — passing them as
	# fields would make frappe.get_list throw on an unknown column.
	virtual = {c["key"] for c in AVAILABLE_TICKET_COLUMNS if c.get("virtual")}
	return [pref["key"] for pref in _load_column_preferences() if pref["key"] not in virtual]


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
		# owner (creator) is resolved to a full name for the "Created By" column.
		if row_dict.get("owner"):
			users_needed.add(row_dict.get("owner"))

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
		# "Created By": resolve owner -> full name (fallback the raw owner id/email).
		owner = row_dict.get("owner")
		if owner:
			ou = user_map.get(owner)
			row_dict.created_by = {
				"full_name": (ou.get("full_name") if ou else "") or owner,
				"email": (ou.get("email") if ou else "") or owner,
			}
		else:
			row_dict.created_by = None
		row_dict.status_indicator = _status_indicator(row_dict)
		row_dict.priority_target = PRIORITY_TARGETS.get(row_dict.get("priority"), "")
		# Drop the heavy ranking-only / HTML fields before they hit the wire.
		# Ranking already ran on the candidate rows (search path) and the
		# non-search path never needed them — see LIST_RESPONSE_EXCLUDED_FIELDS.
		for excluded in LIST_RESPONSE_EXCLUDED_FIELDS:
			row_dict.pop(excluded, None)
		decorated.append(row_dict)
	return decorated


def _normalize_search_text(value):
	text = html.unescape(cstr(value or ""))
	text = re.sub(r"<[^>]+>", " ", text)
	text = re.sub(r"&nbsp;", " ", text, flags=re.IGNORECASE)
	text = re.sub(r"\s+", " ", text)
	return text.strip().lower()


def _search_tokens(value):
	# `\w` matches Unicode word characters, so accented names (e.g. "José",
	# "Bhavnâ") tokenize as ONE whole word instead of being split/truncated at
	# the accent the way the old [a-z0-9...] class did. Query-side only — the
	# stored search index is unchanged, so no re-backfill is needed. `@ . -` are
	# kept so emails and refs (ta-16@x.edu) stay intact.
	return [token for token in re.findall(r"[\w@.-]+", _normalize_search_text(value)) if token]


def _like_pattern(value):
	return f"%{cstr(value or '').strip()}%"


def _ticket_message_search_fields():
	return [
		field
		for field in [
			"custom_primary_message_html",
			"custom_primary_message_text",
			"custom_search_message_body",
			"custom_search_recipient_emails",
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


def _pick_program_enrollment(rows, current_year=None):
	if not rows:
		return None, []

	sorted_rows = sorted(
		[frappe._dict(row) for row in rows],
		key=lambda row: (get_datetime(row.get("modified")), cstr(row.get("name"))),
		reverse=True,
	)

	def _select(pool):
		submitted = [row for row in pool if int(row.get("docstatus") or 0) == 1]
		return (submitted[0] if submitted else pool[0]), bool(submitted)

	# Prefer the current academic year when the student has a NON-CANCELLED row this
	# year (the normal current-student case). Alumni / left / cancelled / not-yet-
	# re-enrolled students have no usable current-year row, so fall back to their
	# LATEST academic year — a cancelled (docstatus=2) current-year row must NOT
	# pin the card to the current year.
	if current_year:
		current_pool = [
			row
			for row in sorted_rows
			if cstr(row.get("academic_year")) == cstr(current_year)
			and int(row.get("docstatus") or 0) != 2
		]
		if current_pool:
			selected, has_submitted = _select(current_pool)
			return selected, ([] if has_submitted else ["Current-year enrollment is not submitted yet"])

	# Fallback: the student's most-recent enrolled YEAR — cancelled or not (the
	# Status column separately shows Alumni/Cancelled). Rank by latest academic year
	# first, then a submitted row within that year, then most-recently modified. Using
	# the latest YEAR (not the latest-modified row) means an older year re-saved
	# recently can't win, and a student whose latest enrolment was cancelled still
	# shows that latest year (e.g. a cancelled 2025-2026 shows 2025-2026, not an older
	# 2024-2025).
	selected = sorted(
		sorted_rows,
		key=lambda row: (
			cstr(row.get("academic_year")),
			int(row.get("docstatus") or 0) == 1,
			get_datetime(row.get("modified")),
			cstr(row.get("name")),
		),
		reverse=True,
	)[0]
	return selected, []


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
			"confirm_for_next_year",
			"possible_dropout",
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
		# Fetch ALL of the student's enrolments (no academic-year filter). The picker
		# prefers the current year for current students and falls back to the latest
		# year for Alumni / Cancelled / not-yet-re-enrolled students.
		filters={"student": ["in", all_student_ids]} if all_student_ids else {"name": "__none"},
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
		# All fee records for the students; _pick_fee_record selects the one linked
		# to the chosen enrolment (so the year matches the enrolment shown).
		filters={"student": ["in", all_student_ids]} if all_student_ids else {"name": "__none"},
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
			enrollments_by_student.get(student_id, []), current_year
		)
		status_messages.extend(enrollment_messages)
		if not selected_enrollment:
			status_messages.append("No enrollment found")

		selected_fee = _pick_fee_record(
			fees_by_student.get(student_id, []),
			selected_enrollment.get("name") if selected_enrollment else None,
		)
		if not selected_fee:
			status_messages.append("No fees record found")
		elif (
			selected_enrollment
			and cstr(selected_fee.get("program_enrollment")).strip()
			and cstr(selected_fee.get("program_enrollment")).strip() != cstr(selected_enrollment.get("name")).strip()
		):
			status_messages.append("Fees record is not linked to the selected enrollment")

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
				"confirm_for_next_year": student.get("confirm_for_next_year") or "",
				"possible_dropout": bool(int(student.get("possible_dropout") or 0)),
				"student_mobile_number": student.get("student_mobile_number"),
				"primary_contact": student.get("primary_contact"),
				"whatsapp_number": student.get("whatsapp_number"),
				"is_sibling_in_school": bool(int(student.get("is_sibling_in_school") or 0)),
				"guardian_ids": student_guardian_ids,
				"guardian_names": sorted(set(guardian_names)),
				"guardian_emails": sorted(set(guardian_emails)),
				"guardians": guardian_cards,
				"reference_number": student.get("reference_number"),
				# The academic year this student is actually shown for — the current
				# year for current students, else their latest enrolled year (Alumni/
				# Cancelled). The SPA shows this per student instead of the global year.
				"academic_year": (
					selected_enrollment.get("academic_year") if selected_enrollment else None
				),
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


def _search_column_limit(column, default=140):
	"""Character capacity of an HD Ticket search column, read from the DB itself.

	These fields were declared as Frappe `Data` with no explicit `length`, so the
	column is VARCHAR(140) — NOT the 255 this writer used to assume. Overflowing it
	raised MySQL 1406, and because every search field is written in ONE set_value,
	that one oversized value failed the whole update: the ticket kept NULL search
	fields and became invisible to search (~97K tickets on UAT). Truncating to the
	column's real width makes the write impossible to overflow on any schema — so
	this stays correct whether or not the widening patch has run on a given site.
	"""
	cache = _request_cache().setdefault("_search_column_limit", {})
	if column not in cache:
		limit = default
		try:
			rows = frappe.db.sql(
				"""SELECT CHARACTER_MAXIMUM_LENGTH FROM information_schema.COLUMNS
				   WHERE TABLE_SCHEMA = DATABASE()
				     AND TABLE_NAME = %s AND COLUMN_NAME = %s""",
				(f"tab{TICKET_DOCTYPE}", column),
			)
			if rows and rows[0][0]:
				limit = int(rows[0][0])
		except Exception:
			# Never let a schema probe break indexing — fall back to the safe minimum.
			pass
		cache[column] = limit
	return cache[column]


def populate_ticket_student_search_fields(ticket):
	# `ticket` may be an HD Ticket doc, or a name passed as a str OR an int. HD
	# Ticket names are numeric, so `frappe.get_all(...).name` comes back as an int
	# (the student-search backfill passes it straight in). Treat any non-doc value
	# as a name to fetch, so an int name doesn't crash on `.get()` below.
	if isinstance(ticket, (str, int)):
		ticket_doc = frappe.get_doc(TICKET_DOCTYPE, cstr(ticket))
	else:
		ticket_doc = ticket
	if not ticket_doc or not cstr(ticket_doc.get("raised_by")).strip():
		ticket_name = cstr(ticket_doc.name if ticket_doc else ticket).strip()
		if ticket_name:
			# Stamp the student-search fields empty (non-NULL) so a ticket with no
			# raised_by is marked PROCESSED and drops out of the backfill's IS NULL
			# pending set instead of being re-queried forever.
			blank_update = {
				field: ""
				for field in (
					"custom_search_student_names",
					"custom_search_student_refs",
					"custom_search_guardian_emails",
				)
				if frappe.db.has_column(TICKET_DOCTYPE, field)
			}
			if blank_update:
				frappe.db.set_value(
					TICKET_DOCTYPE, ticket_name, blank_update, update_modified=False
				)
			update_ticket_message_search_index(ticket_name, ticket_doc=ticket_doc)
		return {}

	context = get_student_context_for_ticket(ticket_doc.name, ticket_doc.raised_by)
	search_update = {}
	# Truncate every value to the column's ACTUAL width (see _search_column_limit).
	# Hardcoding 255 here while the columns were VARCHAR(140) is what raised MySQL
	# 1406 and left tickets unindexed.
	if frappe.db.has_column(TICKET_DOCTYPE, "custom_search_student_names"):
		search_update["custom_search_student_names"] = ", ".join(
			sorted(
				{
					cstr(student.get("student_name")).strip()
					for student in context.get("students", [])
					if cstr(student.get("student_name")).strip()
				}
			)
		)[: _search_column_limit("custom_search_student_names")]
	if frappe.db.has_column(TICKET_DOCTYPE, "custom_search_student_refs"):
		search_update["custom_search_student_refs"] = ", ".join(
			sorted(
				{
					cstr(student.get("reference_number")).strip()
					for student in context.get("students", [])
					if cstr(student.get("reference_number")).strip()
				}
			)
		)[: _search_column_limit("custom_search_student_refs")]
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
		search_update["custom_search_guardian_emails"] = ", ".join(sorted(emails))[
			: _search_column_limit("custom_search_guardian_emails")
		]
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


# Max chars stored in custom_search_recipient_emails (a Small Text field). Caps
# the comma-joined recipient set so the per-row LIKE recipient probe stays cheap
# and the column never bloats — ~60+ emails, far beyond any real ticket.
RECIPIENT_EMAILS_MAX = 2000
_EMAIL_EXTRACT_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


@functools.lru_cache(maxsize=1)
def _support_inbox_emails():
	"""Lowercased set of the helpdesk's own mailbox addresses (every Email Account
	email_id, e.g. feedback@walnutedu.in). Stripped from the denormalised recipient
	set so a support inbox — a recipient on essentially every ticket — doesn't make
	every ticket match. Cached for the worker's lifetime (Email Accounts are stable;
	a migrate/worker restart refreshes it)."""
	emails = set()
	try:
		for row in frappe.get_all("Email Account", fields=["email_id"], page_length=0):
			value = cstr(row.get("email_id") or "").strip().lower()
			if value:
				emails.add(value)
	except Exception:
		pass
	return frozenset(emails)


def _build_ticket_recipient_emails(communication_rows):
	"""Comma-joined, deduped external recipient/cc emails across a ticket's
	communications — the people a mail was *sent to* (case 2/3 "send to this mail").
	The helpdesk's own inboxes are stripped so they don't match every ticket."""
	inboxes = _support_inbox_emails()
	emails = []
	seen = set()
	for row in communication_rows or []:
		for field in ("recipients", "cc"):
			for email in _EMAIL_EXTRACT_RE.findall(cstr(row.get(field) or "").lower()):
				if email in inboxes or email in seen:
					continue
				seen.add(email)
				emails.append(email)
	return ", ".join(emails)[:RECIPIENT_EMAILS_MAX]


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
	recipient_emails = _build_ticket_recipient_emails(communication_rows)
	return primary_message_html, primary_message_text, combined, recipient_emails


def _build_ticket_message_search_field_update(ticket_name, ticket_doc=None):
	field_names = _ticket_message_search_fields()
	if not field_names:
		return {}
	(
		primary_message_html,
		primary_message_text,
		search_text,
		recipient_emails,
	) = _build_ticket_message_search_values(ticket_name, ticket_doc=ticket_doc)
	search_update = {}
	if "custom_primary_message_html" in field_names:
		search_update["custom_primary_message_html"] = primary_message_html
	if "custom_primary_message_text" in field_names:
		search_update["custom_primary_message_text"] = primary_message_text
	if "custom_search_message_body" in field_names:
		search_update["custom_search_message_body"] = search_text
	if "custom_search_recipient_emails" in field_names:
		search_update["custom_search_recipient_emails"] = recipient_emails
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


def _empty_family_terms(seed_email=None):
	seed = cstr(seed_email or "").strip().lower()
	return {
		"emails": {seed} if seed else set(),
		"student_refs": set(),
		"student_names": set(),
		"student_ids": set(),
	}


def _student_ids_for_guardian_ids(guardian_ids):
	"""Student ids linked to any of these guardians (via the Student Guardian child table)."""
	student_ids = set()
	if not guardian_ids:
		return student_ids
	for row in frappe.get_all(
		"Student Guardian",
		fields=["parent"],
		filters={"parenttype": "Student", "guardian": ["in", list(guardian_ids)]},
		page_length=0,
	):
		if row.parent:
			student_ids.add(row.parent)
	return student_ids


def _family_terms_for_student_ids(student_ids, seed_terms=None):
	"""Expand a set of student ids into the full family search-term bundle:
	every sibling-guardian email + each student's login email / reference number /
	display name / id. Shared by the guardian-email, student-email and
	student-code resolvers so they all surface the SAME family of tickets."""
	terms = seed_terms if seed_terms is not None else _empty_family_terms()
	student_ids = {cstr(s).strip() for s in (student_ids or set()) if cstr(s).strip()}
	if not student_ids:
		return terms

	# Sibling guardians: every guardian of every resolved student, so one
	# member's identifier surfaces the whole family (the other parent included).
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

	# Pull each student's identifiers: id, ref, name, login + contact emails.
	if _has_doctype("Student"):
		student_fields = ["name", "first_name", "last_name", "reference_number", "user"]
		for row in frappe.get_all(
			"Student",
			fields=student_fields,
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
			email_value = cstr(row.get("user") or "").strip().lower()
			if email_value and "@" in email_value:
				terms["emails"].add(email_value)

	return terms


def _expand_email_to_family_search_terms(email):
	"""Given a guardian OR student email, return every search term that
	identifies the whole family: every guardian's email + the students' login
	emails + reference numbers + display names + student IDs. Searching by one
	parent's (or the student's own) email then surfaces every ticket associated
	with the family, including ones raised by a sibling or indexed only by
	student ref/name."""
	email = cstr(email or "").strip().lower()
	terms = _empty_family_terms(email)
	if not email:
		return terms
	if not (_has_doctype("Guardian") and _has_doctype("Student Guardian")):
		return terms

	# 1) email belongs to a guardian -> the students of that guardian.
	guardian_ids = set()
	for row in frappe.get_all("Guardian", fields=["name"], filters={"email_address": email}, page_length=0):
		guardian_ids.add(row.name)
	for row in frappe.get_all("Guardian", fields=["name"], filters={"user": email}, page_length=0):
		guardian_ids.add(row.name)
	student_ids = _student_ids_for_guardian_ids(guardian_ids)

	# 2) email belongs to a student directly (Student.user) — a student-raised
	#    ticket. Fan out to the SAME family so a student email surfaces siblings +
	#    guardians, symmetric with the guardian path.
	if _has_doctype("Student") and frappe.db.has_column("Student", "user"):
		for row in frappe.get_all("Student", fields=["name"], filters={"user": email}, page_length=0):
			if row.name:
				student_ids.add(row.name)

	return _family_terms_for_student_ids(student_ids, seed_terms=terms)


def _expand_email_to_family_emails(email):
	"""Backward-compat shim — kept so external callers still work."""
	return _expand_email_to_family_search_terms(email)["emails"]


_PHONE_MIN_DIGITS = 8


def _looks_like_phone(value):
	"""True when the query is a phone number: at least 8 digits and predominantly
	digits. The exact ticket-ID lookup runs BEFORE this in the caller, and ticket
	IDs are <=6 digits, so a real ticket ID is never misread as a phone."""
	raw = cstr(value or "").strip()
	if not raw:
		return False
	digits = re.sub(r"\D", "", raw)
	if len(digits) < _PHONE_MIN_DIGITS:
		return False
	compact = re.sub(r"\s", "", raw)
	return bool(compact) and (len(digits) / len(compact)) >= 0.7


def _expand_phone_to_family_search_terms(phone):
	"""Resolve a guardian phone number to the whole family's search terms (same
	shape as _expand_email_to_family_search_terms). Matches Guardian.mobile_number /
	alternate_number on the last 10 digits (resilient to +91 / spacing), takes that
	guardian's email, and reuses the email family-walk — so a phone search surfaces
	every sibling's tickets. Returns empty terms (caller falls through to normal
	search) when no guardian matches or the matched guardian has no email."""
	empty = {"emails": set(), "student_refs": set(), "student_names": set(), "student_ids": set()}
	digits = re.sub(r"\D", "", cstr(phone or ""))
	if len(digits) < _PHONE_MIN_DIGITS or not _has_doctype("Guardian"):
		return empty
	tail = digits[-10:]
	seed_email = None
	for field in ("mobile_number", "alternate_number"):
		if not frappe.db.has_column("Guardian", field):
			continue
		for row in frappe.get_all(
			"Guardian",
			fields=["email_address", "user"],
			filters=[[field, "like", f"%{tail}"]],
			page_length=100,
		):
			seed_email = cstr(row.email_address or row.user or "").strip().lower()
			if seed_email:
				break
		if seed_email:
			break
	if not seed_email:
		return empty
	return _expand_email_to_family_search_terms(seed_email)


# Student identity codes. Live data: Student.name = <2-letter branch><reference>
# e.g. "BFOA01"; reference_number = the trailing part e.g. "OA01". The mandatory
# trailing digits reliably separate these from ordinary words ("fees", "ledger").
# The DB lookup is the real gate — the regex is only a cheap pre-filter so plain
# words never hit the Student table.
_STUDENT_CODE_RE = re.compile(r"^[A-Za-z]{2,6}\d{1,4}$")


def _looks_like_student_code(value):
	return bool(_STUDENT_CODE_RE.match(cstr(value or "").strip()))


def _expand_student_code_to_family_search_terms(code):
	"""Resolve a student identity code — the student name (e.g. ``BFOA01``) or the
	reference number (e.g. ``OA01``) — to the whole family's search terms, reusing
	the same student->family walk as the email path. Returns empty terms (caller
	falls through) when nothing resolves."""
	code = cstr(code or "").strip()
	if not code or not _has_doctype("Student"):
		return _empty_family_terms()
	student_ids = set()
	# Exact student name (the PK) — instant; collation makes it case-insensitive.
	if frappe.db.exists("Student", code):
		student_ids.add(code)
	# Reference number (e.g. "OA01").
	if frappe.db.has_column("Student", "reference_number"):
		for row in frappe.get_all(
			"Student", fields=["name"], filters={"reference_number": code}, page_length=0
		):
			if row.name:
				student_ids.add(row.name)
	if not student_ids:
		return _empty_family_terms()
	return _family_terms_for_student_ids(student_ids)


def _family_is_expanded(terms, seed_emails=0):
	"""True when family expansion found identifiers beyond the seed query."""
	return bool(
		terms.get("student_refs")
		or terms.get("student_names")
		or terms.get("student_ids")
		or len(terms.get("emails", set())) > seed_emails
	)


# Wall-time budget (seconds) for a single candidate-resolution query. The family
# OR-search and the LIKE fallback can full-scan on a cold buffer pool; this caps
# them so a worker can never be pinned. A query that exceeds it raises 1969 and
# the caller degrades to "no candidates from this probe" (falls through).
CANDIDATE_STATEMENT_TIMEOUT_SEC = 4


@contextlib.contextmanager
def _statement_timeout(seconds):
	"""Bound queries in this block via MariaDB session max_statement_time, then
	restore unlimited. Best-effort — a backend without support runs unguarded."""
	applied = False
	try:
		try:
			frappe.db.set_execution_timeout(int(seconds))
			applied = True
		except Exception:
			applied = False
		yield
	finally:
		if applied:
			try:
				frappe.db.set_execution_timeout(0)
			except Exception:
				pass


def _is_statement_timeout(exc):
	msg = cstr(exc).lower()
	return "1969" in msg or "max_statement_time" in msg or "max_execution_time" in msg


def _guarded_get_list(doctype, **kwargs):
	"""frappe.get_list bounded by CANDIDATE_STATEMENT_TIMEOUT_SEC. Returns [] (not
	an exception) when the query exceeds the budget, so a pathological candidate
	scan degrades to 'no candidates from this probe' instead of hanging a worker."""
	try:
		with _statement_timeout(CANDIDATE_STATEMENT_TIMEOUT_SEC):
			return frappe.get_list(doctype, **kwargs)
	except Exception as exc:
		if _is_statement_timeout(exc):
			return []
		raise


def _family_candidate_names(terms, base_filters, match_recipients=False):
	"""Resolve a family's tickets fast, safe and PRECISE.

	Primary probe (always): a single index-backed raised_by probe — equality on
	every family email (guardians + student `user` logins) plus an
	indexed prefix range on each student id (BFOA01 -> 'bfoa01@%'). All conditions
	are on the one raised_by_unity_idx column, so MariaDB index-merges instead of
	full-scanning. Statement-guarded.

	Recipient probe (``match_recipients``, for the email/phone paths — case 2/3
	"send to this mail"): tickets where a family email is a To/CC recipient, via the
	dedicated recipient FULLTEXT index (see _recipient_candidates) — index-backed,
	not a %LIKE% scan. No-op until that index + backfill land.

	Note: we deliberately do NOT match the denormalised student refs/names.
	populate_ticket_student_search_fields derives a ticket's refs from its OWN
	raised_by, so those fields never point to a student outside the raiser's family
	— matching them adds zero recall over the raised_by equality while a short ref
	like "AA01" over-matches (prefix + body tokens) and inflates results."""
	names = set()

	raised_or = []
	for email in terms.get("emails", set()):
		value = cstr(email).strip().lower()
		if value:
			raised_or.append([TICKET_DOCTYPE, "raised_by", "=", value])
	for student_id in terms.get("student_ids", set()):
		sid = cstr(student_id).strip().lower()
		if sid:
			raised_or.append([TICKET_DOCTYPE, "raised_by", "like", f"{sid}@%"])
	if raised_or:
		_append_ticket_names(
			names,
			_guarded_get_list(
				TICKET_DOCTYPE,
				fields=["name"],
				filters=base_filters,
				or_filters=raised_or,
				order_by="modified desc",
				page_length=MAX_SEARCH_CANDIDATES,
			),
		)

	if match_recipients:
		names |= _recipient_candidates(terms.get("emails", set()), base_filters)

	return names


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

	# Indexed Data fields only for the LIKE path. The 12KB custom_search_message_body
	# is deliberately NOT here: a leading-wildcard `%tok%` scan over it could not
	# short-circuit the LIMIT and full-scanned all ~93K rows whenever a token was
	# sparse/absent — the primary search timeout. FULLTEXT now owns all body/content
	# matching; LIKE only covers the short, index-friendly identity Data fields.
	search_fields = ["name", "subject", "raised_by"]
	for field in [
		"custom_search_student_names",
		"custom_search_student_refs",
		"custom_search_guardian_emails",
	]:
		if _has_field(TICKET_DOCTYPE, field):
			search_fields.append(field)

	# Email search (cases 2 & 3): a guardian OR student email. Resolve to the whole
	# family and match via the index-only family resolver (raised_by equality/prefix
	# + FULLTEXT over distinctive refs/ids). Runs even when nothing expands (its
	# raised_by equality), so an email query never leaks into the tokenised content
	# path. Falls through only when the email/family has no tickets at all.
	if _looks_like_email(query):
		terms = _expand_email_to_family_search_terms(query)
		candidate_names |= _family_candidate_names(terms, base_filters, match_recipients=True)
		if candidate_names:
			return candidate_names

	# Guardian phone family expansion (case 5): a phone that resolves to a guardian
	# surfaces every ticket of the whole family. Falls through to content search
	# when no guardian matches (preserving an incidental phone-in-body match).
	if _looks_like_phone(query):
		phone_terms = _expand_phone_to_family_search_terms(query)
		if _family_is_expanded(phone_terms):
			candidate_names |= _family_candidate_names(phone_terms, base_filters, match_recipients=True)
			if candidate_names:
				return candidate_names

	# Student-code search (case 1): the query is a student name code ("BFOA01") or
	# reference number ("OA01"). Resolve it to the student's family and surface
	# every ticket the family raised. Robust even when the denormalised
	# custom_search_* fields are stale/NULL, because the resolved student login
	# (indexed raised_by prefix) and guardian emails are matched directly. Falls
	# through to FULLTEXT/LIKE when the code doesn't resolve (a code-shaped word
	# that isn't a real student) or the resolved student simply has no tickets.
	if _looks_like_student_code(query):
		code_terms = _expand_student_code_to_family_search_terms(query)
		if _family_is_expanded(code_terms):
			candidate_names |= _family_candidate_names(code_terms, base_filters)
			if candidate_names:
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
	# Cap the token count so a pasted paragraph can't build an unbounded filter.
	# Raised 8 → 16 so longer pasted phrases keep more of their distinguishing
	# words (the AND-of-OR is a single SQL statement, so more tokens just add
	# AND clauses, not extra round-trips).
	MAX_SEARCH_TOKENS = 16
	tokens = tokens[:MAX_SEARCH_TOKENS]

	if not tokens:
		# Fall back to substring match on the raw query so users with quoted
		# punctuation/short queries that tokenize to nothing still get results.
		or_filters = [
			[TICKET_DOCTYPE, field, "like", _like_pattern(query)] for field in search_fields
		]
		_append_ticket_names(
			candidate_names,
			_guarded_get_list(
				TICKET_DOCTYPE,
				fields=["name"],
				filters=base_filters,
				or_filters=or_filters,
				order_by="modified desc",
				page_length=MAX_SEARCH_CANDIDATES,
			),
		)
		return candidate_names

	# FULLTEXT is authoritative for content: it covers body/subject/refs/names/
	# guardian-emails (all indexed). For any query with an indexable (>=3-char)
	# token, return its result DIRECTLY — even when empty. We deliberately do NOT
	# fall through to a full %LIKE% scan on no match: that just full-scans 93K rows
	# to the same empty answer (the old 4s "no results" cost) and the FT index
	# already covers every column a content search needs. BOOLEAN MODE, no relevance
	# sort, statement-guarded (see _fulltext_candidates).
	if _fulltext_index_available() and _fulltext_boolean_query(query):
		return _fulltext_candidates(query, base_filters)

	# Only reached when the query has no FULLTEXT-indexable token (e.g. "AC", "12")
	# or the index isn't on this site: body-less multi-token AND-of-OR over the
	# short identity Data fields. Single SQL + statement guard.
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
	# Statement-time guarded: the Data-field LIKEs are small but a cold buffer pool
	# can still make a 93K scan slow — degrade to empty rather than pin a worker.
	try:
		with _statement_timeout(CANDIDATE_STATEMENT_TIMEOUT_SEC):
			rows = query.run(as_dict=True)
	except Exception as exc:
		if _is_statement_timeout(exc):
			return set()
		raise
	return {cstr(row["name"]) for row in rows}


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


# Per-statement guard (seconds) for the raw FULLTEXT SELECT. MariaDB kills the
# query if it runs longer (raising 1969, which we catch + degrade). Set to 5 so a
# COLD broad single-common-word search ("fee" ~4s cold: reading a posting list
# present in 40K+ docs) still COMPLETES and returns results, rather than being
# killed to empty — while staying far under the old 30s+ hang and bounding any
# worker-hold. Warm (the production norm) these are 150ms-2.5s.
FULLTEXT_STATEMENT_TIMEOUT_SEC = 5
# InnoDB indexes tokens >= innodb_ft_min_token_size (3 on this deploy). Lowered
# from the old 4 so 3-char content words ("fee", "bus") are searchable.
_FULLTEXT_MIN_TOKEN = 3
_FULLTEXT_MAX_TOKENS = 12


def _fulltext_boolean_query(query):
	"""Build a MariaDB BOOLEAN MODE match string from a free-text query.

	- Re-tokenise on word chars only (``\\w+``) so the BOOLEAN operator
	  characters ``@ . -`` (which appear in emails/refs) can never reach the
	  parser and flip a token into an exclude/phrase. Identity lookups
	  (email / phone / student-code) own those punctuated queries anyway.
	- Drop tokens shorter than the InnoDB min token size (3) — they aren't in
	  the index and a 1-2 char stem with ``*`` explodes the match set.
	- A single token is prefix-matched (``+tok*``) so "transp" finds "transport"
	  and as-you-type stays useful.
	- A multi-word query requires the 1-3 most distinctive tokens (longest =
	  rarest heuristic) with ``+`` so it narrows to tickets that actually contain
	  its meaningful words instead of OR-matching every common word and truncating
	  an arbitrary 1000. The Python ranker does the final ordering.
	"""
	tokens = [
		t for t in re.findall(r"\w+", _normalize_search_text(query)) if len(t) >= _FULLTEXT_MIN_TOKEN
	]
	if not tokens:
		return ""
	# Dedupe (preserve first-seen order).
	unique = list(dict.fromkeys(tokens))
	# Single token (incl. as-you-type): prefix-match so "transp" finds "transport".
	if len(unique) == 1:
		return f"+{unique[0]}*"
	by_rarity = sorted(unique, key=len, reverse=True)[:_FULLTEXT_MAX_TOKENS]
	# Multi-token content search: REQUIRE the top 2-3 most distinctive tokens
	# (longest = rarest), matched EXACTLY, and drop the rest. Optional (non-`+`)
	# tokens don't restrict a BOOLEAN match set, and because we rank in Python — not
	# by FT relevance — they add only cold posting-list cost for zero gain. Exact
	# (no `*`) avoids prefix-expanding a common word, which was the ~3s cold cost.
	# The required set stays a superset of the Python ranker's all-tokens result, so
	# nothing rankable is lost; the ranker enforces the full token set on content.
	require_count = 3 if len(by_rarity) >= 4 else 2
	return " ".join(f"+{token}" for token in by_rarity[:require_count])


def _fulltext_candidates(query, base_filters):
	"""Candidate set via the MariaDB FULLTEXT index — the primary path for any
	content/subject query that has a >=3-char word. BOOLEAN MODE with NO relevance
	``ORDER BY``: the Python ranker (`_ranked_ticket_ids`) re-orders the bounded
	set anyway, and scoring the whole match set with ``ORDER BY MATCH()`` was the
	>30s timeout. Statement-time guarded so it can never hang a worker. Returns
	empty on a missing index / timeout / no usable tokens and the caller falls
	through to the body-less LIKE path — never a dead end.
	"""
	# Skip entirely if the FULLTEXT index isn't on this site — saves the
	# round-trip + 1191 exception cost on every query.
	if not _fulltext_index_available():
		return set()
	boolean_query = _fulltext_boolean_query(query)
	if not boolean_query:
		return set()

	col_list = ", ".join(f"`{c}`" for c in _FULLTEXT_COLUMNS)
	# SET STATEMENT ... FOR scopes the timeout to THIS select only (not the
	# permission re-query below). The timeout is an int literal, never user input.
	sql = (
		f"SET STATEMENT max_statement_time={int(FULLTEXT_STATEMENT_TIMEOUT_SEC)} FOR "
		f"SELECT name FROM `tabHD Ticket` "
		f"WHERE MATCH({col_list}) AGAINST (%s IN BOOLEAN MODE) "
		f"LIMIT %s"
	)
	try:
		rows = frappe.db.sql(sql, (boolean_query, MAX_SEARCH_CANDIDATES))
	except Exception as exc:
		msg = cstr(exc)
		# 1969 = query interrupted (hit the statement timeout) — the expected
		# backstop under pathological load; degrade quietly to the LIKE fallback.
		# Log anything else once (e.g. 1191 missing index on an un-migrated site).
		if "1969" not in msg and "max_statement_time" not in msg.lower():
			frappe.log_error(
				title="unity search FULLTEXT fallback failed",
				message=f"{type(exc).__name__}: {exc}",
			)
		return set()

	# cstr — HD Ticket names are integers (autoincrement naming); keep the whole
	# candidate pipeline string-typed so set-union/dedup with the other probes
	# (which go through _append_ticket_names) never splits "105" from 105.
	candidate_names = {cstr(row[0]) for row in rows if row and row[0]}
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
	return {cstr(row.name) for row in filtered}


_RECIPIENT_FT_INDEX_NAME = "recipient_ft_idx"
_RECIPIENT_MAX_EMAILS = 20


@functools.lru_cache(maxsize=1)
def _recipient_ft_index_available():
	"""Does the dedicated recipient_ft_idx FULLTEXT index exist? Cached per worker."""
	try:
		rows = frappe.db.sql(
			"""SELECT 1 FROM information_schema.STATISTICS
			   WHERE table_schema = DATABASE()
			     AND table_name = 'tabHD Ticket'
			     AND index_name = %s
			   LIMIT 1""",
			(_RECIPIENT_FT_INDEX_NAME,),
		)
		return bool(rows)
	except Exception:
		return False


def _recipient_candidates(emails, base_filters):
	"""Tickets where any of ``emails`` is a To/CC recipient — "sent to this mail"
	(cases 2 & 3). Matched via the dedicated recipient FULLTEXT index (NOT a
	leading-wildcard %LIKE% scan, which was a ~3s full scan). Each email's
	distinctive tokens (local part + domain labels >= the InnoDB min size) are a
	required ``(+a +b +c)`` group; groups are OR'd so any family member as a
	recipient is a hit. NO relevance ORDER BY (Python ranker orders), statement
	guarded, permissions re-applied. Empty when the index/field is absent
	(un-migrated / un-backfilled site) — the caller still has the raised_by probe."""
	if not (
		_has_field(TICKET_DOCTYPE, "custom_search_recipient_emails")
		and _recipient_ft_index_available()
	):
		return set()
	groups = []
	for email in emails:
		tokens = [
			t for t in re.findall(r"\w+", cstr(email).lower()) if len(t) >= _FULLTEXT_MIN_TOKEN
		]
		if tokens:
			groups.append("(" + " ".join(f"+{t}" for t in tokens) + ")")
		if len(groups) >= _RECIPIENT_MAX_EMAILS:
			break
	if not groups:
		return set()
	boolean_query = " ".join(groups)

	sql = (
		f"SET STATEMENT max_statement_time={int(FULLTEXT_STATEMENT_TIMEOUT_SEC)} FOR "
		f"SELECT name FROM `tabHD Ticket` "
		f"WHERE MATCH(`custom_search_recipient_emails`) AGAINST (%s IN BOOLEAN MODE) "
		f"LIMIT %s"
	)
	try:
		rows = frappe.db.sql(sql, (boolean_query, MAX_SEARCH_CANDIDATES))
	except Exception as exc:
		msg = cstr(exc)
		if "1969" not in msg and "max_statement_time" not in msg.lower():
			frappe.log_error(
				title="unity recipient FULLTEXT failed",
				message=f"{type(exc).__name__}: {exc}",
			)
		return set()

	candidate_names = {cstr(row[0]) for row in rows if row and row[0]}
	if not candidate_names or not base_filters:
		return candidate_names
	filtered = frappe.get_list(
		TICKET_DOCTYPE,
		fields=["name"],
		filters=_merge_filters(
			base_filters,
			[[TICKET_DOCTYPE, "name", "in", list(candidate_names)]],
		),
		page_length=len(candidate_names),
	)
	return {cstr(row.name) for row in filtered}


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
	# Candidate set: every ticket EVER assigned to the user (ANY ToDo status),
	# resolved via the indexed ToDo table — never an `_assign LIKE` scan. The
	# assignee is `allocated_to` (the `owner` is whoever DID the assigning, e.g. the
	# funnel bot), with `owner` kept as a fallback for legacy rows. Newest first so
	# the MAX cap keeps the rows a user would actually look at.
	rows = frappe.get_all(
		"ToDo",
		filters={"reference_type": TICKET_DOCTYPE},
		or_filters={"allocated_to": user, "owner": user},
		fields=["reference_name"],
		order_by="creation desc",
		page_length=MAX_ASSIGNED_LOOKUP,
	)
	candidates = []
	seen = set()
	for row in rows:
		name = row.reference_name
		if name and name not in seen:
			seen.add(name)
			candidates.append(name)

	# Keep only tickets where the user is the CURRENT assignee per HD Ticket._assign.
	# This makes a CLOSED ticket the user was last assigned still appear under them,
	# and drops a ticket that was reassigned away — both regardless of ToDo status
	# (closing/reassigning moves the ToDo out of "Open"). Chunked IN on the indexed
	# `name` PK keeps it fast.
	names = set()
	CHUNK = 2000
	for i in range(0, len(candidates), CHUNK):
		chunk = candidates[i : i + CHUNK]
		for row in frappe.get_all(
			TICKET_DOCTYPE,
			filters={"name": ["in", chunk]},
			fields=["name", "_assign"],
			page_length=len(chunk),
		):
			assignees = frappe.parse_json(row.get("_assign") or "[]") or []
			if user in assignees:
				# HD Ticket names are numeric; get_all returns them as int. Cast to
				# str so the set matches ToDo.reference_name / the original contract.
				names.add(cstr(row.get("name")))
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

	# "Created By" = the ticket's owner (creator). owner is an indexed column, so a plain
	# equality filter is cheap even over the full table.
	if filters.get("created_by"):
		res.append([TICKET_DOCTYPE, "owner", "=", filters.created_by])

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


@frappe.whitelist()
def benchmark_ticket_search(queries=None, view="all", repeat=1):
	"""Admin-only, read-only search timing harness. Runs the candidate resolver for
	each query and reports candidate count + wall-ms, so every query shape can be
	verified to resolve in milliseconds and never approach the API timeout.
	SELECT-only — safe to run on production.

	Usage:
	  bench --site <site> execute helpdesk.api.unity_helpdesk.benchmark_ticket_search \\
	    --kwargs '{"queries": ["BFOA01", "OA01", "parent@x.com", "fee receipt"]}'
	"""
	capabilities = _require_unity_access()
	if not capabilities.can_manage_unity_settings:
		frappe.throw(_("You are not allowed to run the search benchmark"), frappe.PermissionError)

	import time

	query_list = [cstr(q) for q in (_parse_json(queries, None) or []) if cstr(q).strip()]
	if not query_list:
		frappe.throw(_("Provide a non-empty 'queries' list"))
	try:
		repeat = max(1, min(int(repeat or 1), 10))
	except (TypeError, ValueError):
		repeat = 1

	base_filters = _build_filters(
		"all",
		None,
		assigned_agent=None if capabilities.can_view_all_tickets else _session_user(),
	)

	results = []
	for raw_query in query_list:
		timings = []
		candidate_count = 0
		sample = []
		error = None
		for _iteration in range(repeat):
			start = time.monotonic()
			try:
				names = _search_candidate_ticket_names(base_filters, raw_query)
			except Exception as exc:
				error = f"{type(exc).__name__}: {exc}"
				names = set()
			timings.append((time.monotonic() - start) * 1000.0)
			candidate_count = len(names)
			if not sample:
				sample = sorted(names, key=str)[:5]
		results.append(
			{
				"query": raw_query,
				"candidates": candidate_count,
				"ms_min": round(min(timings), 1),
				"ms_max": round(max(timings), 1),
				"ms_avg": round(sum(timings) / len(timings), 1),
				"sample": sample,
				"error": error,
			}
		)
	return {"results": results, "repeat": repeat, "view": view}


def _resolve_ticket_context(view, filters, search, message_body, page_length, start):
	"""Compute the shared context that get_tickets_page and get_tickets_summary
	both need: capabilities, effective view, base filter list, cleaned search
	text, and (when searching) the candidate-name set + family expansion. The
	result is cached on `frappe.local` so the two parallel endpoints don't
	repeat the work twice within a single request — only relevant for the
	back-compat `get_tickets` wrapper, but harmless otherwise.
	"""
	# Clamp pagination defensively: a missing/zero value falls back to 20, and a
	# huge value (a stale 500 preference, or a crafted request) is capped so the
	# list query can never be asked for an unbounded number of rows and time out.
	page_length = max(1, min(int(page_length or 20), MAX_TICKET_PAGE_LENGTH))
	start = max(0, int(start or 0))

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
		cached["page_length"] = page_length
		cached["start"] = start
		return cached

	capabilities = _require_unity_access()
	search = cstr(search or message_body or "").strip()
	effective_view = "all" if view == "all" and capabilities.can_view_all_tickets else "my"
	# Always scope the base filters by the effective view — including during search.
	# The My-Tickets view must stay assigned-to-me whether or not a query is present;
	# widening to "all" while searching leaked every matching ticket into My Tickets
	# for admins (who have no assigned_agent fallback). Non-admins were already scoped
	# via assigned_agent, but admins on the My tab were not — hence the leak.
	list_filters = _build_filters(
		effective_view,
		filters,
		assigned_agent=_session_user() if not capabilities.can_view_all_tickets else None,
	)
	fields = _ticket_fields(extra=_selected_column_fields())
	search_candidate_names = None
	search_family_terms = None
	if search:
		search_candidate_names = _search_candidate_ticket_names(list_filters, search)
		# When the search is a guardian email OR a guardian phone, we expanded to
		# the whole family. Pass the expansion to the ranker so it accepts family
		# matches as Tier-2 hits even when the literal email/phone isn't on the
		# ticket (e.g. a sibling's ticket that only carries the shared guardian
		# email). Without this the ranker would drop family members that don't
		# literally contain the query string.
		if _looks_like_email(search):
			expanded = _expand_email_to_family_search_terms(search)
			if (
				len(expanded.get("emails") or set()) > 1
				or expanded.get("student_refs")
				or expanded.get("student_names")
				or expanded.get("student_ids")
			):
				search_family_terms = expanded
		elif _looks_like_phone(search):
			expanded = _expand_phone_to_family_search_terms(search)
			if _family_is_expanded(expanded):
				search_family_terms = expanded
		elif _looks_like_student_code(search):
			# Student name code / reference ("SHOB76", "OB76"): same as the email/phone
			# paths — pass the family expansion to the ranker so EVERY family ticket
			# is a Tier-2 hit (whether raised by the student login or a guardian email),
			# instead of scattering them across relevance tiers. With one tier, the
			# tiebreaker (modified desc) takes over → newest ticket first.
			expanded = _expand_student_code_to_family_search_terms(search)
			if _family_is_expanded(expanded):
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
		# `candidate_rows` already holds every candidate with the same `fields`,
		# and `paginated_ids` is a slice of names ranked *from* candidate_rows —
		# so the page is already in memory. Slice it directly instead of paying a
		# second DB round-trip (and an extra `IN (...)` scan) for rows we just
		# fetched. row_map is keyed by name; we re-order it to the ranked slice.
		row_map = {row.name: row for row in candidate_rows}
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
def search_users(query=None):
	"""Typeahead for the New-Ticket "Customer Email" field.

	Access-gated to Unity users and bounded to 10 rows. Returns enabled users
	(customers/website users AND agents) matching name/email/full_name, with
	prefix matches ranked first. Wildcards in the query are stripped so a bare
	'%' can't be used to dump the table. Replaces the broad
	frappe.client.get_list call the SPA used before.
	"""
	_require_unity_access()
	query = cstr(query or "").strip()
	if len(query) < 2:
		return []
	safe = query.replace("%", "").replace("_", "")
	if not safe:
		return []
	return frappe.db.sql(
		"""
		SELECT name, full_name, email
		FROM `tabUser`
		WHERE enabled = 1
		  AND name NOT IN ('Administrator', 'Guest')
		  AND (name LIKE %(like)s OR email LIKE %(like)s OR full_name LIKE %(like)s)
		ORDER BY
		  CASE WHEN name LIKE %(prefix)s OR email LIKE %(prefix)s THEN 0 ELSE 1 END,
		  full_name
		LIMIT 10
		""",
		{"like": f"%{safe}%", "prefix": f"{safe}%"},
		as_dict=True,
	)


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
		# A non-empty hold reason implies the ticket is On Hold — unless the caller
		# explicitly cleared the flag (is_on_hold == 0) in the same request. Keeps the
		# reason and the "Issues On Hold" indicator consistent (e.g. a reason typed
		# from the list must reflect as On Hold, not silently do nothing).
		if (
			cstr(hold_reason).strip()
			and (is_on_hold is None or int(bool(int(is_on_hold))))
			and _has_field(TICKET_DOCTYPE, "custom_is_on_hold")
		):
			ticket.custom_is_on_hold = 1
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


def _status_field_updates(value, current_status):
	"""The core field writes for a status change — shared by the inline
	`update_ticket` save path (helpdesk.api.unity_helpdesk_ext) and the bulk
	fast-path below, so the two always agree.

	"On Hold" is a VIRTUAL status: the real `status` stays Open and the
	`custom_is_on_hold` flag carries it. Returns a {field: value} dict suitable for
	either `doc.set(...)` or `frappe.db.set_value(dt, dn, dict)`."""
	updates = {}
	if value == "On Hold":
		if _has_field(TICKET_DOCTYPE, "custom_is_on_hold"):
			updates["custom_is_on_hold"] = 1
		if current_status in FINAL_STATUSES or not current_status:
			updates["status"] = "Open"
	else:
		updates["status"] = value
		if _has_field(TICKET_DOCTYPE, "custom_is_on_hold"):
			updates["custom_is_on_hold"] = 0
	return updates


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
				# Lighter than assign_ticket_to_agent: manage the ToDo directly with
				# notifications OFF (a bulk reassign must not fire N notifications) and
				# skip loading the full HD Ticket doc. Keeps ToDo/`_assign` correct.
				clear_all_assignments(TICKET_DOCTYPE, name)
				if clean_value:
					assign_to_add(
						{
							"doctype": TICKET_DOCTYPE,
							"name": name,
							"assign_to": [clean_value],
							"notify": 0,
						}
					)
			elif field_name == "status":
				# Fast path: write the same status fields the inline update_ticket
				# save would, but via a raw db.set_value — skipping the full doc
				# lifecycle (SLA recompute / Activity / Version / search-index /
				# realtime). The accepted bulk trade-off. Fetch only the current
				# status for the On-Hold decision.
				current_status = frappe.db.get_value(TICKET_DOCTYPE, name, "status")
				updates = _status_field_updates(clean_value, current_status)
				# The controller would normally stamp a resolution date on
				# Resolved/Closed; we skip it, so set it here when missing.
				if clean_value in FINAL_STATUSES and _has_field(TICKET_DOCTYPE, "resolution_date"):
					if not frappe.db.get_value(TICKET_DOCTYPE, name, "resolution_date"):
						updates["resolution_date"] = frappe.utils.now_datetime()
				frappe.db.set_value(TICKET_DOCTYPE, name, updates)
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


def _default_bulk_recipients():
	"""Addresses BCC'd on every bulk email, from HD Settings (blank = disabled).

	Returns a de-duplicated, validated, lowercased list of strings. Surfaced to
	the SPA via get_profile() and consumed by the bulk-email background job.
	"""
	# Guard against the field not yet existing on this site (it ships as an
	# hd_settings docfield that lands on `bench migrate`). A missing OPTIONAL
	# setting must never raise — get_profile() also carries available_columns /
	# column_preferences, so a throw here blanks the entire ticket list.
	if not frappe.get_meta("HD Settings").has_field("unity_bulk_email_default_recipients"):
		return []
	raw = frappe.db.get_single_value("HD Settings", "unity_bulk_email_default_recipients") or ""
	out = []
	seen = set()
	for part in cstr(raw).replace(";", ",").replace("\n", ",").split(","):
		email = part.strip().lower()
		if not email or email in seen:
			continue
		if not frappe.utils.validate_email_address(email, throw=False):
			continue
		seen.add(email)
		out.append(email)
	return out


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
			"bulk_email_default_recipients": _default_bulk_recipients(),
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

	settings = frappe.get_doc("HD Settings")
	settings.unity_email_thread_layout = layout
	settings.save(ignore_permissions=True)
	return {"unity_email_thread_layout": settings.unity_email_thread_layout}


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

	def _add(email, name, student=None, reference=None):
		e = cstr(email or "").strip().lower()
		if e and _email_re.match(e) and e not in seen:
			seen.add(e)
			row = {"email": e, "name": name or e}
			# Student hits carry their identity so the composer can add the recipient by
			# STUDENT ID (not by email). A guardian email shared by two siblings resolves
			# ambiguously by email — keying the picked token on the student id keeps each
			# sibling distinct (the "clicking a name removes a selected student" bug).
			if student:
				row["student"] = student
			if reference:
				row["reference"] = reference
			results.append(row)

	# Search Student by id / reference number / name / email — return the student's
	# OWN email (the `user` login email) so the BCC picker resolves students by
	# reference number or name, not only guardians/contacts. Listed first so a
	# code/ref search surfaces the student.
	if frappe.db.exists("DocType", "Student"):
		try:
			student_fields = ["name", "first_name", "last_name", "user"]
			optional = [f for f in ("student_name", "reference_number") if frappe.db.has_column("Student", f)]
			student_or = [["name", "like", f"%{q}%"]]
			for f in ("reference_number", "student_name", "first_name", "last_name", "user"):
				if frappe.db.has_column("Student", f):
					student_or.append([f, "like", f"%{q}%"])
			for s in frappe.get_all(
				"Student",
				or_filters=student_or,
				fields=student_fields + optional,
				limit_page_length=10,
			):
				display = " ".join(
					p for p in (cstr(s.get("first_name")), cstr(s.get("last_name"))) if p.strip()
				).strip() or cstr(s.get("student_name") or "").strip() or cstr(s.get("name"))
				_add(
					s.get("user"),
					display,
					student=cstr(s.get("name") or "").strip() or None,
					reference=cstr(s.get("reference_number") or "").strip() or None,
				)
		except Exception:
			pass

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
	guardian emails when a recipient matches a Student's `user` email.

	Response shape (changed from a bare {email: [guardians]} dict):

	    {
	      "mapping": {student_email: [guardian_email, ...], ...},
	      "diagnostic": {
	        "input_count":            <int>,
	        "students_matched":       <int>,  # rows where the student `user` email matched
	        "students_with_guardians": <int>, # students that yielded guardian emails
	        "unmatched_emails":       [...],  # input emails with no Student record
	      },
	    }

	The diagnostic block lets the SPA surface a non-blocking warning when
	the auto-fill silently produces nothing (e.g. wrong/blank `user`
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

	# Match each input email against the Student's `user` email — the authoritative
	# student address. student_email_id is deliberately NOT used (often blank/stale).
	students = frappe.get_all(
		"Student",
		fields=["name", "user"],
		filters={"user": ["in", normalized]},
		page_length=len(normalized) + 1,
	)
	if not students:
		return _empty()

	email_by_student_id = {}
	for s in students:
		user_id = cstr(s.user or "").strip().lower()
		if user_id:
			email_by_student_id[s.name] = user_id
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
def resolve_bulk_email_students(refs):
	"""Resolve reference numbers / student ids / emails to per-student bulk-email
	groups for the composer's "reference number" mode.

	For each input matching a Student (by name, reference_number or user)
	returns the student's deliverable email, that student's guardian
	emails, and a merge ``data`` dict (common Student fields). The composer builds
	one ticket + one email per student (student + guardians when the
	Include-guardians toggle is on). Unresolvable inputs come back in ``unmatched``;
	matched students with no email in ``no_email``.
	"""
	capabilities = _require_unity_access()
	if not capabilities.get("can_view_all_tickets"):
		frappe.throw(_("You are not allowed to send bulk emails"), frappe.PermissionError)

	empty = {"students": [], "merge_fields": [], "unmatched": [], "no_email": []}
	if not frappe.db.exists("DocType", "Student"):
		return empty

	raw = _parse_json(refs, []) or []
	if isinstance(raw, str):
		raw = [raw]
	if not isinstance(raw, (list, tuple)):
		return empty

	tokens = []
	seen_tokens = set()
	for value in raw:
		token = cstr(value or "").strip()
		if token and token.lower() not in seen_tokens:
			seen_tokens.add(token.lower())
			tokens.append(token)
	if not tokens:
		return empty

	lowered = [t.lower() for t in tokens]
	meta = frappe.get_meta("Student")
	has_ref = meta.has_field("reference_number")
	has_user = meta.has_field("user")

	fetch_fields = ["name", "student_name", "first_name", "middle_name", "last_name"]
	for field in ("user", "reference_number", "school"):
		if meta.has_field(field):
			fetch_fields.append(field)
	fetch_fields = list(dict.fromkeys(fetch_fields))

	or_filters = {"name": ["in", tokens]}
	if has_ref:
		or_filters["reference_number"] = ["in", tokens]
	if has_user:
		or_filters["user"] = ["in", lowered]

	try:
		rows = frappe.get_all(
			"Student",
			fields=fetch_fields,
			or_filters=or_filters,
			page_length=len(tokens) * 4 + 10,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "unity_helpdesk.resolve_bulk_email_students")
		return empty

	by_name, by_ref, by_email = {}, {}, {}
	for row in rows:
		by_name[cstr(row.get("name")).strip().lower()] = row
		ref = cstr(row.get("reference_number") or "").strip().lower()
		if ref:
			by_ref.setdefault(ref, row)
		value = cstr(row.get("user") or "").strip().lower()
		if value:
			by_email.setdefault(value, row)

	resolved = {}
	order = []
	unmatched = []
	for token in tokens:
		key = token.lower()
		row = by_name.get(key) or by_ref.get(key) or by_email.get(key)
		if not row:
			unmatched.append(token)
			continue
		sid = cstr(row.get("name")).strip()
		if sid and sid not in resolved:
			resolved[sid] = row
			order.append(sid)

	guardians_by_student = _guardian_emails_for_student_ids(list(resolved.keys()))

	students = []
	no_email = []
	for sid in order:
		row = resolved[sid]
		email = cstr(row.get("user") or "").strip().lower()
		data = {
			"first_name": cstr(row.get("first_name") or ""),
			"middle_name": cstr(row.get("middle_name") or ""),
			"last_name": cstr(row.get("last_name") or ""),
			"student_name": cstr(row.get("student_name") or ""),
		}
		if has_ref:
			data["reference_number"] = cstr(row.get("reference_number") or "")
		if meta.has_field("school"):
			data["school"] = cstr(row.get("school") or "")
		students.append(
			{
				"student": sid,
				"student_name": _student_display_name(row) or sid,
				"email": email,
				"has_email": bool(email),
				"guardian_emails": guardians_by_student.get(sid, []),
				"data": data,
			}
		)
		if not email:
			no_email.append(sid)

	common = [
		field
		for field in ("first_name", "last_name", "middle_name", "student_name", "school")
		if meta.has_field(field)
	]
	if has_ref:
		common.append("reference_number")
	return {
		"students": students,
		"merge_fields": common,
		"unmatched": unmatched,
		"no_email": no_email,
	}


@frappe.whitelist()
def get_student_merge_fields():
	"""All Student doctype fields usable as {{merge}} placeholders in the bulk /
	single email composer. Returns [{fieldname, label}] for value-bearing fields
	(layout fieldtypes excluded). [] when the Student doctype isn't installed.

	The send path already fills ANY field by its exact fieldname (it fetches
	`fields=["*"]`), so this just lets the composer SHOW the full, accurate list and
	flag template tokens that won't resolve.
	"""
	capabilities = _require_unity_access()
	if not capabilities.get("can_view_all_tickets"):
		frappe.throw(_("You are not allowed to send bulk emails"), frappe.PermissionError)
	if not frappe.db.exists("DocType", "Student"):
		return []
	skip_types = {
		"Section Break",
		"Column Break",
		"Tab Break",
		"HTML",
		"Table",
		"Table MultiSelect",
		"Button",
		"Image",
		"Fold",
		"Heading",
		"Geolocation",
	}
	out = []
	seen = set()
	for df in frappe.get_meta("Student").fields:
		fieldname = cstr(df.fieldname or "").strip()
		if not fieldname or df.fieldtype in skip_types or fieldname in seen:
			continue
		seen.add(fieldname)
		out.append(
			{"fieldname": fieldname, "label": cstr(df.label or fieldname).strip() or fieldname}
		)
	return out


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
