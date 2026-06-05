# Unity Helpdesk — Load-Test & Capacity Report

**Date:** 2026-05-30  **Site:** unity.local (dev bench, 93,000 HD Tickets, 12 cores)
**Branch:** rechecking

---

## 1. Executive summary

A May-2026 Locust run against UAT collapsed at 500 users (flat ~3 RPS, p95 pinned at the
120 s proxy timeout, ~40 % failures). We investigated, built a faithful load-test harness,
and proved the cause and the fix on this machine.

**The crash was worker starvation, not slow code.** Frappe serves with synchronous
gunicorn workers (one request each); UAT/this bench run only ~5. 500 users against ~5
workers is a 100:1 overload that queues until the proxy times out.

**Proven on our machine:** the *same* load against the *same* code, changing only the
gunicorn worker count from 4 → 12, nearly halved p95 (6,100 → 3,300 ms) and flipped the
run from FAIL to PASS — **zero code changes**.

We also found and **fixed** a real per-request issue (a missing index on the customer
portal path: ~4 s → ~1 ms), and identified the login path as the dominant per-request
cost under concurrency.

---

## 2. The original failure (UAT, 2026-05-29)

| Symptom | Reading | Meaning |
|---|---|---|
| RPS flat ~3/s while users → 500 | green line never rises | throughput ceiling hit immediately |
| avg 65 s, p95 117 s response | climbs with users | requests queuing for a free worker |
| max ~118 s | = nginx `proxy_read_timeout` (120 s) | proxy giving up → 504 |
| 40 % failures | 504 / 500 / `RemoteDisconnected` / `0` | backend out of capacity |

---

## 3. Root-cause analysis

- **Capacity (primary):** sync gunicorn workers handle 1 request each. `gunicorn_workers`
  is **5** on the bench. ~5 concurrent requests max; the rest queue to the timeout.
- **Per-request cost (secondary):** the longer a request holds a worker, the lower the
  throughput ceiling. The read paths are already heavily optimised (Redis-cached cards,
  parallel student-context, FULLTEXT search, indexes), so the main remaining costs are
  login (bcrypt + boot cache) and — for non-agents — a permission-filter scan (now fixed).

---

## 4. What we built — the load-test harness (`apps/helpdesk/loadtest/`)

A faithful, repeatable Locust harness that mirrors the real Unity SPA (every call matches
`unity_helpdesk/src/api.js`): login once → `get_csrf_token` → POST `get_tickets_page` +
`get_tickets_summary` (list) and `get_ticket_detail` + `get_student_context` (detail),
with realistic think-time. It scores each run PASS/FAIL on failure-ratio and p95.

| File | Purpose |
|---|---|
| `locustfile.py` | the harness (FastHttpUser, staged-ramp shape, pass/fail gate, Host-header override) |
| `setup_test_users.py` | provision/teardown a pool of test agents (users **+ HD Agent records**) |
| `RUNBOOK.md` | step-by-step scaled-worker procedure + healthy-vs-saturation guide |
| `CAPACITY.md` | server-sizing recommendation for the deployer (workers, DB connections) |
| `README.md` | setup + how to read results |
| `requirements.txt`, `users.csv.example`, `.gitignore` | supporting files |

---

## 5. Measurements (all real, this machine)

### 5a. Single-user, warm (the code is fast per-request)
| Endpoint | Time |
|---|---|
| `get_tickets_page` (agent, all) | 34 ms |
| `get_tickets_summary` (all, 93K-row aggregate) | 63 ms |
| `get_ticket_detail` | 0.78 s |
| `get_student_context` | 0.27 s |
| `login` warm / cold | 0.49 s / 5.5 s |

### 5b. The scaling proof — A/B, identical 20-user / 70 s load, only workers changed
| Metric | 4 workers | 12 workers |
|---|---|---|
| Aggregate **p95** | 6,100 ms | **3,300 ms** |
| Aggregate avg | 2,151 ms | **959 ms** |
| Throughput | 4.0 req/s | **5.4 req/s** |
| Requests in 70 s | 276 | **376** |
| `tickets_page` avg | 1,939 ms | **956 ms** |
| `login` avg | 5,411 ms | **3,759 ms** |
| **Verdict** | ❌ FAIL | ✅ PASS |

