# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt
"""Unity Bulk Email Batch — a durable record of one bulk-email submission.

Created by ``helpdesk.api.unity_helpdesk_ext.bulk_send_email`` and updated live by the
background job ``_bulk_send_email_job`` as it processes each student. It powers:

* the SPA progress bar (polled via ``get_bulk_email_batch_status``),
* the honest "X sent / K failed" result + the exportable failed-recipient list,
* the fingerprint-based duplicate-submission guard, and
* per-student idempotency (``processed_keys``) so a re-run never duplicates or drops.
"""

import frappe
from frappe.model.document import Document


class UnityBulkEmailBatch(Document):
	pass
