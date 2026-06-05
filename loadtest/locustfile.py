"""Unity Helpdesk load test — mirrors the real SPA traffic, not a synthetic storm.

Why this exists
---------------
A May-2026 run against UAT collapsed at 500 users: flat ~3 RPS, p95 pinned at the
120 s nginx timeout, ~40 % failures. Root cause was worker starvation (Frappe serves
with synchronous gunicorn workers; the bench was set to 5). This harness lets us
reproduce and *measure* that failure mode from our side — locally first, UAT only in a
coordinated window — so we catch the ceiling before real users do.

What it models (faithfully)
---------------------------
Every call below matches what `unity_helpdesk/src/api.js` + the views actually do:
  * Login once per user via POST /api/method/login  (real users don't re-login per click)
  * Fetch the CSRF token via GET get_csrf_token, then send it on every POST
  * All API calls are POST /api/method/<method> with a JSON body + X-Frappe-CSRF-Token
  * The list view fires get_tickets_page + get_tickets_summary in parallel
  * The detail view fires get_ticket_detail, then get_student_context separately
  * Realistic think-time between actions (zero think-time overstates load — that's
    partly why the original "500 users" looked like a brick wall)

Run it
------
  python -m venv loadtest/.venv && loadtest/.venv/bin/pip install -r loadtest/requirements.txt
  loadtest/.venv/bin/locust -f loadtest/locustfile.py --host http://unity.local:8000

Then open http://localhost:8089. Or headless (see README.md for the full recipe):
  loadtest/.venv/bin/locust -f loadtest/locustfile.py --headless \
      -u 200 -r 10 -t 5m --host http://unity.local:8000

Credentials come from loadtest/users.csv (columns: usr,pwd) — a *pool* of accounts so
users don't all share one login (which skews caching and causes row-lock contention).
Falls back to the LOADTEST_USER / LOADTEST_PWD env vars if no CSV is present.
"""

import csv
import os
import random
import threading
from itertools import cycle
from pathlib import Path

from locust import FastHttpUser, between, events, task
from locust.exception import StopUser

# --- Method strings, copied verbatim from the SPA so the test can't drift -----
M_LOGIN = "/api/method/login"
M_CSRF = "/api/method/helpdesk.api.unity_helpdesk.get_csrf_token"
M_TICKETS_PAGE = "/api/method/helpdesk.api.unity_helpdesk.get_tickets_page"
M_TICKETS_SUMMARY = "/api/method/helpdesk.api.unity_helpdesk.get_tickets_summary"
M_TICKET_DETAIL = "/api/method/helpdesk.api.unity_helpdesk_ext.get_ticket_detail"
M_STUDENT_CONTEXT = "/api/method/helpdesk.api.unity_helpdesk.get_student_context"

# Views the list toggle exposes; weighted toward "all" / "my" like real usage.
VIEWS = ["all", "all", "all", "my", "my", "unassigned"]
# A few representative search strings — short tokens + an email-ish term so we
# exercise both the LIKE and the family/guardian-expansion search paths.
SEARCH_TERMS = ["fee", "leave", "bus", "exam", "admission", "walnutedu.in"]

PAGE_LENGTH = 20

# When connecting to a gunicorn/staging server by IP, Frappe resolves the site from
# the Host header (raw gunicorn does NOT honour serve_default_site the way `bench
# serve` does). Set LOADTEST_HOST_HEADER=unity.local so requests route to the site
# even though --host is http://127.0.0.1:8001. Empty => send no override.
HOST_HEADER = os.getenv("LOADTEST_HOST_HEADER", "").strip()
_BASE_HEADERS = {"Host": HOST_HEADER} if HOST_HEADER else {}


# --- Credential pool ----------------------------------------------------------
def _load_credentials():
    """Return a list of {usr, pwd} dicts from loadtest/users.csv, or fall back
    to a single account from env. Empty list => the test will error loudly on
    start, which is what we want (better than silently hammering as guest)."""
    csv_path = Path(__file__).parent / "users.csv"
    creds = []
    if csv_path.exists():
        with csv_path.open(newline="") as fh:
            for row in csv.DictReader(fh):
                usr = (row.get("usr") or "").strip()
                pwd = (row.get("pwd") or "").strip()
                if usr and pwd:
                    creds.append({"usr": usr, "pwd": pwd})
    if not creds:
        env_usr = os.getenv("LOADTEST_USER")
        env_pwd = os.getenv("LOADTEST_PWD")
        if env_usr and env_pwd:
            creds.append({"usr": env_usr, "pwd": env_pwd})
    return creds


