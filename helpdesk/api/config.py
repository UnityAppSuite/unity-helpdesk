import frappe


@frappe.whitelist(allow_guest=True)
def get_config():
	fields = [
		"brand_logo",
		"helpdesk_ui",
		"prefer_knowledge_base",
		"setup_complete",
		"skip_email_workflow",
	]
	settings = frappe.get_single("HD Settings")
	res = frappe._dict({field: settings.get(field) for field in fields})
	res.helpdesk_ui = res.helpdesk_ui or "Default Helpdesk"
	return res
