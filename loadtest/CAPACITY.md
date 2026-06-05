# Helpdesk Server Capacity Plan — sizing for 500+ concurrent users

**Audience:** whoever provisions/deploys UAT & prod. This is a *recommendation* — the
config changes here are applied by the deployer, not committed-and-migrated by the app.

## TL;DR

The May-2026 load test didn't reveal slow code — it revealed **worker starvation**.
Frappe serves requests with **synchronous gunicorn workers**: each worker handles
exactly one request at a time. With only **5 workers**, the server can do ~5 concurrent
requests; the other 495 users queue until nginx's 120 s `proxy_read_timeout` fires →
504s and a ~40 % failure rate. The fix is **more concurrency on the web tier + matching
DB connections**, then re-test with the harness in this folder.

## The evidence it was capacity, not code

| Symptom in the report | What it means |
|---|---|
| RPS flat at ~3/s while users ramped 0→500 | Throughput ceiling hit almost immediately — classic saturation |
| Response time grew linearly with users (avg 65 s, p95 117 s) | Requests queuing behind a tiny worker pool |
| Max times clustered at ~118 s | nginx `proxy_read_timeout` (120 s) firing |
| Failures: 504, `RemoteDisconnected`, connection-refused (`got 0`) | Backend out of capacity / proxy gave up |

The helpdesk code is already heavily optimised (Redis-cached dashboard cards, split
page/summary endpoints, parallel student-context, FULLTEXT search, ToDo-based assignee
filter, the `unity_ticket_list_indexes` patch). Per-request cost is **not** the wall.

## Current configuration (reference: local dev bench)

| Setting | Value | Source |
|---|---|---|
| `gunicorn_workers` | **5** | `sites/common_site_config.json` |
| `background_workers` | 2 | `sites/common_site_config.json` |
| CPU cores (local box) | 12 | `nproc` |
| Worker class | sync (1 request/worker) | gunicorn default |
| nginx `proxy_read_timeout` | ~120 s | bench nginx template |
| MariaDB `max_connections` | 50 | `/etc/mysql/mariadb.conf.d/99-frappe-local.cnf` |
| `innodb_buffer_pool_size` | 3 G | same |

> ⚠️ **UAT box specs are unknown** — confirm its CPU core count and current
> `gunicorn_workers`/`max_connections` with the deployer before applying the numbers below.

## The sizing math (why 500 users ≠ 500 workers)

"500 concurrent users" is not 500 simultaneous in-flight requests. With think-time:

```
concurrent in-flight requests  ≈  active_users × (avg_request_time / think_time)
```

For 500 users at, say, ~250 ms per request and ~7 s of think-time between clicks:

```
500 × (0.25 / 7)  ≈  ~18 simultaneous requests
```

So the real target is roughly **15–25 workers' worth of concurrency**, plus headroom —
very achievable. The harness gives you the *actual* avg_request_time and the real
in-flight number under your traffic mix; size against those, not against the headline
"500".

## Recommended changes

### 1. Web tier concurrency (the decisive lever)

Pick one:

**Option A — more sync workers (simplest).** Set on the UAT/prod box:
```jsonc
// sites/common_site_config.json
"gunicorn_workers": <cpu_cores * 2 + 1>   // e.g. 25 on a 12-core box
```
Cost: each sync worker is a full process (~150–300 MB RAM). Sized by RAM, not just CPU.

**Option B — gthread workers (better RAM efficiency for I/O-bound Frappe).** Frappe
spends most of a request waiting on MariaDB/Redis, so threads add concurrency cheaply.
Configure gunicorn with a worker class of `gthread` and `--threads N` (set via the
bench supervisor config / `WORKERS`+`THREADS` env the production setup exposes).
Effective concurrency ≈ `workers × threads`. Start around `workers = cpu_cores`,
`threads = 4–8`, then let the harness tell you where the knee is.

> Re-run `bench setup supervisor` (or your deploy's equivalent) after changing worker
> counts so supervisor picks them up, then `supervisorctl reread && update`.

### 2. Database connections (must scale with the web tier)

Every worker/thread that's mid-request holds ≥1 DB connection. Raise MariaDB
`max_connections` to comfortably exceed:
```
(web concurrency: workers × threads) + background_workers + scheduler + headroom
```
e.g. 25 workers + 2 background + scheduler + buffer → set `max_connections` to **150–200**
on UAT/prod (local is intentionally capped at 50). Watch for `Too many connections`
errors in the MariaDB log as the canary.

### 3. Proxy timeouts (leave generous, aim to never approach)

Keep nginx `proxy_read_timeout` ~120 s. The goal of the changes above is that requests
finish in well under a second — the timeout should be a safety net, not a routine event.

### 4. Background workers

`background_workers: 2` is fine unless the load test shows email/notification jobs
backing up (check the RQ queue depth). Bump if so.

### 5. Redis

`redis_cache` (currently `maxmemory 1500 MB`, `allkeys-lru`) — confirm headroom on the
prod box; the dashboard-summary cache and per-row avatar caches live here. LRU eviction
under memory pressure quietly slows everything (cache misses → DB hits).

## Validation procedure

1. **Baseline:** run the harness against current config; reproduce the flat-RPS collapse
   to confirm the harness faithfully shows the failure mode.
2. **Apply** the web-concurrency + DB-connection changes on a **staging/local** bench
   sized like UAT.
3. **Re-test** at the real target with realistic think-time:
   ```
   locust -f loadtest/locustfile.py --headless -u 500 -r 10 -t 10m --host <staging>
   ```
   Pass criteria: RPS scales with users (no flat ceiling), p95 stays well under the 120 s
   proxy timeout, failure rate ≈ 0.
4. **Promote** to a coordinated, off-hours UAT window; re-run against UAT to confirm
   production-like behaviour before relying on it for real traffic.

## Open items (need deployer input)
- UAT/prod CPU core count, RAM, and current `gunicorn_workers` — for exact Option A/B numbers.
- Whether the prod web tier is one box or load-balanced (changes per-box sizing).