_CREDS = _load_credentials()
_cred_cycle = cycle(_CREDS) if _CREDS else None
_cred_lock = threading.Lock()


def _next_credential():
    # itertools.cycle isn't guaranteed thread-safe; greenlets are cooperative
    # but guard anyway so a spawn burst can't trip over the iterator.
    with _cred_lock:
        return next(_cred_cycle)


@events.test_start.add_listener
def _check_credentials(environment, **kwargs):
    if not _CREDS:
        raise RuntimeError(
            "No credentials. Create loadtest/users.csv (columns: usr,pwd) or set "
            "LOADTEST_USER / LOADTEST_PWD. See loadtest/README.md."
        )


# --- The user -----------------------------------------------------------------
class HelpdeskUser(FastHttpUser):
    """One simulated agent/customer: logs in once, then browses + opens tickets."""

    # Default target. Use 127.0.0.1, NOT unity.local — the dev server serves the
    # default site (serve_default_site) regardless of Host, and unity.local often
    # has no DNS/hosts entry (which silently produces "HTTP 0" connection errors).
    # Override with --host on the CLI for UAT/staging.
    host = "http://127.0.0.1:8000"

    # Match the nginx proxy_read_timeout (~120 s) so the client doesn't cut off a
    # request the server would still answer — we want to MEASURE the long tail and
    # the queuing, not hide it behind a short client timeout.
    network_timeout = 120.0
    connection_timeout = 120.0

    # Realistic think-time. Drop toward between(1, 3) for a stress test, or raise
    # for a soak test. Zero would be a synthetic storm, not real usage.
    wait_time = between(3, 10)

    def on_start(self):
        self.csrf = ""
        self.ticket_names = []  # harvested from list responses, reused for detail
        cred = _next_credential()
        # 1) Login — sets the `sid` session cookie on self.client.
        with self.client.post(
            M_LOGIN,
            data={"usr": cred["usr"], "pwd": cred["pwd"]},
            headers=dict(_BASE_HEADERS),
            name="login",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"login HTTP {resp.status_code}")
                # Stop THIS user (so a bad account doesn't flood the server with
                # task requests) without killing the whole run — a transient
                # connection blip on one user shouldn't abort everyone's test.
                raise StopUser()
            resp.success()
        # 2) Fetch CSRF token, exactly as api.js _refreshCsrfToken() does.
        with self.client.get(M_CSRF, name="get_csrf_token",
                             headers=dict(_BASE_HEADERS), catch_response=True) as resp:
            token = _message(resp)
            if not token:
                resp.failure("no csrf token in response")
            else:
                self.csrf = token
                resp.success()

    # --- helpers --------------------------------------------------------------
    def _post(self, method, body, name):
        """POST a whitelisted method the way the SPA's call() does, and validate
        the Frappe envelope (200 + no `exc`). Returns the unwrapped `message`."""
        with self.client.post(
            method,
            json=body,
            headers={**_BASE_HEADERS, "X-Frappe-CSRF-Token": self.csrf},
            name=name,
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"HTTP {resp.status_code}")
                return None
            try:
                payload = resp.json()
            except Exception:
                resp.failure("non-JSON response")
                return None
            if payload.get("exc") or payload.get("_server_messages"):
                resp.failure(f"server exc: {str(payload.get('exc'))[:120]}")
                return None
            resp.success()
            return payload.get("message")

    # --- tasks (weights reflect real traffic: lots of list, some detail) ------
    @task(6)
    def browse_ticket_list(self):
        view = random.choice(VIEWS)
        params = {"view": view, "filters": None, "search": "",
                  "page_length": PAGE_LENGTH, "start": 0}
        # Fire page + summary like the SPA does (sequential here is fine — we're
        # measuring server capacity, not client-side overlap).
        page = self._post(M_TICKETS_PAGE, params, name="tickets_page")
        self._post(M_TICKETS_SUMMARY,
                   {"view": view, "filters": None, "search": ""},
                   name="tickets_summary")
        # Harvest a few ticket names so open_ticket has real targets.
        if isinstance(page, dict):
            names = [r.get("name") for r in (page.get("data") or []) if r.get("name")]
            if names:
                self.ticket_names = names[:PAGE_LENGTH]

    @task(3)
    def open_ticket(self):
        if not self.ticket_names:
            self.browse_ticket_list()
            if not self.ticket_names:
                return
        name = random.choice(self.ticket_names)
        self._post(M_TICKET_DETAIL, {"name": name}, name="ticket_detail")
        # Student context is fired separately by the SPA after detail lands.
        self._post(M_STUDENT_CONTEXT, {"ticket_name": name}, name="student_context")

    @task(1)
    def search_tickets(self):
        view = random.choice(VIEWS)
        term = random.choice(SEARCH_TERMS)
        params = {"view": view, "filters": None, "search": term,
                  "page_length": PAGE_LENGTH, "start": 0}
        self._post(M_TICKETS_PAGE, params, name="tickets_page (search)")
        self._post(M_TICKETS_SUMMARY,
                   {"view": view, "filters": None, "search": term},
                   name="tickets_summary (search)")


