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
	res = frappe.get_value(doctype="HD Settings", fieldname=fields, as_dict=True) or frappe._dict()
	res.helpdesk_ui = res.helpdesk_ui or "Default Helpdesk"
	return res
