# Copyright (c) 2026, Frappe Technologies and Contributors
# For license information, please see license.txt
"""Keep HD Ticket.agent_group in step with who the ticket is assigned to.

The workflow this serves: every ticket lands in the triage queue (agent_group
defaults to "Calling Team" — an edu_quality Property Setter on the DocField, not
code in this repo), someone reads it there and assigns it on to a specific
person, and from that moment the ticket should show the team that person
actually works in.

WHY THIS HANGS OFF ToDo AND NOT HD Ticket
-----------------------------------------
Assignment never fires an HD Ticket event. `ToDo` is the authoritative record;
`HD Ticket._assign` is a denormalised cache that
frappe/desk/doctype/todo/todo.py::ToDo.update_in_reference() rewrites with
`frappe.db.set_value(..., update_modified=False)` — a raw UPDATE that loads no
document and runs no hooks. So there is nothing to listen to on the ticket.

There are five things that assign a ticket:
  * helpdesk/api/unity_helpdesk_ext.py::update_ticket   (row dropdown + detail)
  * helpdesk/api/unity_helpdesk_ext.py::create_ticket   (composer, with assignee)
  * helpdesk/api/unity_helpdesk.py::bulk_update_tickets (bulk bar — talks to
    frappe.desk.form.assign_to directly and never loads the ticket doc)
  * frappe Assignment Rules (round-robin, fires on every ticket save)
  * the Desk UI's run_doc_method -> HDTicket.assign_agent
A ToDo doc_event is the only point downstream of all five.

WHY THE WRITE IS db.set_value AND NOT doc.save()
------------------------------------------------
Saving the ticket to change agent_group would trigger two things that damage
data:
  * hd_ticket.py::remove_assignment_if_not_in_team (called from on_update)
    CLEARS the assignee when agent_group changes to a team they aren't in —
    i.e. it would undo the very assignment that triggered us. This site already
    carries a "Restore HD Ticket Assignments" recovery script, so it has bitten
    before.
  * frappe's global "*" on_update hook re-runs assignment_rule.apply, which can
    round-robin the ticket away from the agent just assigned.
A raw set_value runs neither, and — since writing agent_group creates no ToDo —
cannot recurse.
"""

import frappe
from frappe.utils import cstr

# Declared locally, and `unity_helpdesk` is imported INSIDE the function below,
# on purpose: doc_events["ToDo"] fires for every ToDo on the site — Sales Orders,
# Leave Applications, everything — and frappe resolves the handler before our
# reference_type guard can run. A module-level import would drag all ~5,700
# lines of unity_helpdesk (plus helpdesk.api.ticket, hd_ticket, helpdesk.search…)
# into the first ToDo save of every worker process, and an ImportError anywhere
# in that graph would break ToDo saves site-wide.
TICKET_DOCTYPE = "HD Ticket"

# ToDo.status values that mean "this assignment is over". update_in_reference()
# uses the same two when recomputing _assign.
_INACTIVE_TODO_STATUSES = ("Cancelled", "Closed")


