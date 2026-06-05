# Education Integration (Students & Guardians)

Unity Helpdesk integrates with the Frappe **Education** app so support agents can see
student context on a ticket and broadcast to guardians.

## Guardian email lookup (bulk email)

When "Include guardian emails" is checked in the bulk-email composer, the SPA calls
`helpdesk.api.unity_helpdesk.get_student_guardian_emails` with the selected student
addresses. For each address it:

1. Finds the matching **Student** record, matching the address against **either**
   `Student.student_email_id` **or** `Student.user` (the linked User id, which on many
   sites *is* the student's email). Matching both fields is important — on Walnut data the
   `student_email_id` field is frequently empty while the real email lives on `user`.
2. Resolves that student's guardians and collects each `Guardian.email_address`.
3. Returns `{ mapping, diagnostic }`, where `mapping` is keyed by the **original input
   email** and `diagnostic` reports `input_count`, `students_matched`,
   `students_with_guardians`, and `unmatched_emails`.

The SPA surfaces a friendly, non-technical message when nothing resolves (e.g. "We
couldn't match any of the selected recipients to a student record, so no guardian emails
were added") instead of internal field names or shell commands.

## Diagnosing lookups

`helpdesk.api.unity_perf.diagnose_guardian_lookup(emails=[...])` steps through the same
pipeline (matching on both `student_email_id` and `user`) and reports where it breaks.
It is an admin/developer tool — it is not referenced in the end-user UI.

## Student context on a ticket

The ticket detail panel shows student/guardian/sibling context. Because that lookup runs
~10+ queries against Education doctypes, it is fetched **in parallel** by the SPA
(`get_student_context`) rather than inline in `get_ticket_detail`, so the ticket page
renders immediately and the panel fills in when ready.

## Relevant fields

- `Student.user` (Link → User), `Student.student_email_id` (Data)
- `Student Guardian` child table → `Guardian` → `Guardian.email_address`
