# Unity Helpdesk — Complete Technical & Non-Technical Report

> **Scope:** This document explains the *new* Helpdesk used by Walnut School — a fork of
> Frappe Helpdesk (`UnityAppSuite/helpdesk`, branch `rechecking`) — covering what it is,
> what we added on top of the stock product, how it works end-to-end, and how it was
> improved for scale. It is written in two halves: a **non-technical** overview for
> stakeholders, and a **technical** reference for developers.

---

# PART 1 — NON-TECHNICAL OVERVIEW

## 1.1 What is Unity Helpdesk?

Unity Helpdesk is Walnut's customised support-ticketing system. It is built on top of
the open-source **Frappe Helpdesk** product but heavily extended for a **school
environment**, where every support request is usually about a **student**, raised by a
**parent/guardian**, and often relates to **fees, classes, or admissions**.

In plain terms: when a parent emails or writes in, a *ticket* is created. Agents (support
staff) see the ticket, automatically see *which student and family it belongs to*, reply,
put it on hold, reassign it, and close it — all from a fast, purpose-built web screen.

There are effectively **two front-ends** living in the same app:

| Front-end | URL | Who uses it | Notes |
|-----------|-----|-------------|-------|
| **Stock Frappe Helpdesk SPA** | `/helpdesk` | (legacy / fallback) | The original product UI. Can auto-redirect to Unity. |
| **Unity Helpdesk SPA** *(our build)* | `/unity-helpdesk` | Walnut support agents | The new, lightweight, education-focused UI we built. |

The new work is almost entirely in the **Unity Helpdesk SPA** and a set of **Unity backend
APIs**. The original product was left intact underneath so we stay upgrade-compatible.

## 1.2 The problem we were solving

Stock Frappe Helpdesk is a general-purpose customer-support tool. For a school it had gaps:

1. **No student/family awareness** — an agent reading a parent's email had no idea which
   child, class, or fee record it referred to without manually searching other systems.
2. **Search was weak at scale** — Walnut runs ~90,000+ tickets. Searching by student name,
   reference number, guardian email or message body was slow or impossible.
3. **No bulk communication** — schools routinely email *all* parents of a class. There was
   no built-in, audited way to do that from the helpdesk.
4. **List/dashboard performance** — large ticket volumes made list pages and counters slow.
5. **No education-specific workflow** — "on hold until fees clear", guardian lookups,
   sibling context, previous-ticket history, etc.

## 1.3 What we added (headline features)

| # | Feature | What it does for the user |
|---|---------|---------------------------|
| 1 | **Student & Guardian Context Panel** | On every ticket, automatically shows the student(s), their class, the guardians (name/mobile/email), siblings, fees and payment schedule — pulled live from the Education app. No manual lookup. |
| 2 | **Education-aware Search** | Search a ticket by ticket ID, **student name**, **student reference number**, **parent/guardian email**, subject, or **anything in the email body**. "Family-aware" — searching a guardian's email finds all their children's tickets. |
| 3 | **Bulk Email to Parents** | Compose one email to many parents/guardians (with CSV import, "include guardians" auto-expand, CC/BCC). Each send is **audited** as a special ticket so you can prove who received what. |
| 4 | **Advanced Dashboard** | Date-range KPIs (Today / Week / Month / Quarter / Year / Custom), per-agent filter, a **donut chart** of ticket types and a **stacked status-trend chart**. |
| 5 | **On-Hold workflow** | Put a ticket on hold with a *from/to date* and a *reason* (e.g. waiting on parent / fees), tracked separately from status. |
| 6 | **Bulk actions on the list** | Select many tickets and change status / priority / assignee / type / team in one go, with a progress bar. |
| 7 | **Customisable ticket list** | Show/hide, reorder (drag), and resize columns; preferences saved per user. |
| 8 | **Two email-thread layouts** | Read the conversation as a **Classic** chronological log or a **Chat-Based** bubble view — a per-user setting. |
| 9 | **Ticket-type colours & keywords** | Admins give each ticket type a colour (for quick visual scanning) and **keywords** that auto-classify incoming tickets. |
| 10 | **Reply templates / canned responses** | Multi-language (English/Hindi/Marathi) saved replies with categories. |
| 11 | **Live search suggestions** | As-you-type dropdown, recent searches, and a `Ctrl+K` shortcut. |
| 12 | **Reply-chain linking** | When a parent replies to a bulk email, the reply is automatically linked back to the original send for traceability. |

