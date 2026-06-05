# Load-Test Runbook — answering "can we survive 500+ users?"

This is the step-by-step procedure to run the scaled-worker test and read the result.
It's grounded in what we actually measured on the dev bench (93,000 HD Tickets) on
2026-05-30 — those numbers are the baseline you're comparing against.

---

## 0. What we already learned (baseline measurements)

Measured directly, single user, warm cache:

| Endpoint | Time | Notes |
|---|---|---|
| `login` (cold, first ever) | **5.5 s** | bcrypt + boot-cache build — paid once, then warm |
| `login` (warm) | 0.49 s | bcrypt is intentionally CPU-bound |
| `get_tickets_page` (agent, all) | **34 ms** | fast — the list page is well-optimised |
| `get_tickets_summary` (all) | 63 ms | the 93K-row dashboard aggregate, Redis-cached 30 s |
| `get_ticket_detail` | 0.78 s | |
| `get_student_context` | 0.27 s | the Education-app joins |

### Demonstrated scaling A/B (gunicorn, port 8001, 20 users, 70 s, same MariaDB/Redis)

We ran the identical load against the same app with only the gunicorn worker count
changed — the cleanest possible proof that capacity is the lever:

| Metric | **4 workers** | **12 workers** |
|---|---|---|
| Aggregate p95 | 6,100 ms | **3,300 ms** |
| Aggregate avg | 2,151 ms | **959 ms** |
| Throughput | 4.0 req/s | **5.4 req/s** |
| Requests in 70 s | 276 | **376** |
| `tickets_page` avg | 1,939 ms | **956 ms** |
| `login` avg | 5,411 ms | **3,759 ms** |
| Harness verdict | ❌ FAIL (p95 > 5 s) | ✅ PASS |

Tripling workers nearly halved p95 and flipped FAIL→PASS **with zero code changes**.
(Both runs shared the dev session's MariaDB/Redis, so absolute numbers are conservative;
the *shape* — more workers = lower latency, higher throughput — is the point.) `login`
stays the top cost even at 12 workers because 20 users logging in at once stampede the
bcrypt + cold-boot path — see finding #1.

Two findings worth acting on (details in §6):
1. **Login is the per-request hot spot under concurrency.** At just **8 concurrent
   users on the single-process dev server, login spiked to ~23 s** (vs 0.49 s warm) —
   logins serialised and queued. This is the worker-starvation effect in miniature.
2. **The customer/non-agent portal path is slow-ish** (~4 s cold) because the HD Ticket
   permission filter ORs across `contact / raised_by / owner`, and **`owner` has no
   index**, so it can't index-merge.

---

## 1. ⚠️ The single most important caveat: don't test the dev server

`bench serve` / `bench start` runs a **single-process Werkzeug dev server**. It has
essentially no concurrency, so load-testing it tells you about the dev server, *not*
production. **Always run the load test against a gunicorn-backed server** (production-mode
bench, a staging box, or the local gunicorn approximation below).

### Local gunicorn approximation (on a different port, leaves your dev server alone)

```bash
cd /home/ankit/frappe-bench/sites
# N workers — start at 5 to reproduce the failure, then raise to compare
../env/bin/gunicorn -b 127.0.0.1:8001 -w 5 -t 120 --worker-class sync \
    frappe.app:application
```
Point the test at `--host http://127.0.0.1:8001`. Re-run with `-w 25` (or
`--worker-class gthread --threads 6`) to see the difference scaling makes. This is the
cleanest way to *prove* the capacity thesis on one machine.

> Note: even this shares one MariaDB / Redis with your dev session. For trustworthy
> absolute numbers, run on a staging box sized like UAT. But the *shape* of the
> curve (does RPS scale or go flat?) is valid here.

---

## 2. Prepare

```bash
cd /home/ankit/frappe-bench/apps/helpdesk

# a) Test-account pool (LOCAL site only — creates real login-capable agents)
cat loadtest/setup_test_users.py | bench --site unity.local console
#    -> 30 users loadtest1..30@walnutedu.in, each a Helpdesk Admin + HD Agent

# b) users.csv (git-ignored)
printf 'usr,pwd\n' > loadtest/users.csv
for i in $(seq 1 30); do printf 'loadtest%s@walnutedu.in,Loadtest@123\n' "$i" >> loadtest/users.csv; done

# c) locust venv (once)
python -m venv loadtest/.venv && loadtest/.venv/bin/pip install -r loadtest/requirements.txt
```

> The pool has 30 accounts; Locust cycles through them, so you can run 500 users on 30
> accounts. For a more realistic spread (less row-lock/cache sharing), bump
> `LOADTEST_N=200` when running `setup_test_users.py` and regenerate `users.csv`.

---

## 3. Run

### A. Baseline — reproduce the collapse (5 workers)

