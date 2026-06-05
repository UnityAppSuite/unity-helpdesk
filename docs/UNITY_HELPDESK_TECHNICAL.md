# Unity Helpdesk — Technical Documentation

**Project:** Walnut School — Unity Helpdesk
**Repository:** `UnityAppSuite/helpdesk` (fork of `frappe/helpdesk`)
**Branch:** `rechecking`
**Audience:** Developers, DevOps, technical reviewers
**Companion document:** `UNITY_HELPDESK_FEATURES.md` (non-technical)

---

## Table of Contents

1. [Overview & Design Principle](#1-overview--design-principle)
2. [Technology Stack](#2-technology-stack)
3. [System Architecture](#3-system-architecture)
4. [Build & Serving Model](#4-build--serving-model)
5. [Front-End (Unity SPA)](#5-front-end-unity-spa)
6. [Back-End (Unity API Layer)](#6-back-end-unity-api-layer)
7. [Data Model & Doctypes](#7-data-model--doctypes)
8. [Custom Fields & Database Indexes](#8-custom-fields--database-indexes)
9. [Search — Deep Dive](#9-search--deep-dive)
10. [Bulk Email — Implementation](#10-bulk-email--implementation)
11. [Student / Guardian Context — Implementation](#11-student--guardian-context--implementation)
12. [Dashboard & Caching](#12-dashboard--caching)
13. [Hooks](#13-hooks)
14. [Patches, Backfills & Install](#14-patches-backfills--install)
15. [Performance Engineering](#15-performance-engineering)
16. [Security & Permissions](#16-security--permissions)
17. [Diagnostics & Operations](#17-diagnostics--operations)
18. [File / Module Map](#18-file--module-map)

---

## 1. Overview & Design Principle

Unity Helpdesk is a **fork** of the open-source Frappe Helpdesk product, extended for a
school environment where every ticket is typically about a **student**, raised by a
**parent/guardian**, and frequently relates to **fees, classes, or admissions**.

**Core design principle:** the stock product is left intact underneath. All Unity work is
**additive** —

- a separate **Unity SPA** (`/unity-helpdesk`) instead of modifying the stock SPA, and
- a separate **Unity API layer** that *wraps and optimises* the standard Helpdesk doctypes
  rather than replacing them.

This keeps the fork upgrade-compatible with upstream while layering education-specific
features and scale optimisations on top.

---

## 2. Technology Stack

| Layer | Technology |
|-------|-----------|
| **Application framework** | Frappe Framework (Python), running under a Frappe Bench |
| **Back-end language** | Python 3 (whitelisted `@frappe.whitelist()` RPC methods) |
| **Primary database** | MariaDB (InnoDB) — `HD Ticket`, `Communication`, indexes, FULLTEXT |
| **Cache / queue / search** | Redis — key/value cache, RQ background jobs, and RediSearch full-text index |
| **Background jobs** | Frappe RQ workers (queues: `short`, `long`) |
| **Front-end framework** | Vue 3 (Composition + Options API) |
| **Router** | Vue Router 4 |
| **Build tool** | Vite (separate builds for stock `desk/` and `unity_helpdesk/`) |
| **Rich text** | TinyMCE (reused from the stock `desk` SPA via alias) |
| **HTML sanitisation** | DOMPurify (client-side, on all rendered ticket HTML) |
| **Email** | Frappe Email Account / Communication + `frappe.sendmail` |
| **Education data source** | Walnut Education app (Student, Guardian, Program Enrollment, Fees, Payment Schedule) |

---

## 3. System Architecture

```
                         ┌──────────────────────────────────────────────┐
                         │                  Browser                      │
   /unity-helpdesk  ───► │  Unity Helpdesk SPA (Vue 3 + Vite)  ◄── ours │
   /helpdesk        ───► │  Stock Helpdesk SPA (Vue 3 + Vite)           │
                         └───────────────┬──────────────────────────────┘
                                         │ POST /api/method/...  (CSRF + session cookie)
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

The Unity SPA talks almost exclusively to `helpdesk.api.unity_helpdesk` and
`helpdesk.api.unity_helpdesk_ext`. These call into the standard doctypes underneath.

---

## 4. Build & Serving Model

Two independent Vite builds, each served by a Frappe website route declared in
`helpdesk/hooks.py`:

| Route | Controller | Template | Compiled assets |
|-------|-----------|----------|-----------------|
| `/helpdesk`, `/helpdesk/<path>` | `www/helpdesk/index.py` | `www/helpdesk/index.html` | `/assets/helpdesk/desk/` |
| `/unity-helpdesk`, `/unity-helpdesk/<path>` | `www/unity_helpdesk/index.py` | `www/unity_helpdesk/index.html` | `/assets/helpdesk/unity_helpdesk/` |

- The stock `/helpdesk` controller checks `HD Settings.helpdesk_ui`; when it equals
  `"Unity Helpdesk"` it raises `frappe.Redirect` to `/unity-helpdesk`.
- Both controllers inject `csrf_token`, `frappe_version`, `helpdesk_version`, `site_name`
  into `window.*` and set `no_cache = 1`.

**Unity SPA build** (`unity_helpdesk/vite.config.js`, `unity_helpdesk/package.json`):

- `outDir: ../helpdesk/public/unity_helpdesk`
- `build`: `vite build --base=/assets/helpdesk/unity_helpdesk/ && yarn copy-html-entry`
- `copy-html-entry`: copies the built `index.html` into `www/unity_helpdesk/index.html`
- `dev`: `vite --host 0.0.0.0`
- `build` target `es2021`, source maps enabled.

> **Operational note:** the committed `public/unity_helpdesk` bundle must be rebuilt and
> committed whenever SPA source changes — the served SPA is the *built* bundle, not the
> source.

---

## 5. Front-End (Unity SPA)

Source root: `unity_helpdesk/src/`

### 5.1 Bootstrap & routing (`main.js`)

Vue Router mounted at base `/unity-helpdesk/`:

| Path | Component | Props |
|------|-----------|-------|
| `/` | redirect | → `/tickets/my` |
| `/dashboard` | `DashboardView` | — |
| `/tickets/my` | `TicketsView` | `view: "my"` |
| `/tickets/all` | `TicketsView` | `view: "all"` |
| `/tickets/:ticketId` | `TicketDetailView` | route params |
| `/settings` | `ProfileView` | — |

### 5.2 App shell (`App.vue`)

- Sidebar nav (role-gated), topbar with page title/subtitle (emitted by child views).
- **Create Ticket** composer modal and **Bulk Email** composer modal live here.
- Loads session once and `provide()`s `unitySession`, `unityAgents`, `unityTicketTypes`;
  child views `inject()` them to avoid redundant calls.
- On mount: `getUnityProfile()` → `helpdesk.api.unity_helpdesk.get_profile`; on
  `AuthRedirectError` → `/login?redirect-to=<path>`.

### 5.3 HTTP client (`api.js`)

- `call(method, params, options)` — `POST /api/method/<method>`, JSON body, CSRF header.
  - On CSRF error: fetches a fresh token via
    `helpdesk.api.unity_helpdesk.get_csrf_token` and **retries once**.
  - On auth failure (401 / "login to access"): `redirectToLogin()`.
- `callWithRetry()` — exponential backoff (1s / 3s / 7s) for idempotent reads.
- `uploadAttachment(file, doctype, docname)` — Frappe native `upload_file` (`is_private=1`).
- `sanitize(html)` — DOMPurify; applied to all server-rendered HTML before display.

### 5.4 View → endpoint map (verified against `api.js` / `*.vue`)

| View | Endpoints |
|------|-----------|
| **DashboardView** | `unity_helpdesk.get_dashboard_summary`, `unity_helpdesk.get_agents` |
| **TicketsView** | `unity_helpdesk.get_tickets_page`, `unity_helpdesk.get_tickets_summary`, `unity_helpdesk.get_ticket_suggestions`, `unity_helpdesk.bulk_update_tickets`, `unity_helpdesk.update_column_preferences`, `unity_helpdesk.get_agents`, `unity_helpdesk.get_ticket_types` |
| **TicketDetailView** | `unity_helpdesk_ext.get_ticket_detail`, `unity_helpdesk.get_student_context`, `unity_helpdesk_ext.reply`, `unity_helpdesk_ext.add_comment`, `unity_helpdesk_ext.update_ticket`, `unity_helpdesk.get_accessible_ticket_summaries`, `unity_helpdesk.get_bulk_emails_received_by` |
| **ProfileView** | `unity_helpdesk.get_profile`, `unity_helpdesk.update_unity_settings`, `unity_helpdesk.list_ticket_types_with_keywords`, `unity_helpdesk.create_ticket_type`, `unity_helpdesk.update_ticket_type_keywords`, `unity_helpdesk.update_ticket_type_color`, `helpdesk.api.reply_templates.*` |
| **UsersView** | `unity_helpdesk.get_agents`, `unity_helpdesk.get_agent_candidates`, `unity_helpdesk.create_agent` |
| **App shell** | `unity_helpdesk_ext.create_ticket`, `unity_helpdesk_ext.bulk_send_email`, `unity_helpdesk_ext.get_bulk_email_sample_csv`, `unity_helpdesk.search_contacts`, `unity_helpdesk.get_student_guardian_emails`, `unity_helpdesk.get_csrf_token` |

> **Parallel loading:** `TicketDetailView` fetches `get_ticket_detail` and
> `get_student_context` **in parallel**, so the heavier education join never blocks the
> first paint of the ticket thread.

---

## 6. Back-End (Unity API Layer)

### 6.1 `helpdesk/api/unity_helpdesk.py` (core engine, ~4,100 lines)

Primary read/optimise engine. Key whitelisted endpoints:

- **Listing / search:** `get_tickets_page`, `get_tickets_summary`,
  `get_tickets` (back-compat wrapper combining both), `get_ticket_suggestions`.
- **Detail / context:** `get_ticket_detail`, `get_student_context`,
  `get_accessible_ticket_summaries`, `get_bulk_emails_received_by`.
- **Mutations:** `create_ticket`, `update_ticket`, `reply`,
  `bulk_update_tickets` (max 500 names; fields: status/priority/ticket_type/_assign/agent_group).
- **Dashboard:** `get_dashboard_summary` (range / from_date / to_date / agent →
  cards + ticket-type breakdown + status trend).
- **Admin / lookups:** `get_profile`, `get_agents`, `get_agent_candidates`, `create_agent`,
  `get_ticket_types`, `create_ticket_type`, `list_ticket_types_with_keywords`,
  `update_ticket_type_color`, `update_ticket_type_keywords`, `search_contacts`,
  `get_student_guardian_emails`, `update_column_preferences`, `update_unity_settings`,
  `get_csrf_token`, `enqueue_auto_assign_ticket_types`.
- **Diagnostics:** `backfill_ticket_message_search_fields`,
  `diagnose_ticket_thread_and_search`.

Internal mechanics:

- **Recognised custom fields** (`UNITY_TICKET_FIELDS`): `custom_is_on_hold`,
  `custom_hold_from/to/reason`, `custom_is_bulk_email`, `custom_via_unity_portal`,
  `custom_bulk_email_recipients`, `custom_replied_to_ticket`, plus the search/denormalised
  fields.
- **Per-request cache** `frappe.local._unity_request_cache` (cleared per request): assigned
  ticket names, resolved search/pagination context, guardian-emails-by-student.
- **Ticket decoration** bulk-fetches `User` rows to compute assignee info — avoids N+1.
- **Assignment fast path** reads the indexed **ToDo** table (owner + reference_type +
  reference_name + status) instead of `_assign LIKE '%user%'` full-table scans.

### 6.2 `helpdesk/api/unity_helpdesk_ext.py` (CRUD + bulk email, ~800 lines)

Loaded fresh on bench restart to avoid stale in-memory state. Endpoints:
`get_ticket_detail`, `create_ticket`, `update_ticket`, `reply`, `add_comment`,
`bulk_send_email`, `get_bulk_email_sample_csv`. (See §10 for the bulk-email pattern.)

### 6.3 `helpdesk/api/unity_perf.py` (diagnostics, ~560 lines)

Operator tooling (not user-facing): `run_filter_benchmark`, `run_endpoint_benchmark`,
`print_search_diagnostic`, `diagnose_guardian_lookup`, `print_backfill_status`.

### 6.4 `helpdesk/search.py` (RediSearch, ~230 lines)

`Search` / `HelpdeskSearch` wrap a Redis full-text index (fields: `name` w5, `subject` w2,
`description` w1, `team` tag, `creation`/`modified` sortable). Index lifecycle via
`build_index`, `build_index_in_background` (`after_migrate`), `build_index_if_not_exists`
(scheduler `all`). This is the secondary RediSearch path; the primary scale search is the
MariaDB LIKE + FULLTEXT path described in §9.

---

## 7. Data Model & Doctypes

Standard Helpdesk doctypes the Unity layer builds on:

- **HD Ticket** — central object. Status `Open → Replied → Resolved → Closed`. Links:
  `contact`, `customer`, `ticket_type`, `priority`, `agent_group` (HD Team), `sla`.
  SLA fields: `response_by`, `resolution_by`, `first_responded_on`, `resolution_date`,
  `resolution_time`, `agreement_status`. Assignment via Frappe `_assign` (JSON).
- **HD Team / HD Agent** — a team owns an auto-created **Assignment Rule**
  (`status=='Open' and agent_group=='<team>'`). Agents are added/removed from rotation on
  `is_active`/`groups` change.
- **HD Ticket Type** — `keywords` (word-boundary auto-classification), default `priority`,
  plus Unity `custom_color`.
- **HD Ticket Priority** — `integer_value` (lower = higher priority).
- **HD Ticket Comment** — internal notes. **HD Ticket Activity** — audit trail.
- **HD Service Level Agreement** (+ Priority / Service Day / Holiday children) — response &
  resolution targets computed against working hours, with pause/hold support.
- **Communication** — Frappe email/message records; inbound replies re-open tickets.
- **HD Canned Response** — extended by Unity with `category`, `language`, `subject_template`,
  `is_active`.

---

## 8. Custom Fields & Database Indexes

### 8.1 Custom fields on `HD Ticket`

| Field | Type | Purpose |
|-------|------|---------|
| `custom_via_unity_portal` | Check | Ticket originated in SPA/portal (vs inbound email) |
| `custom_is_bulk_email` | Check | Marks a bulk-email **audit** ticket |
| `custom_bulk_email_recipients` | Long Text | Denormalised recipient list of a bulk send |
| `custom_replied_to_ticket` | Link → HD Ticket | Links a reply back to the bulk/portal source |
| `custom_is_on_hold` | Check | On-hold workflow flag |
| `custom_hold_from` / `custom_hold_to` | Date | Hold window |
| `custom_hold_reason` | Text | Hold reason |
| `custom_search_student_names` | Data | Denormalised student names (search) |
| `custom_search_student_refs` | Data | Denormalised student reference numbers (search) |
| `custom_search_guardian_emails` | Data | Denormalised guardian emails (search) |
| `custom_primary_message_html` / `custom_primary_message_text` | Text | Cached first message (fast render) |
| `custom_search_message_body` | Small Text | Searchable head+tail of all messages |

Other doctypes:

- **HD Ticket Type:** `custom_color` (Color).
- **HD Canned Response:** `category` (Link), `language` (Select EN/HI/MR),
  `subject_template` (Data), `is_active` (Check).

### 8.2 Indexes on `HD Ticket`

| Index | Columns | Serves |
|-------|---------|--------|
| `raised_by_unity_idx` | `(raised_by)` | guardian-family search + backfill |
| `creation_unity_idx` | `(creation)` | date-range filters |
| `modified_unity_idx` | `(modified)` | list `ORDER BY modified DESC` (no filesort) |
| `status_modified_unity_idx` | `(status, modified)` | status filter + ordered list / counts |
| `on_hold_modified_unity_idx` | `(custom_is_on_hold, modified)` | on-hold KPI + filter |
| `contact_unity_idx` | `(contact)` | permission-query OR branch |
| `owner_unity_idx` | `(owner)` | permission-query OR branch (index_merge) |
| `search_body_ft_idx` | FULLTEXT `(custom_search_message_body, subject, custom_search_student_names, custom_search_student_refs, custom_search_guardian_emails)` | long-query natural-language search |

---

## 9. Search — Deep Dive

Search is the single most engineered feature. It must resolve, against ~90k tickets, queries
that may be a ticket ID, a student name, a reference number, a parent email, a subject
fragment, or an entire pasted email paragraph — and rank them sensibly.

### 9.1 Pipeline

```
query
  │
  ├─ 1. Exact ticket-ID match?        ─► return that ticket immediately
  │
  ├─ 2. Looks like an email address?  ─► expand to the guardian's whole family
  │        (_expand_email_to_family_search_terms): collect all student IDs / refs /
  │         names / emails for that guardian so a parent email finds every child's tickets
  │
  ├─ 3. "Long" query (≥4 tokens OR >60 chars) AND FULLTEXT index exists?
  │        ─► MariaDB FULLTEXT candidates (_fulltext_candidates) over search_body_ft_idx
  │           (index availability is cached so it isn't re-checked every request)
  │
  └─ 4. Otherwise ─► multi-token AND-of-OR LIKE across:
           name, subject, raised_by, custom_search_student_names,
           custom_search_student_refs, custom_search_guardian_emails,
           custom_search_message_body
  │
  ▼
ranking (_rank_ticket_document) ─► pagination (_compute_tickets_page)
                                 ─► bulk decoration (no N+1) ─► response
```

### 9.2 7-tier ranking

Candidate rows are normalised into search documents and scored:

| Tier | Match | Tie-break |
|------|-------|-----------|
| 0 | Exact ticket ID | — |
| 1 | Student reference-number match | — |
| 2 | Direct email match **or** family-aware email match (via guardian expansion) | — |
| 3 | Prefix match on ticket ID / student ref / email | shortest prefix wins |
| 4 | All query tokens present in a student name | shorter name wins |
| 5 | Query substring in subject/description | subject ranks above description |
| 6 | All tokens present in the message body | — |

### 9.3 What makes long-query search possible

The `custom_search_message_body` field is a **denormalised, size-budgeted** concatenation of
a ticket's messages (a head + tail slice, so very long threads still fit), kept current by
doc-event hooks (see §13) on `Communication` and `HD Ticket Comment`. The FULLTEXT index
`search_body_ft_idx` covers it plus subject and the student/guardian search fields, enabling
natural-language scoring for long pasted text — which plain LIKE handles poorly.

### 9.4 As-you-type suggestions

`get_ticket_suggestions(search, view, limit≤8)` returns lightweight rows for the dropdown.
The SPA also keeps recent searches in `localStorage` and binds `Ctrl+K` to focus search.

### 9.5 Diagnostics

`unity_perf.print_search_diagnostic(ticket_name, query)` and
`unity_helpdesk.diagnose_ticket_thread_and_search(name, text)` show normalised tokens and a
per-field presence matrix — i.e. *why* a query did or didn't match a given ticket.

---

## 10. Bulk Email — Implementation

**Endpoint:** `unity_helpdesk_ext.bulk_send_email(subject, message, recipients, cc, bcc, attachments, ticket_type)`

**The "asymmetric send" / audit pattern:**

```
SPA bulk_send_email
  │ validate: RECIPIENT_HARD_CAP=1000, TOTAL_ADDRESS_HARD_CAP=1500
  │ parse address lists (comma / semicolon / JSON) → (valid_lowercased, invalid_count)
  ▼
enqueue _bulk_send_email_job (queue: "short")
  │
  ├─ create ONE audit HD Ticket:
  │     custom_via_unity_portal = 1
  │     custom_is_bulk_email    = 1
  │     custom_bulk_email_recipients = denormalised recipient list
  │     description = collapsible <details> audit HTML (recipients / CC / BCC / message)
  │
  ├─ create ONE Communication linked to the audit ticket
  │
  └─ ONE frappe.sendmail(expose_recipients=None):
        To  = real recipients
        CC  = cc_list
        BCC = _default_bulk_recipients()   ← the audit mailbox sees the full send
     → recipients do NOT see each other; the audit mailbox DOES receive the message
```

Why this matters: it is **one** outbound email to many addresses (not thousands of
individual sends), it hides recipients from each other, and it still produces a permanent,
queryable audit trail. Later, when a parent **replies** to a bulk email, the reply-link hook
(§13) sets `custom_replied_to_ticket` on the reply ticket, pointing back to the audit ticket.

Supporting endpoints: `get_bulk_email_sample_csv` (CSV template for recipient import) and
`get_student_guardian_emails` (expand a list of student emails → guardian emails, returned
with a diagnostic of matched/unmatched).

---

## 11. Student / Guardian Context — Implementation

**Endpoint:** `unity_helpdesk.get_student_context(ticket_name)`

```
ticket.raised_by
  ├─ Student.student_email_id == raised_by        ─► student found
  └─ else Guardian.email_address == raised_by     ─► guardian found → that guardian's students
        for each student:
          • siblings (other students of the same guardian)
          • current Program Enrollment (class / academic year)
          • all Fees linked to the student
          • payment schedule
          • guardians (name / mobile / alt-mobile / email)
          • school location
  ─► returned to the SPA (fetched in parallel with get_ticket_detail)
```

Resolved context is also surfaced through the denormalised search fields
(`custom_search_student_names/refs/guardian_emails`) so it is searchable, and cached message
text (`custom_primary_message_*`) so the thread renders fast. Legacy fields
(`custom_list_of_student`, `custom_all_fees_details_of_students`, `custom_payment_schedule`,
`custom_student_remark`, `custom_previous_ticket_details`) remain in the schema for back-compat.

Diagnostic: `unity_perf.diagnose_guardian_lookup(emails)` walks the lookup chain step by step
(Student.student_email_id/user → Guardian.email_address → Student Guardian links).

---

## 12. Dashboard & Caching

**Endpoint:** `unity_helpdesk.get_dashboard_summary(range, from_date, to_date, agent)`

- `range` ∈ {today, week, month, quarter, year, custom}; optional `agent` filter.
- Returns: `cards` (Total / Created / Pending / On Hold / Resolved / Closed),
  `ticket_type_breakdown` (donut), and `status_trend` (stacked bar, bucketed by
  day/week/month).
- **Count strategy:** the unfiltered path issues narrow per-status `COUNT` queries that ride
  the covering indexes; the filtered path uses a single `SUM(CASE …)` aggregate.

**Caching layers (Redis):**

| Cache | Key | TTL | Notes |
|-------|-----|-----|-------|
| Dashboard summary cards | `unity:tickets:summary:{md5(user+view+filters)}` | 30s | empty-search path only; SPA shows "…" placeholder during the gap |
| Communication text | `helpdesk_comm_text:{ticket_name}` | 5m | avoids re-reading the thread on repeated detail/search reads |
| FULLTEXT index availability | (cached flag) | — | so search never re-probes for the index |

---

## 13. Hooks

Defined in `helpdesk/hooks.py`.

**Install / migrate:**
`before_install` / `after_install` → `setup.install`; `after_migrate` →
`search.build_index_in_background`.

**Scheduler:**
`all` → `search.build_index_if_not_exists`; `daily` →
`unity_helpdesk.send_open_ticket_reminders`.

**Website routes:** `/helpdesk` and `/unity-helpdesk` (see §4).

**doc_events:**

| Doctype | Event | Handler → effect |
|---------|-------|------------------|
| Contact | `before_insert` | auto-link to `HD Customer` by email domain |
| Assignment Rule | `on_trash` | block deletion of the last HD Ticket assignment rule |
| HD Ticket | `after_insert` | populate student search fields + refresh message-search index |
| Communication | `after_insert` | refresh message-search index; set `custom_replied_to_ticket` on bulk-email/portal replies |
| Communication | `on_update` | refresh message-search index |
| HD Ticket Comment | `after_insert` / `on_update` | refresh message-search index |

> All search-index hooks are wrapped in a defensive `_safe()` — they **log but never raise**,
> so ticket/email ingestion can never break because of an index error.

**Permissions:** `has_permission` + `permission_query_conditions` for `HD Ticket`
(see §16). `ignore_links_on_delete` includes `HD Notification`.

---

## 14. Patches, Backfills & Install

### 14.1 Patch categories (`patches.txt`)

**Custom fields:** `unity_ticket_message_search_fields`,
`unity_helpdesk_student_search_fields`, `unity_helpdesk_portal_origin_fields`,
`unity_reply_link_field`, `unity_bulk_email_recipients_field`,
`unity_canned_response_extension`, `unity_ticket_type_color_field`.

**Indexes:** `unity_ticket_creation_index`, `unity_ticket_list_indexes`,
`unity_raised_by_index`, `unity_owner_index`, `unity_ticket_search_fulltext`.

**Backfills (long queue, 50-row batches, 0.2s sleep, idempotent — short-circuit when
complete):** `unity_ticket_message_search_rebuild`, `unity_bulk_email_recipients_backfill`,
`unity_bulk_email_default_recipients_backfill`, the student-search backfill, plus
`unity_post_migrate_warmup` (pre-warms the InnoDB buffer pool after sweeps).

### 14.2 Fresh-install custom-field creation (critical)

Fresh installs mark patches as *applied without running them*. Therefore schema-affecting
patches must **also** be invoked directly from `ensure_unity_custom_fields()` in
`setup/install.py` (it calls `.execute()` on the schema patches; they are idempotent and the
backfill loops are no-ops on an empty site).

> **Rule for contributors:** any new schema-affecting Unity patch must be wired into
> `ensure_unity_custom_fields()`, or fresh installs will be missing those custom fields.

### 14.3 Index-deduplication strategy

Because patches already recorded in `tabPatch Log` on existing sites never re-run, new
indexes are split into *new* patch files (e.g. `unity_raised_by_index` and `unity_owner_index`
are separate from the patches that originally introduced them) so they apply on production
sites that already logged the earlier patch.

---

## 15. Performance Engineering

1. **Covering/composite indexes** for list pages and KPI counts (no filesort; index-only
   counts).
2. **FULLTEXT-first for long queries**, with cached index availability so it is never
   re-probed.
3. **ToDo-based assignment lookups** replace `_assign LIKE` full scans.
4. **Redis dashboard cache** (30s) with "…" placeholders to avoid layout jumps during the gap.
5. **Communication-text cache** (5m) so repeated reads don't re-parse the thread.
6. **Parallel student-context fetch** so the education join doesn't block ticket render.
7. **Bulk fast path** — raw `db.set_value()` for priority/ticket_type/agent_group in bulk
   updates, skipping the heavy `on_update` search rebuild.
8. **Deferred, chunked backfills** keep `bench migrate` fast; live hooks populate new rows
   inline while history backfills.
9. **Post-migrate warmup** touches hot index pages to load them into the InnoDB buffer pool
   so the first user request after a sweep isn't a cold read.

---

## 16. Security & Permissions

- **Transport:** all SPA calls are authenticated by session cookie + CSRF token; the client
  auto-refreshes CSRF and retries once.
- **Row-level access (`HD Ticket`):** `has_permission` allows the ticket contact,
  `raised_by`, owner, any agent, or a user linked to the ticket's customer.
  `permission_query_conditions` restricts non-agents to their own tickets (contact /
  raised_by / owner / customer) using an index_merge union across `contact_unity_idx`,
  `owner_unity_idx`, etc.
- **Role gating:** capabilities (`can_view_my_tickets`, `can_view_all_tickets`,
  `can_manage_agents`, `can_view_dashboard`, `can_manage_unity_settings`) are returned by
  `get_profile` and gate both API behaviour and SPA navigation.
- **Output safety:** all rendered HTML passes through DOMPurify on the client.
- **Bulk-email caps:** hard limits (1000 recipients / 1500 total addresses) prevent runaway
  sends.

---

## 17. Diagnostics & Operations

| Tool | Purpose |
|------|---------|
| `unity_perf.run_filter_benchmark()` | time the list-filter queries + `EXPLAIN` |
| `unity_perf.run_endpoint_benchmark(view, page_length)` | benchmark list/summary endpoints |
| `unity_perf.print_search_diagnostic(ticket, query)` | per-field match matrix for a query |
| `unity_perf.diagnose_guardian_lookup(emails)` | step-through of the student/guardian resolver |
| `unity_perf.print_backfill_status()` | progress + RQ state of the three backfill jobs |
| `unity_helpdesk.diagnose_ticket_thread_and_search(name, text)` | thread counts + index contents |
| `unity_helpdesk.backfill_ticket_message_search_fields(names, limit)` | manual re-index of message search fields |

---

## 18. File / Module Map

| Area | Path |
|------|------|
| Unity core API | `helpdesk/api/unity_helpdesk.py` |
| Unity CRUD + bulk email | `helpdesk/api/unity_helpdesk_ext.py` |
| Performance diagnostics | `helpdesk/api/unity_perf.py` |
| RediSearch wrapper | `helpdesk/search.py` |
| Hooks | `helpdesk/hooks.py` |
| Patches | `helpdesk/patches.txt`, `helpdesk/patches/` |
| Install / fresh-install custom fields | `helpdesk/setup/install.py` (`ensure_unity_custom_fields`) |
| Doc-event handlers | `helpdesk/helpdesk/hooks/{contact,search_index,reply_link}.py` |
| Standard ticket model | `helpdesk/helpdesk/doctype/hd_ticket/hd_ticket.py` |
| SLA engine | `helpdesk/helpdesk/doctype/hd_service_level_agreement/` |
| Unity SPA source | `unity_helpdesk/src/` (`main.js`, `App.vue`, `api.js`, `views/`) |
| Unity SPA build config | `unity_helpdesk/vite.config.js`, `unity_helpdesk/package.json` |
| Unity SPA web route | `helpdesk/www/unity_helpdesk/` |
| Compiled SPA assets | `helpdesk/public/unity_helpdesk/` |
| Topic docs | `docs/{bulk-email,configuration,education-integration,performance}.md` |

---

*Unity Helpdesk fork — `UnityAppSuite/helpdesk`, branch `rechecking`.*