def sync_agent_group_for_ticket(ticket_name, user):
	"""Point a ticket's agent_group at `user`'s team.

	Returns the team it set, "" when it cleared the group, or None when it wrote
	nothing.

	The rules, in order. Rules 2 and 3 are shaped around not *losing* a group
	that someone set on purpose; rule 1 deliberately does clear one, because the
	group it would otherwise leave behind is a default nobody chose:

	1. User is in no team -> CLEAR the group. Not a corner case: 45 of the 61
	   agents on this site have no HD Team membership, so this is the common
	   path.

	   This rule used to be a no-op, on the reasoning that blanking would strip
	   "Calling Team" off tickets as they get worked. That turned out to be the
	   wrong trade: every ticket is BORN "Calling Team" (edu_quality sets a
	   Property Setter default on HD Ticket.agent_group), so leaving it alone
	   meant a ticket owned by a teamless agent still advertised itself as the
	   Calling Team's, on 319 open tickets at the time of the change. A group
	   nobody owns is worse than no group: the SPA renders the blank as
	   "Unspecified", which is the truth.

	   Note the interaction with team restrictions, currently OFF. If
	   `restrict_tickets_by_agent_group` is ever switched on while
	   `do_not_restrict_tickets_without_an_agent_group` is 0, cleared tickets
	   become invisible to non-super-admins. Turn the second setting on at the
	   same time as the first.
	2. The ticket's current group is already one of the user's teams -> leave it
	   alone, so a deliberate manual choice survives reassignment within a team.
	3. Otherwise stamp the user's first team. `_user_teams` is ORDER BY t.name,
	   which is what makes "first" stable for a multi-team agent.
	"""
	if not ticket_name or not user:
		return None

	# Deferred import — see the note next to TICKET_DOCTYPE.
	from helpdesk.api.unity_helpdesk import _user_teams

	teams = [
		cstr(row.get("team")).strip()
		for row in (_user_teams(user) or [])
		if cstr(row.get("team") or "").strip()
	]
	current = cstr(frappe.db.get_value(TICKET_DOCTYPE, ticket_name, "agent_group") or "").strip()

	if not teams:  # rule 1
		if not current:
			return None  # already Unspecified, nothing to write
		# NULL, not "". Every one of the 9.8k historically blank tickets here is
		# NULL, and mixing the two representations splits the data for no gain.
		# The "Unspecified" filter matches both either way (`is not set` emits
		# `IS NULL OR = ''`), but a single representation keeps the index
		# selective and the rows comparable.
		frappe.db.set_value(
			TICKET_DOCTYPE,
			ticket_name,
			"agent_group",
			None,
			update_modified=False,
		)
		# "" is the CALLER-facing signal for "cleared", distinct from None which
		# this function uses for "wrote nothing". It is not what was stored.
		return ""

	if current in teams:
		return None  # rule 2

	frappe.db.set_value(
		TICKET_DOCTYPE,
		ticket_name,
		"agent_group",
		teams[0],
		update_modified=False,
	)
	return teams[0]


def on_todo_change(doc, method=None):
	"""doc_event on ToDo `on_update` — see the module docstring.

	Reassignment arrives as SEVERAL events, and the ordering is what makes this
	correct. `assign_to.clear()` re-saves *every* ToDo the ticket has ever had —
	it filters by reference only, not by status — so a typical reassign fires
	this handler 3-4 times (the median ticket here carries 2-3 historical ToDos,
	of which ~2% are Open). All but one hit the status guard below before running
	a single query, and `sync_agent_group_for_ticket` is idempotent, so the
	repeats cost one primary-key lookup each.

	The last event to do any work is always the freshly inserted Open ToDo, which
	carries the NEW assignee in `allocated_to`. Un-assignment reaches us only as
	Cancelled/Closed and is skipped, so un-assignment never touches the group.
	(Assigning TO a teamless agent does clear it — that is rule 1, and it is
	driven by the new assignee, not by the un-assignment.)

	Nothing in here may raise. This runs inside the caller's transaction, so an
	exception would fail the assignment itself; a ticket showing a stale team is
	a far smaller problem than an assignment that silently didn't happen.
	"""
	try:
		# A patch or fresh install touching ToDos must not silently retag tickets.
		if frappe.flags.in_install or frappe.flags.in_patch or frappe.flags.in_migrate:
			return
		if cstr(doc.reference_type) != TICKET_DOCTYPE or not doc.reference_name:
			return
		if cstr(doc.status) in _INACTIVE_TODO_STATUSES:
			return
		sync_agent_group_for_ticket(doc.reference_name, doc.allocated_to)
	except Exception:
		# defer_insert: if the original failure was a deadlock/lock-timeout the
		# connection is already aborted, and an immediate Error Log insert would
		# raise straight back out of this except — failing the assignment, i.e.
		# exactly what this block exists to prevent.
		try:
			frappe.log_error(
				frappe.get_traceback(),
				"Unity Helpdesk: agent_group sync from ToDo",
				defer_insert=True,
			)
		except Exception:
			pass
