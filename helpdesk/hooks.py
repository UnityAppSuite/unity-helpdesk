app_name = "helpdesk"
app_title = "Helpdesk"
app_publisher = "Frappe Technologies"
app_description = "Customer Service Software"
app_icon = "octicon octicon-file-directory"
app_color = "grey"
app_email = "hello@frappe.io"
app_license = "AGPLv3"

before_install = "helpdesk.setup.install.before_install"
after_install = "helpdesk.setup.install.after_install"
after_migrate = "helpdesk.search.build_index_in_background"

scheduler_events = {
	"all": ["helpdesk.search.build_index_if_not_exists"],
	"daily": ["helpdesk.api.unity_helpdesk.send_open_ticket_reminders"],
}


website_route_rules = [
	{
		"from_route": "/helpdesk",
		"to_route": "helpdesk",
	},
	{
		"from_route": "/helpdesk/<path:app_path>",
		"to_route": "helpdesk",
	},
	{
		"from_route": "/unity-helpdesk",
		"to_route": "unity_helpdesk",
	},
	{
		"from_route": "/unity-helpdesk/<path:app_path>",
		"to_route": "unity_helpdesk",
	},
]

doc_events = {
	"Contact": {
		"before_insert": "helpdesk.helpdesk.hooks.contact.before_insert",
	},
	"Assignment Rule": {
		"on_trash": "helpdesk.overrides.on_assignment_rule_trash",
	},
	# One agent, one team. On `validate` rather than inside the Unity endpoints
	# so the desk HD Team form and data imports are covered too, and so it runs
	# before any row is written (HDTeam.after_insert creates an Assignment Rule
	# plus seven day rows, which a later failure would strand).
	"HD Team": {
		"validate": "helpdesk.api.unity_helpdesk.validate_single_team_membership",
	},
	# Keep the unity search index resilient to whatever override another app installs
	# on HD Ticket / Communication / HD Ticket Comment. doc_events run alongside class
	# methods, so even if an override skips super(), search stays fresh.
	"HD Ticket": {
		"after_insert": "helpdesk.helpdesk.hooks.search_index.on_ticket_after_insert",
	},
	"Communication": {
		"after_insert": [
			"helpdesk.helpdesk.hooks.search_index.on_communication_after_insert",
			"helpdesk.helpdesk.hooks.reply_link.on_communication_after_insert",
		],
		"on_update": "helpdesk.helpdesk.hooks.search_index.on_communication_on_update",
	},
	"HD Ticket Comment": {
		"after_insert": "helpdesk.helpdesk.hooks.search_index.on_comment_after_insert",
		"on_update": "helpdesk.helpdesk.hooks.search_index.on_comment_on_update",
	},
	# Assignment fires NO event on HD Ticket — `_assign` is a denormalised cache
	# that ToDo.update_in_reference() rewrites with a raw db.set_value. ToDo is
	# the only hook point downstream of all five assignment writers (the two
	# unity_helpdesk_ext endpoints, the bulk bar, Assignment Rules, and the Desk
	# UI), which is why agent_group is synced from here. See
	# helpdesk/api/unity_agent_group.py.
	# `on_update` ONLY — do not add after_insert. Document.run_post_save_methods()
	# runs on_update for inserts too, so after_insert would double-fire; worse, it
	# runs BEFORE ToDo.update_in_reference() takes its `tabToDo … FOR UPDATE`,
	# giving us the reverse lock order (HD Ticket then ToDo) against every
	# concurrent assignment.
	"ToDo": {
		"on_update": "helpdesk.api.unity_agent_group.on_todo_change",
	},
}

has_permission = {
	"HD Ticket": "helpdesk.helpdesk.doctype.hd_ticket.hd_ticket.has_permission",
}

permission_query_conditions = {
	"HD Ticket": "helpdesk.helpdesk.doctype.hd_ticket.hd_ticket.permission_query",
}

ignore_links_on_delete = [
	"HD Notification",
]