## 1.4 How it was improved (performance & reliability)

The fork is tuned for Walnut's large ticket volume:

- **Faster lists & counters** — purpose-built database indexes and lighter queries make the
  ticket list and the KPI cards load quickly even with tens of thousands of tickets.
- **Smarter search** — short queries use fast pattern matching; long queries (whole
  paragraphs pasted from an email) fall back to a database **FULLTEXT** index. The system
  caches whether the index exists so it never wastes time checking.
- **Cached dashboard** — the summary cards are cached in Redis for ~30 seconds, so repeated
  loads are near-instant; cards show a "…" placeholder during the brief gap instead of
  jumping.
- **Background, non-blocking upgrades** — heavy one-time data backfills (building the
  searchable message body for old tickets, populating student/guardian search fields) run
  in the background so deployments/migrations stay fast and the site stays responsive.
- **Audited, efficient bulk email** — one email is sent to many recipients rather than
  thousands of individual sends, while an audit mailbox still sees the full recipient list.

## 1.5 Who uses it (roles)

- **Helpdesk User / Agent** — handle tickets (view, reply, assign, hold, close).
- **Helpdesk Admin / Super Admin** — additionally manage agents, ticket types & colours,
  reply templates, Unity settings, and can send bulk email / view all tickets and the
  full dashboard.
- **Parents / Guardians** — interact via **email** (and the portal); they don't use the
  agent SPA. Their messages become tickets and their replies re-open tickets automatically.

## 1.6 The day-to-day flow in plain words

1. A parent emails support (or a ticket is created via the portal/SPA).
2. A ticket is created, auto-classified by **type** (via keywords) and **priority**, an
   **SLA** clock starts, and it's routed to a **team/agent**.
3. The agent opens the ticket and immediately sees the **student & guardian context**.
4. The agent replies (email goes to the parent), adds **internal notes**, or puts the
   ticket **on hold**.
5. A parent reply re-opens the ticket; the conversation continues.
6. When done, the agent marks it **Resolved/Closed**; SLA is marked fulfilled.
7. Managers watch the **dashboard** for volumes, types and trends; staff can send **bulk
   emails** to groups of parents as needed.

---

# PART 2 — TECHNICAL REFERENCE

## 2.1 Repository, fork & branch

- **Origin (our fork):** `https://github.com/UnityAppSuite/helpdesk.git`
- **Upstream:** `https://github.com/frappe/helpdesk.git` (`frappe` remote)
- **Current working branch:** `rechecking`
- Recent themes (from history): performance optimisations, parallel student-context,
  dynamic ticket-type colours, Redis-cached dashboard summary, FULLTEXT-first search,
  asymmetric bulk-email send.

## 2.2 High-level architecture

```
                         ┌──────────────────────────────────────────────┐
                         │                  Browser                      │
                         │                                              │
   /unity-helpdesk  ───► │  Unity Helpdesk SPA (Vue 3 + Vite)  ◄── ours │
   /helpdesk        ───► │  Stock Helpdesk SPA (Vue 3 + Vite)           │
                         └───────────────┬──────────────────────────────┘
                                         │ /api/method/... (CSRF + session)
                                         ▼
        ┌────────────────────────────────────────────────────────────────────┐
        │                     Frappe Framework (Python)                        │
        │                                                                      │
        │  Unity API layer            Standard Helpdesk           Hooks/Patches│
        │  • unity_helpdesk.py        • hd_ticket.py              • hooks.py   │
        │  • unity_helpdesk_ext.py    • hd_service_level_agree…   • patches/   │
        │  • unity_perf.py            • hd_agent / hd_team        • setup/      │
        │  • search.py                • api/ , extends/                         │
        └───────────────┬───────────────────────────┬──────────────────────────┘
                        ▼                           ▼
                ┌───────────────┐           ┌──────────────────┐
                │   MariaDB     │           │      Redis        │
                │  HD Ticket +  │           │  • summary cache  │
                │  indexes/FT   │           │  • comm-text cache│
                │  Communication│           │  • RediSearch idx │
                └───────────────┘           └──────────────────┘
                        ▲
                        │ student/guardian/fees joins
                ┌───────────────────────────┐
                │ Education app (Student,    │
                │ Guardian, Program          │
                │ Enrollment, Fees, …)       │
                └───────────────────────────┘
```