```bash
cd /home/ankit/frappe-bench/sites && ../env/bin/gunicorn -b 127.0.0.1:8001 -w 5 -t 120 frappe.app:application &
cd /home/ankit/frappe-bench/apps/helpdesk
LOADTEST_SHAPE=stages loadtest/.venv/bin/locust -f loadtest/locustfile.py --headless \
    --host http://127.0.0.1:8001 --html loadtest/baseline_5workers.html
```
Expect: RPS goes flat, p95 climbs toward the timeout, failures appear as users rise.

### B. Scaled — the real test (25 workers, or gthread)

```bash
cd /home/ankit/frappe-bench/sites && ../env/bin/gunicorn -b 127.0.0.1:8001 -w 25 -t 120 frappe.app:application &
cd /home/ankit/frappe-bench/apps/helpdesk
LOADTEST_SHAPE=stages loadtest/.venv/bin/locust -f loadtest/locustfile.py --headless \
    --host http://127.0.0.1:8001 --html loadtest/scaled_25workers.html
```
`LOADTEST_SHAPE=stages` ramps 50 → 100 → 200 → 350 → 500 users (holding each level) so
the curve reveals the knee. Without it, use plain `-u 500 -r 10 -t 10m`.

### Interactive (watch it live)
```bash
loadtest/.venv/bin/locust -f loadtest/locustfile.py --host http://127.0.0.1:8001
# open http://localhost:8089
```

---

## 4. What a HEALTHY result looks like

On the **Number of Users / Total RPS / Response Times** charts:

- **RPS rises roughly linearly with users**, then plateaus *gently* near capacity.
  It does NOT go flat early while users keep climbing.
- **Response times stay low and flat** as users ramp — p50 in the tens-to-hundreds of
  ms, p95 comfortably under a couple of seconds — until you approach the true ceiling.
- **Failure rate stays at ~0** across the whole ramp.
- **The "knee"** (where p95 suddenly bends upward) sits at or beyond your target user
  count. If you ramp to 500 and the knee is at ~520, you have just enough; if it's at
  900, you have healthy headroom.

Healthy console summary (illustrative):
```
tickets_page      ...  Avg  120  p95  280   0.0% fails
tickets_summary   ...  Avg   90  p95  210   0.0% fails
login             ...  Avg  600  p95 1400   0.0% fails
[loadtest] PASS — fail ratio 0.0%, p95 1400ms
```

## 5. What SATURATION looks like (the failing UAT run, and the 5-worker baseline)

- **RPS flat** (e.g. stuck at ~3/s) no matter how many users you add — the server is
  serving at its ceiling and everything else queues.
- **Response times climb in lockstep with users** — a straight diagonal line up, because
  requests sit in a queue whose length grows with load.
- **p95/p99 pinned near the proxy timeout** (~120 s) → `504 Gateway Time-out`,
  `RemoteDisconnected`, connection-refused (`HTTP 0`).
- **Failure rate climbs** past the knee.
- **`login` dominates** the latency table (it holds a worker longest under contention).

```
[loadtest] FAIL — fail ratio 38.2% > 1.0%; p95 117000ms > 5000ms
```

---

## 6. Pass/fail + what to do with the result

The harness scores the run automatically (exit code + a `[loadtest] PASS/FAIL` line):
- Thresholds: `LOADTEST_MAX_FAIL_RATIO` (default 1%) and `LOADTEST_MAX_P95_MS` (default
  5000). Override per run, e.g. `LOADTEST_MAX_P95_MS=3000`.

**If baseline (5w) FAILs and scaled (25w) PASSes** → confirms the fix is capacity. Hand
[`CAPACITY.md`](./CAPACITY.md) to the deployer to apply the worker/DB sizing on UAT/prod,
then re-run against UAT in a coordinated window.

**Actionable findings from §0 to consider alongside scaling:**
1. **Warm the boot cache / login.** The 5.5 s cold login is paid by the first user after
   every worker restart (gunicorn recycles workers every ~5000 requests). Options: a
   post-deploy warmup hit, or higher `gunicorn_max_requests` so restarts are rarer. Do
   **not** remove boot-cache from auth — it's load-bearing.
2. **Index `owner` on HD Ticket** (the customer/parent portal path). `contact` and
   `raised_by` are already indexed; `owner` is not, which defeats the index-merge on the
   permission OR. A one-line index (extend the existing `unity_ticket_list_indexes`
   patch) turns the ~4 s cold customer load into a sub-second one. Verify with
   `EXPLAIN` showing `index_merge` after adding it.
3. **Scale MariaDB `max_connections`** with the worker count (it's 50 locally; see
   CAPACITY.md) — 25 workers each holding a connection will exhaust 50 fast.

---

## 7. Teardown

```bash
# remove the test accounts when done
LOADTEST_TEARDOWN=1 bench --site unity.local console < loadtest/setup_test_users.py
# stop any background gunicorn you started
pkill -f "gunicorn -b 127.0.0.1:8001"
```
