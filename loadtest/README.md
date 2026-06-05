# Unity Helpdesk — Load Testing

Reproduce and measure the helpdesk's behaviour under concurrent load, **from our
side**, so we catch the capacity ceiling before real users hit it.

> Background: a May-2026 Locust run against UAT collapsed at 500 users — flat ~3
> RPS, p95 pinned at the 120 s nginx timeout, ~40 % failures. Root cause was
> **worker starvation** (Frappe uses synchronous gunicorn workers; the bench was
> set to 5 → ~5 concurrent requests max). See [`CAPACITY.md`](./CAPACITY.md) for the
> sizing plan that fixes it. This harness is how we verify the fix.

## What the harness does

`locustfile.py` simulates real Unity SPA traffic (every call mirrors
`unity_helpdesk/src/api.js` + the views):

| Simulated action | Endpoints hit (POST `/api/method/...`) | Task weight |
|---|---|---|
| Browse the ticket list | `get_tickets_page` + `get_tickets_summary` | 6 |
| Open a ticket | `get_ticket_detail` + `get_student_context` | 3 |
| Search | `get_tickets_page` + `get_tickets_summary` (with a term) | 1 |

Each simulated user **logs in once** (POST `/api/method/login`), fetches a CSRF
token (`get_csrf_token`), then reuses the session — exactly like a browser. It uses
realistic think-time (`between(3, 10)` s) so the numbers reflect real usage, not a
synthetic machine-gun storm.

## Setup

```bash
cd apps/helpdesk

# 1. Install locust into a DEDICATED venv (never the bench env)
python -m venv loadtest/.venv
loadtest/.venv/bin/pip install -r loadtest/requirements.txt

# 2. Provide a POOL of test accounts (so users don't all share one login)
cp loadtest/users.csv.example loadtest/users.csv
#   ...then edit loadtest/users.csv with real test-account passwords.
#   users.csv is git-ignored — never commit real credentials.
```

### Creating test accounts on a local site

Use the provided script (LOCAL site only — it creates real login-capable accounts):

```bash
cat loadtest/setup_test_users.py | bench --site unity.local console
```

This creates 30 `loadtest{N}@walnutedu.in` accounts, each with the **"Helpdesk Admin"
role AND an HD Agent record**. Both matter: helpdesk's HD Ticket permission filter
(`hd_ticket.permission_query`) treats a non-agent as a *customer* and restricts + slows
the query — `is_agent()` is true only when an HD Agent record exists. Without the HD
Agent record the test users see **0 tickets** and hit a slow scan path, so the test
wouldn't exercise the real "All Tickets" agent scenario. (We learned this the hard way —
see [`RUNBOOK.md`](./RUNBOOK.md) §0.)

Then build `users.csv`:
```bash
printf 'usr,pwd\n' > loadtest/users.csv
for i in $(seq 1 30); do printf 'loadtest%s@walnutedu.in,Loadtest@123\n' "$i" >> loadtest/users.csv; done
```
Tear them down later with: `LOADTEST_TEARDOWN=1 bench --site unity.local console < loadtest/setup_test_users.py`

## Running

> **⚠️ Don't load-test `bench serve`.** The dev server is a single Werkzeug process with
> almost no concurrency — testing it measures the dev server, not production. For the
> real capacity test, run against a **gunicorn-backed** server (see
> [`RUNBOOK.md`](./RUNBOOK.md) §1). The default `--host` below (`127.0.0.1:8000`) is fine
> for a quick *smoke* test that the harness works; it is not a capacity measurement.

**Interactive (recommended first):**
```bash
loadtest/.venv/bin/locust -f loadtest/locustfile.py
# host defaults to http://127.0.0.1:8000; open http://localhost:8089
```

**Headless (for CI / repeatable runs):**
```bash
loadtest/.venv/bin/locust -f loadtest/locustfile.py --headless \
    -u 200 -r 10 -t 5m --html loadtest/report.html
#   -u 200  total users     -r 10  spawn 10/sec     -t 5m  run for 5 minutes
```

**Auto-find the knee (staged ramp 50→100→200→350→500):**
```bash
LOADTEST_SHAPE=stages loadtest/.venv/bin/locust -f loadtest/locustfile.py --headless \
    --host http://127.0.0.1:8001 --html loadtest/report.html
```

**Pass/fail scoring.** Headless runs print `[loadtest] PASS/FAIL` and set the exit code,
gated on failure ratio and p95. Tune with `LOADTEST_MAX_FAIL_RATIO` (default `0.01`) and
`LOADTEST_MAX_P95_MS` (default `5000`).

### Where to point `--host`
- **Use `127.0.0.1`, not `unity.local`** — the latter usually has no DNS entry and
  silently yields `HTTP 0` connection errors. The dev server serves the default site
  regardless of Host header.
- **Capacity testing:** a gunicorn-backed local server or a staging box — see RUNBOOK §1.
- **UAT** (`https://uat.unityedu.tech`) — **only in a coordinated, off-hours window.**
  Hammering UAT causes the very downtime we're trying to prevent. Agree a slot with
  the deployer first.

## Reading the results

Don't trust the average — look at these four signals:

1. **RPS vs. Number of Users** — in a healthy system RPS *rises* as users ramp. If
   RPS goes **flat** while users keep climbing, you've hit the throughput ceiling and
   everything past it is queuing. (Flat ~3 RPS is exactly what the failing UAT run
   showed.)
2. **p95 / p99 response time** — the experience of your slowest users. Watch for the
   **"knee"**: response time stays flat, then suddenly spikes. That inflection point
   is your real capacity. If p95 approaches ~120 s you're hitting the nginx proxy
   timeout → imminent 504s.
3. **Failure rate** — should be ~0. `504` = proxy gave up waiting on a saturated
   backend; `0` / `RemoteDisconnected` = connection refused/reset (out of capacity);
   `500` = an application error worth investigating separately.
4. **The per-endpoint table** — which method dominates median/p95 tells you where to
   look (e.g. `tickets_summary` carries the dashboard-aggregate cost; `student_context`
   carries the Education-app joins).

## Tuning the scenario

- Stress test: lower `wait_time` toward `between(1, 3)` in `locustfile.py`.
- Soak test: raise `-t` to `30m`+ and keep users moderate, to surface leaks / GC.
- More load than one box can generate: run distributed (`--master` + `--worker`).
  `FastHttpUser` (already used here) generates far more load per core than the default.

## Companion server-side tooling

`helpdesk/api/unity_perf.py` already times the endpoints from *inside* Frappe:
```bash
bench --site unity.local execute helpdesk.api.unity_perf.run_endpoint_benchmark
bench --site unity.local execute helpdesk.api.unity_perf.run_filter_benchmark
```
Use it alongside Locust: Locust tells you *when* the system saturates; `unity_perf`
tells you *which query* dominates a single request.