**Key idea:** the Unity SPA talks almost exclusively to the **Unity API layer**
(`helpdesk.api.unity_helpdesk` and `helpdesk.api.unity_helpdesk_ext`), which wraps and
optimises the standard Helpdesk doctypes rather than replacing them.

## 2.3 Serving & build model

Both SPAs are independent Vite builds served by Frappe website routes (defined in
`helpdesk/hooks.py`):

| Route | Controller | Template | SPA assets |
|-------|-----------|----------|-----------|
| `/helpdesk`, `/helpdesk/<path>` | `www/helpdesk/index.py` | `www/helpdesk/index.html` | `/assets/helpdesk/desk/` |
| `/unity-helpdesk`, `/unity-helpdesk/<path>` | `www/unity_helpdesk/index.py` | `www/unity_helpdesk/index.html` | `/assets/helpdesk/unity_helpdesk/` |

- The stock `/helpdesk` controller checks `HD Settings.helpdesk_ui`; if set to
  `"Unity Helpdesk"`, it **redirects to `/unity-helpdesk`**.
- Each controller injects `csrf_token`, `frappe_version`, `helpdesk_version`, `site_name`
  into the page (`window.*`) and sets `no_cache = 1`.
- **Unity SPA build** (`unity_helpdesk/vite.config.js`, `package.json`):
  - `outDir: ../helpdesk/public/unity_helpdesk`
  - `build`: `vite build --base=/assets/helpdesk/unity_helpdesk/ && yarn copy-html-entry`
  - `copy-html-entry` copies the built `index.html` into `www/unity_helpdesk/index.html`.

## 2.4 Unity SPA (front-end) — structure

Source: `unity_helpdesk/src/`

- **`main.js`** — Vue Router (mount base `/unity-helpdesk/`):
  - `/` → redirect `/tickets/my`
  - `/dashboard` → `DashboardView`
  - `/tickets/my` and `/tickets/all` → `TicketsView` (`props.view = "my" | "all"`)
  - `/tickets/:ticketId` → `TicketDetailView`
  - `/settings` → `ProfileView`
- **`App.vue`** — app shell: sidebar nav (role-gated), topbar, **Create Ticket** composer,
  **Bulk Email** composer, session bootstrap. Provides `unitySession`, `unityAgents`,
  `unityTicketTypes` to child views via `provide/inject`.
- **`api.js`** — the single HTTP client:
  - `call(method, params, options)` — `POST /api/method/<method>`, JSON, CSRF header.
  - Auto-recovers from CSRF errors by fetching a fresh token via
    `helpdesk.api.unity_helpdesk.get_csrf_token` and **retrying once**.
  - On auth failure → `redirectToLogin()` (`/login?redirect-to=<path>`).
  - `callWithRetry()` — backoff retries (1s/3s/7s) for idempotent calls.
  - `uploadAttachment()` — Frappe `upload_file` (private).
  - `sanitize()` — DOMPurify on all rendered HTML.

### Views → backend endpoints (verified from `api.js` / `*.vue`)

