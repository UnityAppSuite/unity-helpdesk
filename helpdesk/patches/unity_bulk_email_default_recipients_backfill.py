"""Backfill the configurable bulk-email default/audit recipient on existing sites.

The previously hardcoded audit address (``feedback@walnutedu.in``) was removed in
favour of the HD Settings field ``unity_bulk_email_default_recipients`` (blank by
default). To keep the open-source code free of any site-specific address while
preserving behaviour on deployments that relied on the old hardcoded copy, this
patch copies the address from the site-config key ``bulk_email_default_recipients``
into the setting — if and only if that key is present and the setting hasn't already
been configured in the UI.

Fresh installs skip this patch (patches listed in ``patches.txt`` are marked
applied-without-running on a new site), so they stay blank. Existing sites run it
once on ``bench migrate``. Operators opt in with, e.g.::

    bench --site <site> set-config bulk_email_default_recipients feedback@example.com

Idempotent and safe to re-run.
"""

import frappe


def execute():
	value = frappe.conf.get("bulk_email_default_recipients")
	if not value:
		return

	if isinstance(value, (list, tuple)):
		value = ", ".join(str(v).strip() for v in value if str(v).strip())
	value = str(value).strip()
	if not value:
		return

	# Respect an address an admin has already entered in HD Settings.
	current = frappe.db.get_single_value("HD Settings", "unity_bulk_email_default_recipients")
	if current and str(current).strip():
		return

	frappe.db.set_single_value("HD Settings", "unity_bulk_email_default_recipients", value)