def _message(resp):
    """Pull Frappe's `message` field out of a response, tolerating errors."""
    if resp.status_code != 200:
        return None
    try:
        return resp.json().get("message")
    except Exception:
        return None


# --- Headless pass/fail gate --------------------------------------------------
# So a run can be scored automatically (CI / runbook): the process exits non-zero
# if the aggregate failure ratio or p95 latency blows past the thresholds. Tune
# via env. Defaults: <1% failures and p95 under 5 s = a healthy run.
MAX_FAIL_RATIO = float(os.getenv("LOADTEST_MAX_FAIL_RATIO", "0.01"))
MAX_P95_MS = float(os.getenv("LOADTEST_MAX_P95_MS", "5000"))


@events.quitting.add_listener
def _score_run(environment, **kwargs):
    stats = environment.stats.total
    p95 = stats.get_response_time_percentile(0.95) or 0
    fail_ratio = stats.fail_ratio
    reasons = []
    if fail_ratio > MAX_FAIL_RATIO:
        reasons.append(f"fail ratio {fail_ratio:.1%} > {MAX_FAIL_RATIO:.1%}")
    if p95 > MAX_P95_MS:
        reasons.append(f"p95 {p95:.0f}ms > {MAX_P95_MS:.0f}ms")
    if reasons:
        environment.process_exit_code = 1
        print(f"\n[loadtest] FAIL — {'; '.join(reasons)}")
    else:
        environment.process_exit_code = 0
        print(f"\n[loadtest] PASS — fail ratio {fail_ratio:.1%}, p95 {p95:.0f}ms")


# --- Optional staged ramp ("find the knee") -----------------------------------
# Activate with:  LOADTEST_SHAPE=stages locust -f loadtest/locustfile.py --headless ...
# When the env var is unset, NO shape class exists, so the normal -u/-r flags
# work as usual. With it set, Locust ignores -u/-r and walks the stages below —
# holding each level so the RPS/latency curve reveals where the server saturates.
if os.getenv("LOADTEST_SHAPE") == "stages":
    from locust import LoadTestShape

    class StagesShape(LoadTestShape):
        # (cumulative elapsed seconds, target users, spawn rate)
        stages = [
            {"t": 90, "users": 50, "rate": 10},
            {"t": 210, "users": 100, "rate": 10},
            {"t": 360, "users": 200, "rate": 15},
            {"t": 540, "users": 350, "rate": 20},
            {"t": 750, "users": 500, "rate": 25},
            {"t": 960, "users": 500, "rate": 25},  # hold at target
        ]

        def tick(self):
            run_time = self.get_run_time()
            for stage in self.stages:
                if run_time < stage["t"]:
                    return (stage["users"], stage["rate"])
            return None  # past the last stage => stop the test