| View | Purpose | Endpoints called |
|------|---------|------------------|
| **DashboardView** | KPI cards + donut + status-trend, date-range & agent filters | `unity_helpdesk.get_dashboard_summary`, `unity_helpdesk.get_agents` |
| **TicketsView** | List, filters, search, suggestions, bulk edit, column prefs | `unity_helpdesk.get_tickets_page`, `unity_helpdesk.get_tickets_summary`, `unity_helpdesk.get_ticket_suggestions`, `unity_helpdesk.bulk_update_tickets`, `unity_helpdesk.update_column_preferences`, `unity_helpdesk.get_agents`, `unity_helpdesk.get_ticket_types` |
| **TicketDetailView** | Thread, student context, reply/note, update, hold, history, prev tickets | `unity_helpdesk_ext.get_ticket_detail`, `unity_helpdesk.get_student_context`, `unity_helpdesk_ext.reply`, `unity_helpdesk_ext.add_comment`, `unity_helpdesk_ext.update_ticket`, `unity_helpdesk.get_accessible_ticket_summaries`, `unity_helpdesk.get_bulk_emails_received_by` |
| **ProfileView** (Settings) | Profile, Unity settings, ticket types (colour/keywords), reply templates | `unity_helpdesk.get_profile`, `unity_helpdesk.update_unity_settings`, `unity_helpdesk.list_ticket_types_with_keywords`, `unity_helpdesk.create_ticket_type`, `unity_helpdesk.update_ticket_type_keywords`, `unity_helpdesk.update_ticket_type_color`, `helpdesk.api.reply_templates.*` |
| **UsersView** | Agent management | `unity_helpdesk.get_agents`, `unity_helpdesk.get_agent_candidates`, `unity_helpdesk.create_agent` |
| **App shell** | Create ticket / bulk email / contact search | `unity_helpdesk_ext.create_ticket`, `unity_helpdesk_ext.bulk_send_email`, `unity_helpdesk_ext.get_bulk_email_sample_csv`, `unity_helpdesk.search_contacts`, `unity_helpdesk.get_student_guardian_emails` |

> **Note:** TicketDetailView fetches the ticket and the student context in **parallel**
> (`get_ticket_detail` + `get_student_context`) so the heavier education join doesn't block
> first paint.

## 2.5 Unity API layer (back-end)

### `helpdesk/api/unity_helpdesk.py` (core engine, ~4,100 lines)

The main read/optimise engine. Notable whitelisted endpoints:

- **Listing / search:** `get_tickets_page`, `get_tickets_summary`, `get_tickets`
  (back-compat wrapper), `get_ticket_suggestions`.
- **Detail / context:** `get_ticket_detail`, `get_student_context`,
  `get_accessible_ticket_summaries`, `get_bulk_emails_received_by`.
- **Mutations:** `create_ticket`, `update_ticket`, `reply`, `bulk_update_tickets`
  (max 500 names).
- **Dashboard:** `get_dashboard_summary` (range/from/to/agent → cards + type breakdown +
  status trend).
- **Admin / lookups:** `get_profile`, `get_agents`, `get_agent_candidates`, `create_agent`,
  `get_ticket_types`, `create_ticket_type`, `list_ticket_types_with_keywords`,
  `update_ticket_type_color`, `update_ticket_type_keywords`, `search_contacts`,
  `get_student_guardian_emails`, `update_column_preferences`, `update_unity_settings`,
  `get_csrf_token`, `enqueue_auto_assign_ticket_types`.
- **Diagnostics:** `backfill_ticket_message_search_fields`,
  `diagnose_ticket_thread_and_search`.

Internals worth knowing:

- **Custom fields recognised** (`UNITY_TICKET_FIELDS`): `custom_is_on_hold`,
  `custom_hold_from/to/reason`, `custom_is_bulk_email`, `custom_via_unity_portal`,
  `custom_bulk_email_recipients`, `custom_replied_to_ticket`, plus search/denormalised
  fields.
- **Per-request cache** (`frappe.local._unity_request_cache`) for assigned-ticket names,
  resolved search/pagination context, and guardian-email-by-student lookups.
