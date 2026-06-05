# Seeds clean, fake demo data for Unity Helpdesk media capture (no real PII).
# Run:  bench --site unity.local execute helpdesk.docs._capture.scripts.seed_demo.run
#   or: bench --site unity.local console  then  exec(open('<abs path>').read())
#
# Creates: a capture agent (System Manager + HD Agent), 3 demo ticket types,
# 2 demo families (guardians + student siblings) with program enrollment + fees
# in the current academic year, and ~8 demo tickets across statuses/types all
# assigned to the capture agent (so "My Tickets" + the agent-filtered Dashboard
# show only demo data). Idempotent: re-running wipes the prior demo set first.
#
# Education doctypes (Student/Guardian/Program Enrollment/Fees/...) are inserted
# with raw SQL to bypass the heavy edu_quality validation + Student override
# hooks -- we only need the fields the student-context resolver reads.

import json
import os

import frappe
from frappe.utils import nowdate, add_days
from frappe.utils.password import update_password

CAP_USER = "capture-agent@unity-demo.example.com"
CAP_PASS = "Capture@2026xyz"
GUARDIANS = [
    # gid, guardian_name, email, mobile, alt, relation
    ("DEMO-GARD-01", "Priya Sharma", "priya.sharma@unity-demo.example.com", "9820011111", "9820022222", "Mother"),
    ("DEMO-GARD-02", "Rahul Mehta", "rahul.mehta@unity-demo.example.com", "9820033333", "", "Father"),
]
STUDENTS = [
    # sid, student_name, first, last, ref, program, division, gid
    ("EDU-DEMO-0001", "Aarav Sharma", "Aarav", "Sharma", "WS-DEMO-1001", "Grade 5", "A", "DEMO-GARD-01"),
    ("EDU-DEMO-0002", "Anaya Sharma", "Anaya", "Sharma", "WS-DEMO-1002", "Grade 2", "B", "DEMO-GARD-01"),
    ("EDU-DEMO-0003", "Ishaan Mehta", "Ishaan", "Mehta", "WS-DEMO-1003", "Grade 8", "C", "DEMO-GARD-02"),
]
TICKET_TYPES = [
    ("Admission Enquiry", "Demo: new-admission and sibling-admission questions"),
    ("Fees & Payments", "Demo: fee receipts, payment schedules and clarifications"),
    ("Transport", "Demo: bus routes, pickup/drop and transport fees"),
]
TICKETS = [
    # subject, guardian_email, ticket_type, status, on_hold, with_thread
    ("Fee receipt not received for April",         "priya.sharma@unity-demo.example.com", "Fees & Payments",   "Open",     0, 1),
    ("Sibling admission for younger child",        "priya.sharma@unity-demo.example.com", "Admission Enquiry", "Open",     1, 0),
    ("Question about the annual day schedule",     "priya.sharma@unity-demo.example.com", "Admission Enquiry", "Replied",  0, 1),
    ("Bus route change request from next month",   "rahul.mehta@unity-demo.example.com",  "Transport",         "Open",     0, 0),
    ("Transport fee clarification",                "rahul.mehta@unity-demo.example.com",  "Transport",         "Replied",  0, 0),
    ("Please update our registered mobile number", "priya.sharma@unity-demo.example.com", "Fees & Payments",   "Resolved", 0, 0),
    ("Fee payment confirmation for term 1",        "rahul.mehta@unity-demo.example.com",  "Fees & Payments",   "Resolved", 0, 0),
    ("Login issue on the parent app",              "priya.sharma@unity-demo.example.com", "Admission Enquiry", "Closed",   0, 0),
]
# A distinctive token embedded only in a demo email body, used to demo the
# "search by email body content" feature with clean (demo-only) results. It is
# deliberately unique so it cannot match any real ticket, and it appears only in
# the body (not the subject), which proves the search reaches into the body.
BODY_SEARCH_TOKEN = "WALDEMO7788"
THREADS = {
    "Fee receipt not received for April": [
        ("Received", "priya.sharma@unity-demo.example.com",
         "Hello, I paid the April term fees last week via UPI (payment reference "
         + BODY_SEARCH_TOKEN
         + ") but I still haven't received the receipt by email. Could you please share it? Thank you."),
    ],
    "Question about the annual day schedule": [
        ("Received", "priya.sharma@unity-demo.example.com",
         "Hi team, could you tell me the date and timing for this year's annual day? We'd like to plan family travel."),
        ("Sent", CAP_USER,
         "Hello Mrs. Sharma, the annual day is on 14th December, 9:30 AM at the school auditorium. We'll share a detailed schedule soon."),
    ],
}