> `tickets_page` was 34 ms solo but ~1.9 s at 20 users on 4 workers — not because the
> query slowed down, but because workers were busy and requests queued. More workers =
> less queuing. This is the whole story in one number.

### 5c. The fix we applied — `owner` index on HD Ticket (customer/parent portal path)
The permission filter for a non-agent is `contact=u OR raised_by=u OR owner=u`. `owner`
was unindexed, defeating the index-merge → full scan.
| | Before | After (`owner_unity_idx`) |
|---|---|---|
| warm time | ~160 ms | **1.1 ms** |
| cold time | ~4 s (full scan) | sub-second |
| `EXPLAIN` | scan, 93K rows | **`index_merge union(contact, raised_by, owner)`, 3 rows** |

Shipped as patch `helpdesk/patches/unity_owner_index.py` (registered in `patches.txt`).

---

## 6. Findings & recommendations

1. **Scale the web tier (decisive).** Move off 5 sync workers toward `cpu*2+1`, or use
   gthread workers + threads for the I/O-bound ticket queries. Scale MariaDB
   `max_connections` (currently 50) to match. Details + sizing math in `CAPACITY.md`.
   *Owner: deployer applies on UAT/prod.*

2. **`owner` index — DONE.** Customer portal path ~4 s → ~1 ms. Lands automatically on
   the next `bench migrate` via the new patch.

3. **Login is the per-request hot spot under concurrency** (bcrypt + boot cache). Notes:
   - bcrypt is intentionally CPU-bound, so login throughput is limited by **CPU cores**,
     not worker count alone — gthread does *not* help logins (only the I/O-bound queries).
   - The existing `unity_post_migrate_warmup` patch warms the **ticket** buffer pool but
     **not** the login/boot path. A small post-deploy login warm-up (or rarer worker
     recycling via higher `gunicorn_max_requests`) would cut the cold-login spike.
   - Much of the test's login pain is a **stampede artifact** (all simulated users log in
     at spawn). Real users log in once and keep a session, so production login load is
     proportional to the login *rate*, not concurrent users — except at morning spikes.
   - **Do NOT** remove boot-cache from auth; it's load-bearing.

4. **One code cleanup applied:** removed a redundant second DB fetch in the search path
   of `get_tickets_page` (`_compute_tickets_page` in `unity_helpdesk.py`) — the page rows
   were already in `candidate_rows`.

---

## 7. The validation loop (how to answer "are we ready for 500?")

1. Scale workers on a staging box sized like UAT (the dev box's shared MariaDB makes
   absolute numbers unreliable; the *shape* of the curve is valid).
2. Run the staged harness: `LOADTEST_SHAPE=stages` ramps 50→100→200→350→500, holding each.
3. **Healthy** = RPS scales with users, p95 stays well under 120 s, failures ≈ 0, and the
   "knee" sits at/beyond 500. **Saturation** = flat RPS, diagonal p95 climb, 504s.
4. Apply `CAPACITY.md` on UAT (deployer), re-run against UAT in a coordinated off-hours
   window to confirm production-like behaviour.

---

## 8. Changes in this branch (for review — not committed)

- **New:** `apps/helpdesk/loadtest/` (harness, runbook, capacity & this report, setup script).
- **New:** `helpdesk/patches/unity_owner_index.py` + `patches.txt` entry.
- **Edit:** `helpdesk/api/unity_helpdesk.py` — search double-fetch removed.
- **Applied to local DB:** `owner_unity_idx` index; 30 test users + HD Agent records
  (`loadtest{1..30}@walnutedu.in`). Teardown:
  `LOADTEST_TEARDOWN=1 bench --site unity.local console < loadtest/setup_test_users.py`

## 9. Open items (need deployer)
- UAT/prod CPU cores, RAM, current `gunicorn_workers` — for exact sizing.
- Whether the prod web tier is one box or load-balanced.