- **Redis caches:** dashboard summary `unity:tickets:summary:{md5(user+view+filters)}`
  (30s TTL, empty-search path only); communication text `helpdesk_comm_text:{ticket}` (5m).
- **Assignment fast-path:** reads the indexed **ToDo** table (owner/reference/status)
  instead of `_assign LIKE '%user%'` full scans.
- **7-tier search ranking** (exact ID → student ref → email/family email → prefix →
  all-tokens-in-name → query-in-subject/description → all-tokens-in-body).

### `helpdesk/api/unity_helpdesk_ext.py` (CRUD + bulk email, ~800 lines)

Loaded fresh on restart to avoid stale in-memory state. Endpoints: `get_ticket_detail`,
`create_ticket`, `update_ticket`, `reply`, `add_comment`, `bulk_send_email`,
`get_bulk_email_sample_csv`.

**Bulk email (asymmetric send) — the audit pattern:**

- Limits: `RECIPIENT_HARD_CAP = 1000`, `TOTAL_ADDRESS_HARD_CAP = 1500`.
- Validated, then enqueued as `_bulk_send_email_job` (queue `short`).
- The job:
  1. creates one **audit HD Ticket** (`custom_via_unity_portal=1`, `custom_is_bulk_email=1`)
     with the full recipient list denormalised into `custom_bulk_email_recipients`,
  2. creates a single **Communication**,
  3. sends **one** `frappe.sendmail(expose_recipients=None)` — recipients + CC + the
     **default bulk/audit recipients** — so real parents don't see each other, but the
     audit mailbox *does* receive the message and can see the recipient list (rendered in a
     collapsible `<details>` block).

### `helpdesk/api/unity_perf.py` (diagnostics, ~560 lines)

Operator tooling — not user-facing: `run_filter_benchmark`, `run_endpoint_benchmark`,
`print_search_diagnostic`, `diagnose_guardian_lookup`, `print_backfill_status` (tracks the
three backfill jobs and their RQ state).

### `helpdesk/search.py` (RediSearch, ~230 lines)

`Search` / `HelpdeskSearch` wrap a Redis full-text index (fields: `name` w5, `subject` w2,
`description` w1, `team` tag, `creation`/`modified` sortable). Whitelisted `search(query)`
returns agent-gated results. Index lifecycle: `build_index`, `build_index_in_background`
(on `after_migrate`), `build_index_if_not_exists` (scheduler `all`).

## 2.6 Data model (standard doctypes the Unity layer builds on)

- **HD Ticket** — central object. Status `Open → Replied → Resolved → Closed`; links to
  `contact`, `customer`, `ticket_type`, `priority`, `agent_group` (HD Team), `sla`;
  SLA timestamps (`response_by`, `resolution_by`, `first_responded_on`, `resolution_date`),
  `agreement_status`, `_assign`.
- **HD Team / HD Agent** — teams own an auto-created **Assignment Rule**
  (`status=='Open' and agent_group=='<team>'`); agents are added/removed from rotations on
  `is_active`/`groups` changes.
- **HD Ticket Type** — `keywords` (word-boundary auto-classification), `priority`,
  and our `custom_color`.
- **HD Ticket Priority** — `integer_value` (lower = higher priority).
- **HD Ticket Comment** — internal notes. **HD Ticket Activity** — audit trail.
- **HD Service Level Agreement** + Priority/Service Day/Holiday children — response &
  resolution targets computed against working hours, with pause/hold support.
- **Communication** — Frappe's email/message records; inbound replies re-open tickets.

## 2.7 Custom fields & indexes added (the schema deltas)

**Custom fields on `HD Ticket`:**

