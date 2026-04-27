import frappe

from helpdesk.setup.install import UNITY_HELPDESK_ROLES, ensure_unity_roles


def execute():
	ensure_unity_roles()

	for agent_name in frappe.get_all("HD Agent", pluck="name"):
		_assign_role_if_missing(agent_name, "Agent")
		_assign_role_if_missing(agent_name, "Helpdesk User")


def _assign_role_if_missing(user, role):
	if role not in UNITY_HELPDESK_ROLES and role != "Agent":
		return
	if not frappe.db.exists("User", user):
		return
	if frappe.db.exists("Has Role", {"parent": user, "role": role}):
		return

	user_doc = frappe.get_doc("User", user)
	user_doc.append("roles", {"role": role})
	user_doc.save(ignore_permissions=True)