def _ins(table, row, child=None):
    row = dict(row)
    if child:
        row["parent"], row["parentfield"], row["parenttype"] = child
    row.setdefault("owner", "Administrator")
    row.setdefault("modified_by", "Administrator")
    row.setdefault("docstatus", 0)
    row.setdefault("idx", 0)
    cols = ["name", "creation", "modified"] + [c for c in row if c not in ("creation", "modified")]
    cols = list(dict.fromkeys(cols))
    ph, vals = [], []
    for c in cols:
        if c in ("creation", "modified"):
            ph.append("NOW()")
        else:
            ph.append("%s")
            vals.append(row[c])
    frappe.db.sql(
        "INSERT INTO `%s` (%s) VALUES (%s)" % (table, ",".join("`%s`" % c for c in cols), ",".join(ph)),
        tuple(vals),
    )


def run():
    frappe.set_user("Administrator")
    log = []
    guard_by = {g[0]: g for g in GUARDIANS}
    current_ay = frappe.db.get_value("Academic Year", {"custom_current_academic_year": 1}, "name") \
        or frappe.db.get_value("Academic Year", {}, "name")
    priority = frappe.db.get_value("HD Ticket Priority", {}, "name")
    log.append("academic_year=%s priority=%s" % (current_ay, priority))

    # 1) cleanup prior demo set (idempotent)
    demo_emails = [g[2] for g in GUARDIANS]
    old_tickets = frappe.get_all("HD Ticket", filters={"raised_by": ["in", demo_emails + [CAP_USER]]}, pluck="name")
    if old_tickets:
        # Raw-SQL deletes: avoids the on_trash hooks (which enqueue search-index /
        # notification jobs whose workers then lock these rows under us).
        names = [str(t) for t in old_tickets]
        frappe.db.sql("DELETE FROM `tabToDo` WHERE reference_type='HD Ticket' AND reference_name IN %(n)s", {"n": names})
        frappe.db.sql("DELETE FROM `tabCommunication` WHERE reference_doctype='HD Ticket' AND reference_name IN %(n)s", {"n": names})
        frappe.db.sql("DELETE FROM `tabHD Ticket Comment` WHERE reference_ticket IN %(n)s", {"n": names})
        frappe.db.sql("DELETE FROM `tabHD Ticket` WHERE name IN %(n)s", {"n": names})
        frappe.db.commit()
    for tt, _desc in TICKET_TYPES:
        if frappe.db.exists("HD Ticket Type", tt):
            frappe.delete_doc("HD Ticket Type", tt, force=True, ignore_permissions=True)
    sids = [s[0] for s in STUDENTS]
    gids = [g[0] for g in GUARDIANS]
    frappe.db.sql("DELETE FROM `tabPayment Schedule` WHERE parenttype='Fees' AND parent IN (SELECT name FROM `tabFees` WHERE student IN %(s)s)", {"s": sids})
    frappe.db.sql("DELETE FROM `tabFees` WHERE student IN %(s)s", {"s": sids})
    frappe.db.sql("DELETE FROM `tabProgram Enrollment` WHERE student IN %(s)s", {"s": sids})
    frappe.db.sql("DELETE FROM `tabStudent Guardian` WHERE parenttype='Student' AND parent IN %(s)s", {"s": sids})
    frappe.db.sql("DELETE FROM `tabGuardian Student` WHERE parenttype='Guardian' AND parent IN %(g)s", {"g": gids})
    frappe.db.sql("DELETE FROM `tabStudent` WHERE name IN %(s)s", {"s": sids})
    frappe.db.sql("DELETE FROM `tabGuardian` WHERE name IN %(g)s", {"g": gids})
    log.append("cleaned prior demo set")

    # 2) capture agent user (super admin -> all capabilities) + HD Agent
    if not frappe.db.exists("User", CAP_USER):
        u = frappe.get_doc({
            "doctype": "User", "email": CAP_USER, "first_name": "Helpdesk", "last_name": "Demo",
            "send_welcome_email": 0, "user_type": "System User", "roles": [{"role": "System Manager"}],
        }).insert(ignore_permissions=True)
        log.append("created capture user")
    else:
        u = frappe.get_doc("User", CAP_USER)
        if "System Manager" not in {r.role for r in u.roles}:
            u.append("roles", {"role": "System Manager"})
        u.enabled = 1
        u.save(ignore_permissions=True)
        log.append("updated capture user")
    update_password(CAP_USER, CAP_PASS)
    if not frappe.db.exists("HD Agent", CAP_USER):
        frappe.get_doc({"doctype": "HD Agent", "user": CAP_USER, "is_active": 1}).insert(ignore_permissions=True)
        log.append("created HD Agent")

    # 3) guardians + students + bidirectional links (raw SQL)
    for gid, gfull, gemail, gmob, galt, _rel in GUARDIANS:
        _ins("tabGuardian", {
            "name": gid, "guardian_name": gfull, "email_address": gemail,
            "mobile_number": gmob, "alternate_number": galt, "custom_enabled": 1,
        })
    for sid, sname, first, last, ref, program, div, gid in STUDENTS:
        g = guard_by[gid]
        _ins("tabStudent", {
            "name": sid, "student_name": sname, "first_name": first, "last_name": last,
            "reference_number": ref, "custom_reference_number": ref, "program": program,
            "custom_division": div, "student_status": "Active", "enabled": 1,
            "is_sibling_in_school": 1 if gid == "DEMO-GARD-01" else 0,
            "student_mobile_number": "9700000000", "primary_contact": "9700000000",
        })
        _ins("tabStudent Guardian",
             {"name": frappe.generate_hash(length=10), "guardian": gid, "guardian_name": g[1],
              "email": g[2], "relation": g[5]},
             child=(sid, "guardians", "Student"))
        _ins("tabGuardian Student",
             {"name": frappe.generate_hash(length=10), "student": sid, "student_name": sname},
             child=(gid, "students", "Guardian"))
    log.append("created %d guardians, %d students + links" % (len(GUARDIANS), len(STUDENTS)))

    # 4) program enrollment + fees + payment schedule (raw SQL)
    for sid, sname, first, last, ref, program, div, gid in STUDENTS:
        enr = "EDU-ENR-DEMO-%s" % sid.split("-")[-1]
        _ins("tabProgram Enrollment", {
            "name": enr, "student": sid, "student_name": sname, "program": program,
            "academic_year": current_ay, "enrollment_date": nowdate(),
            "docstatus": 1,
        })
        fee = "EDU-FEE-DEMO-%s" % sid.split("-")[-1]
        _ins("tabFees", {
            "name": fee, "student": sid, "student_name": sname, "program_enrollment": enr,
            "program": program, "academic_year": current_ay, "posting_date": nowdate(),
            "due_date": add_days(nowdate(), 30), "grand_total": 60000,
            "outstanding_amount": 25000, "currency": "INR", "payment_plan": "Two Installments",
            "docstatus": 1,
        })
        for i, (term, amt, out, status, due) in enumerate([
            ("Term 1", 30000, 0, "Paid", add_days(nowdate(), -30)),
            ("Term 2", 30000, 25000, "Unpaid", add_days(nowdate(), 30)),
        ], start=1):
            _ins("tabPayment Schedule",
                 {"name": frappe.generate_hash(length=10), "payment_term": term,
                  "description": "%s fees (%s)" % (program, term), "due_date": due,
                  "payment_amount": amt, "outstanding": out, "payment_status": status, "idx": i},
                 child=(fee, "payment_schedule", "Fees"))
    log.append("created enrollment + fees + schedules for %d students" % len(STUDENTS))

    # 5) ticket types
    for tt, desc in TICKET_TYPES:
        doc = {"doctype": "HD Ticket Type", "name": tt, "description": desc}
        if priority:
            doc["priority"] = priority
        frappe.get_doc(doc).insert(ignore_permissions=True)
    log.append("created %d ticket types" % len(TICKET_TYPES))

    # 6) demo tickets (ORM, assigned to capture agent)
    created = []
    for subject, gemail, ttype, status, on_hold, with_thread in TICKETS:
        t = frappe.get_doc({
            "doctype": "HD Ticket", "subject": subject, "raised_by": gemail,
            "ticket_type": ttype, "status": "Open", "priority": priority or None,
            "description": "Demo ticket for media capture. Raised by %s." % gemail,
        })
        t.flags.ignore_mandatory = True
        t.insert(ignore_permissions=True)
        if status != "Open":
            # set terminal/replied status directly to bypass resolve/close validation
            frappe.db.set_value("HD Ticket", t.name, "status", status)
        if on_hold:
            frappe.db.set_value("HD Ticket", t.name, {
                "custom_is_on_hold": 1, "custom_hold_from": nowdate(),
                "custom_hold_to": add_days(nowdate(), 14),
                "custom_hold_reason": "Awaiting younger child's birth certificate from parent.",
            })
        if with_thread:
            for sent_recv, sender, content in THREADS.get(subject, []):
                frappe.get_doc({
                    "doctype": "Communication", "communication_type": "Communication",
                    "communication_medium": "Email", "sent_or_received": sent_recv,
                    "subject": subject, "content": content, "text_content": content,
                    "sender": sender, "reference_doctype": "HD Ticket", "reference_name": t.name,
                }).insert(ignore_permissions=True)
        # An assignment rule auto-assigns new tickets to a real agent on insert
        # (creating ToDos that would surface that agent's name in the Assignment
        # History panel). Clear ALL assignments for this ticket, then assign only
        # the capture agent. The "My Tickets" view resolves assignments via the
        # indexed ToDo table (owner=user, status=Open); the list's "Assigned To"
        # column and the Assignment History panel both read these -- so reset both.
        frappe.db.sql("DELETE FROM `tabToDo` WHERE reference_type='HD Ticket' AND reference_name=%s", str(t.name))
        frappe.db.set_value("HD Ticket", t.name, "_assign", frappe.as_json([CAP_USER]))
        _ins("tabToDo", {
            "name": frappe.generate_hash(length=10), "owner": CAP_USER,
            "allocated_to": CAP_USER, "assigned_by": "Administrator",
            "reference_type": "HD Ticket", "reference_name": str(t.name),
            "status": "Open", "priority": "Medium", "date": nowdate(),
            "description": "Assigned: %s" % subject,
        })
        created.append((t.name, status, ttype))
    log.append("created %d demo tickets" % len(created))

    # write meta the capture script reads (hero ticket id changes each reseed)
    meta = {
        "capture_user": CAP_USER, "capture_pass": CAP_PASS,
        "hero_ticket": str(created[0][0]),
        "all_tickets_type": "Fees & Payments",
        "guardian_emails": [g[2] for g in GUARDIANS],
        "agent_label": "Helpdesk Demo",
        "search_ref": STUDENTS[0][4],            # WS-DEMO-1001 (ref-number search)
        "search_guardian": GUARDIANS[0][2],      # priya.sharma@... (family-aware email search)
        "search_body": BODY_SEARCH_TOKEN,        # WALDEMO7788 (email-body-content search)
    }
    try:
        meta_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo_meta.json")
        with open(meta_path, "w") as fh:
            json.dump(meta, fh, indent=2)
        log.append("wrote demo_meta.json")
    except Exception as e:
        log.append("could not write demo_meta.json: %s" % e)

    frappe.db.commit()
    print("SEED OK")
    for l in log:
        print(" -", l)
    print("CAPTURE_USER", CAP_USER, CAP_PASS)
    print("HERO_TICKET", created[0][0])
    print("ALL_TICKETS_TYPE_FILTER Fees & Payments")
    print("DEMO_GUARDIAN_EMAILS", ", ".join(g[2] for g in GUARDIANS))
    print("TICKETS", created)


run()
