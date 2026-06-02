import frappe

from helpdesk.patches._unity_patch import run_patch
from helpdesk.setup.install import UNITY_HELPDESK_ROLES, ensure_unity_roles


def execute():
	run_patch("unity_helpdesk_roles", _run)


def _run():
	ensure_unity_roles()

	for agent_name in frappe.get_all("HD Agent", pluck="name"):
		_assign_role_if_missing(agent_name, "Agent")
		_assign_role_if_missing(agent_name, "Helpdesk User")


def _assign_role_if_missing(user, role):
	allowed_roles = set(UNITY_HELPDESK_ROLES) | {"Agent"}
	if role not in allowed_roles:
		return
	if not frappe.db.exists("User", user):
		return
	if frappe.db.exists("Has Role", {"parent": user, "role": role}):
		return

	user_doc = frappe.get_doc("User", user)
	user_doc.append("roles", {"role": role})
	user_doc.save(ignore_permissions=True)
