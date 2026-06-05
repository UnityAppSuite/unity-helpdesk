# Capture tooling (docs media)

Generates the screenshots (`docs/assets/*.png`) and feature GIFs
(`docs/assets/recordings/*.gif`) by driving the real Unity Helpdesk SPA (Vue,
served at `/unity-helpdesk/`) with Playwright against **clean, seeded demo
data** — no real student/guardian PII. Maintainers only — not shipped with the
app.

## Prerequisites

- A running bench serving the target site (`bench start`).
- Node 18+.
- Demo data + a capture agent seeded by `scripts/seed_demo.py` (see below).

## One-time setup

```bash
cd docs/_capture
npm install
npx playwright install chromium
# On a minimal Linux box without the browser's system libs and no sudo, fetch
# them locally into ./syslibs (see library_management/docs/_capture for the
# exact apt-get download list) and export LD_LIBRARY_PATH before running node.
```

> The bundled setup reuses the Playwright + Chromium + ffmpeg install (and the
> `syslibs/` shim) from `library_management/docs/_capture` via symlinks, so a
> fresh `npm install` is usually unnecessary on this bench.

## Seed demo data

```bash
bench --site unity.local console <<'PY'
import runpy; runpy.run_path('apps/helpdesk/docs/_capture/scripts/seed_demo.py')
PY
```

Creates a capture agent (`capture-agent@unity-demo.example.com`, System Manager
+ HD Agent → all Unity capabilities), 3 demo ticket types, 2 demo families
(guardians + student siblings with program enrollment + fees in the current
academic year), and ~8 demo tickets across statuses/types — all assigned to the
capture agent. Idempotent: re-running wipes the prior demo set first and writes
`demo_meta.json` (read by `capture.js`).

Because every demo ticket is assigned to the capture agent and uses demo-only
ticket types, **My Tickets**, the **agent-filtered Dashboard**, and the
**type-filtered All Tickets** view all show demo data only — the real
production tickets never appear in the media.

## Run

```bash
export LD_LIBRARY_PATH="$PWD/syslibs/root/usr/lib/x86_64-linux-gnu:$LD_LIBRARY_PATH"
node capture.js        # screenshots -> ../assets, videos -> ./_videos
./convert.sh           # ./_videos/*.webm -> ../assets/recordings/*.gif
```

`capture.js` reaches the site as `http://unity.local:8000` using Chromium's
`--host-resolver-rules` (maps `unity.local` → `127.0.0.1`), so no `/etc/hosts`
entry is needed. Override host/port/credentials with `HD_HOST`, `HD_PORT`,
`HD_USER`, `HD_PASS`, `HD_HERO` env vars.

The bulk-email and reply flows are filled in but **deliberately not sent** — the
scripts stop before the final Send so no real email is enqueued.

`node_modules/`, `syslibs/`, `_videos/`, `demo_meta.json`, and `*.deb` are
git-ignored — only the scripts are committed.
