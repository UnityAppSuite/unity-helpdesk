# Provision (or tear down) a pool of load-test accounts on a LOCAL site.
#
# Run by piping into the bench console (it executes top-to-bottom):
#
#   # create 30 agent accounts:
#   cat loadtest/setup_test_users.py | bench --site unity.local console
#
#   # custom count / password:
#   LOADTEST_N=50 LOADTEST_PWD='Secret@123' \
#       bench --site unity.local console < loadtest/setup_test_users.py
#
#   # tear them all down:
#   LOADTEST_TEARDOWN=1 bench --site unity.local console < loadtest/setup_test_users.py
#
# IMPORTANT — these are real, login-capable accounts. Create them ONLY on a local
# / staging site, never prod. They are named loadtest{N}@example.com so they're
# easy to spot and remove.
#
# Why HD Agent records (not just a role)?
#   helpdesk's HD Ticket permission filter (hd_ticket.permission_query) treats a
#   non-agent as a customer and restricts + slows the query (an OR across
#   contact/raised_by/owner). `is_agent()` is true only when an *HD Agent* record
#   exists for the user. So to load-test the real "All Tickets" agent path, each
#   test user needs BOTH the "Helpdesk Admin" role AND an HD Agent record.
#
# After running, build loadtest/users.csv:
#   printf 'usr,pwd\n' > loadtest/users.csv
#   for i in $(seq 1 30); do \
#       printf 'loadtest%s@example.com,Loadtest@123\n' "$i" >> loadtest/users.csv; done

import os

import frappe

N = int(os.getenv("LOADTEST_N", "30"))
PWD = os.getenv("LOADTEST_PWD", "Loadtest@123")
ROLE = "Helpdesk Admin"
TEARDOWN = os.getenv("LOADTEST_TEARDOWN") in ("1", "true", "yes")


def _email(i):
    return f"loadtest{i}@example.com"


if TEARDOWN:
    removed = 0
    for i in range(1, N + 1):
        email = _email(i)
        if frappe.db.exists("HD Agent", email):
            frappe.delete_doc("HD Agent", email, ignore_permissions=True, force=True)
        if frappe.db.exists("User", email):
            frappe.delete_doc("User", email, ignore_permissions=True, force=True)
            removed += 1
    frappe.db.commit()
    print(f"[loadtest] removed {removed} test users")
else:
    if not frappe.db.exists("Role", ROLE):
        frappe.get_doc({"doctype": "Role", "role_name": ROLE}).insert(ignore_permissions=True)
    users, agents = 0, 0
    for i in range(1, N + 1):
        email = _email(i)
        if not frappe.db.exists("User", email):
            u = frappe.get_doc({
                "doctype": "User", "email": email, "first_name": f"Loadtest {i}",
                "send_welcome_email": 0, "user_type": "System User",
            })
            u.flags.no_welcome_mail = True
            u.insert(ignore_permissions=True)
            u.new_password = PWD
            u.add_roles(ROLE)
            u.save(ignore_permissions=True)
            users += 1
        if not frappe.db.exists("HD Agent", email):
            frappe.get_doc({"doctype": "HD Agent", "user": email, "is_active": 1}).insert(
                ignore_permissions=True
            )
            agents += 1
    frappe.db.commit()
    print(f"[loadtest] created {users} users, {agents} HD Agent records "
          f"(pool size {N}, password '{PWD}')")