| Field | Type | Purpose |
|-------|------|---------|
| `custom_via_unity_portal` | Check | Ticket originated in the SPA/portal (vs inbound email) |
| `custom_is_bulk_email` | Check | Marks a bulk-email **audit** ticket |
| `custom_bulk_email_recipients` | Long Text | Denormalised recipient list of a bulk send |
| `custom_replied_to_ticket` | Link → HD Ticket | Links a reply back to the bulk/portal source |
| `custom_is_on_hold`, `custom_hold_from`, `custom_hold_to`, `custom_hold_reason` | Check/Date/Text | On-hold workflow |
| `custom_search_student_names` | Data | Denormalised student names (search) |
| `custom_search_student_refs` | Data | Denormalised student reference numbers (search) |
| `custom_search_guardian_emails` | Data | Denormalised guardian emails (search) |
| `custom_primary_message_html` / `custom_primary_message_text` | Text | Cached first message (fast render) |
| `custom_search_message_body` | Small Text | Searchable head+tail of all messages |

**On `HD Ticket Type`:** `custom_color` (Color).
**On `HD Canned Response`:** `category` (Link), `language` (Select EN/HI/MR),
`subject_template` (Data), `is_active` (Check).

**Indexes on `HD Ticket`:** `raised_by_unity_idx`, `creation_unity_idx`,
`modified_unity_idx`, `status_modified_unity_idx (status, modified)`,
`on_hold_modified_unity_idx (custom_is_on_hold, modified)`, `contact_unity_idx`,
`owner_unity_idx`, and a **FULLTEXT** `search_body_ft_idx` over
`(custom_search_message_body, subject, custom_search_student_names,
custom_search_student_refs, custom_search_guardian_emails)`.

## 2.8 Hooks (`helpdesk/hooks.py`)

- **Install:** `before_install`/`after_install` (`setup.install`); `after_migrate` →
  `search.build_index_in_background`.
- **Scheduler:** `all` → `search.build_index_if_not_exists`; `daily` →
  `unity_helpdesk.send_open_ticket_reminders`.
- **Website routes:** `/helpdesk` and `/unity-helpdesk` (see §2.3).
- **doc_events:**
  - `Contact.before_insert` → auto-link to HD Customer by email domain.
  - `Assignment Rule.on_trash` → block deleting the last ticket assignment rule.
  - `HD Ticket.after_insert` → populate student search fields + refresh message-search index.
  - `Communication.after_insert` / `on_update` → refresh message-search index; link
    bulk-email replies (`custom_replied_to_ticket`).
  - `HD Ticket Comment.after_insert` / `on_update` → refresh message-search index.
  - All search-index hooks are wrapped in a defensive `_safe()` — they log but never raise,
    so ticket/email ingestion can't break on an index error.
- **Permissions:** `has_permission` + `permission_query_conditions` for HD Ticket
  (contact / raised_by / owner / agent / customer).

## 2.9 Patches & install order (`patches.txt`, `setup/install.py`)

Patches are grouped: **schema/custom-field** patches, **index** patches, and deferred
**backfill** jobs (long queue, 50-row batches, idempotent, short-circuit when complete):

- Custom fields: `unity_ticket_message_search_fields`,
  `unity_helpdesk_student_search_fields`, `unity_helpdesk_portal_origin_fields`,
  `unity_reply_link_field`, `unity_bulk_email_recipients_field`,
  `unity_canned_response_extension`, `unity_ticket_type_color_field`.
- Indexes: `unity_ticket_creation_index`, `unity_ticket_list_indexes`,
  `unity_raised_by_index`, `unity_owner_index`, `unity_ticket_search_fulltext`.
- Backfills: `unity_ticket_message_search_rebuild`,
  `unity_bulk_email_recipients_backfill`, `unity_bulk_email_default_recipients_backfill`,
  + the student-search backfill; plus `unity_post_migrate_warmup` to pre-warm InnoDB.

> **Fresh-install gotcha (important):** fresh installs mark patches as *applied without
> running them*, so schema-affecting patches must also be invoked directly from
> `ensure_unity_custom_fields()` in `setup/install.py`. New schema patches **must** be added
> there or fresh sites will be missing those custom fields.
> *(This matches the team's standing rule — see project memory on install-time patches.)*

## 2.10 Performance optimisations (summary)

1. **Covering/composite indexes** for list + KPI queries (no filesort, index-only counts).
2. **FULLTEXT-first for long queries**; index-availability cached so it isn't re-checked.
3. **ToDo-based assignment lookups** instead of `_assign LIKE` scans.
4. **Redis dashboard cache** (30s) with "…" placeholders to avoid layout jumps.
5. **Communication-text cache** (5m) so repeated detail/search reads don't re-read the thread.
6. **Parallel student-context fetch** so the education join doesn't block ticket render.
7. **Deferred, chunked backfills** keep `bench migrate` fast; live hooks populate new rows
   inline while the backfill catches up on history.
8. **Post-migrate buffer-pool warmup** after large sweeps.

## 2.11 End-to-end flows

### Ticket lifecycle

```
CREATE  (email inbound  |  SPA create_ticket  |  portal/web form)
  └─ before_validate: set type (keywords) → priority → contact → customer
                      → escalation rule → SLA
  └─ before_save:     apply SLA (response_by / resolution_by)
  └─ after_insert:    log activity, publish "helpdesk:new-ticket",
                      populate student-search fields, message-search index,
                      auto-reply (if configured)
ASSIGN  (Assignment Rule rotation  |  manual assign_agent)
REPLY   agent → Communication(Sent) → email out → first_responded_on set → status Replied
        parent → Communication(Received) → status back to Open (re-open)
        + bulk-email reply auto-links via custom_replied_to_ticket
RESOLVE status Resolved → SLA resolution_date/time → agreement "Fulfilled"
CLOSE   status Closed → locked to non-agents
```

### Search resolution

```
query ─► exact ticket-ID?  ─yes─► that ticket
        │ looks like email? ─► expand to guardian's whole family (students/refs/emails)
        │ long (≥4 tokens / >60 chars) & FT index exists? ─► MariaDB FULLTEXT candidates
        └─ else ─► multi-token AND-of-OR LIKE over name/subject/raised_by/student/guardian/body
                 ─► 7-tier ranking ─► paginate ─► bulk-decorate (no N+1) ─► return
```

### Bulk email

```
SPA bulk_send_email ─► validate (caps) ─► enqueue _bulk_send_email_job (queue: short)
   └─ create audit HD Ticket (custom_is_bulk_email=1, recipients denormalised)
   └─ create one Communication
   └─ one sendmail(expose_recipients=None): To=recipients, CC=cc, BCC=default audit mailbox
   └─ audit ticket shows collapsible recipient list; later parent replies link back
```

### Student/guardian context

```
get_student_context(ticket) ─► ticket.raised_by
   ├─ match Student.student_email_id  ─► student
   └─ else match Guardian.email_address ─► students of that guardian
        for each student: siblings + program enrollment + fees + payment schedule
                          + guardians (name/mobile/email) + school location
   ─► returned to SPA in parallel with ticket detail
```

## 2.12 Where to look (file map)

| Area | Path |
|------|------|
| Unity core API | `helpdesk/api/unity_helpdesk.py` |
| Unity CRUD + bulk email | `helpdesk/api/unity_helpdesk_ext.py` |
| Perf diagnostics | `helpdesk/api/unity_perf.py` |
| RediSearch | `helpdesk/search.py` |
| Hooks | `helpdesk/hooks.py` |
| Patches | `helpdesk/patches.txt`, `helpdesk/patches/` |
| Install / custom fields | `helpdesk/setup/install.py` (`ensure_unity_custom_fields`) |
| Doc-event handlers | `helpdesk/helpdesk/hooks/{contact,search_index,reply_link}.py` |
| Standard ticket model | `helpdesk/helpdesk/doctype/hd_ticket/hd_ticket.py` |
| SLA engine | `helpdesk/helpdesk/doctype/hd_service_level_agreement/` |
| Unity SPA source | `unity_helpdesk/src/` |
| SPA web route/controller | `helpdesk/www/unity_helpdesk/` |
| Existing topic docs | `docs/{bulk-email,configuration,education-integration,performance}.md` |

---

*Generated for the Walnut Unity Helpdesk fork (`UnityAppSuite/helpdesk`, branch `rechecking`).*
