# Vehicle Console lifecycle race verification — 2026-11-13

The Start/status race is fixed in the local console. Existing recognition,
delivery and frontend work was preserved. No DB1 catalogue changes, production
writes, commits or pushes were performed.

## Failing before, passing after

The regression was added and run **before** changing the launcher. A real
controlled child waits on a pipe; the launcher pauses at a two-party barrier
after Popen has created the child and before ownership is returned/published.
Start runs on a separate Django request thread. While publication is paused,
other requests poll detail/list, request Stop and submit a competing Start.
No video decoder, camera, GPU, Redis or database is needed for this race suite.

| Observation at the boundary | Before | After |
|---|---|---|
| Detail/list status | `interrupted` / `interrupted` | `starting` / `starting` |
| Stop status | `interrupted` | `stopping` |
| Stop marker recorded | false | true; controlled child observes it |
| Competing Start | HTTP 202 | HTTP 409, while publication remains paused |
| Spawned children | 2 | 1 |

The first red run failed at `assertNotEqual(detail['status'], 'interrupted')`.
Its captured observations also show lost Stop and duplicate admission. The
final regression additionally waits for the competing response before opening
the publication barrier. Timeouts bound test failure; they do not release an
application slot.

Evidence under
[`vehicle/media/console-race-validation-20261113`](../../vehicle/media/console-race-validation-20261113):

- [Failing-before log](../../vehicle/media/console-race-validation-20261113/failing-before.log).
- [Passing-after log](../../vehicle/media/console-race-validation-20261113/passing-after.log): 14 API/lifecycle tests passed; Django explicitly skipped database setup.
- [Backend source before the fix](../../vehicle/media/console-race-validation-20261113/console_runs.before.py), baseline Git status and scoped SHA-256 files.
- [Browser results](../../vehicle/media/console-race-validation-20261113/browser.json), [browser log](../../vehicle/media/console-race-validation-20261113/browser.log) and [cleanup warning screenshot](../../vehicle/media/console-race-validation-20261113/cleanup-attention.png).

## Lifecycle and deployment

Start reserves the slot and publishes starting metadata under a request-thread
lock before staging input or spawning. Input staging, Popen and failed-launch
cleanup run outside that lock so polling, Stop and conflicting admission remain
responsive. A separate reservation covers the full launch/cleanup interval.
Terminal status is a result projection; `active_slot` is the admission decision.
A child that has published `completed` still holds the slot until it exits.

The worker waits for one byte on an inherited launch pipe before entering the
pipeline. The launcher publishes PID/start-ticks ownership and its lifecycle
record before releasing that gate. An unpublished launch cannot allocate model,
decoder or Redis resources. On spawn or ownership-write failure the launcher
retains the reservation, terminates/reaps its own Popen child, removes its exact
private camera directory and records the failed run. Incomplete cleanup keeps
admission reserved, including after a backend restart. There is no sleep or
elapsed-time grace period in admission or worker-death classification. Bounded
waits during termination allow escalation using the owned Popen handle.

The actual worker is also tested with Stop recorded before ownership
publication: it exits `stopped` with zero frames and complete cleanup before
loading models. A controlled live owner survives loss of the web registry;
after actual child exit its nonterminal projection becomes `interrupted`.
Completed terminal histories retain their terminal status after child exit.

The existing documented deployment was one local backend process. It is now
enforced: **one Linux web process per canonical run root, with concurrent
request threads**. A process-lifetime `flock` on `.server.lock` rejects console
endpoints in another process with HTTP 503. Forked/inherited-registry and fresh
interpreter tests both pass; the fork test checks list, Start, detail, Stop and
preview. The lock descriptor is closed across worker exec. Multiworker
WSGI/ASGI deployment is unsupported. Use `runserver --noreload` or one WSGI
worker with threads and a stable private local run root. Do not unlink the lock
file or assign separate roots to bypass this constraint.

Restart inspection uses the owner PID, Linux process start ticks, expected
script/run arguments and non-zombie state. It never signals a recovered PID.
Missing/unreadable ownership after an unpublished launch is uncertain, retains
admission and asks for manual inspection. It is not treated as proof of death.

## Separate hard-exit finding and recovery limits

The separate resource probe uses the existing `OwnedRedis` class with a fresh
private Unix socket, port 0 and an owned temporary directory. It stages an RTSP
credential file without opening any camera. After readiness, `os._exit(23)` in
the controlled worker bypasses cleanup. Both findings were reproduced:

- The mode-0600 credential snapshot remained on disk.
- The owned Redis server continued answering PING after the worker exited.

The API correctly reports `interrupted`, `active_slot: false`, `cleanup: null`.
The UI visibly displays **Cleanup needs attention** for terminal null, missing,
empty, partial or failed cleanup. All five cleanup guarantees must be explicitly
true for “Resources released”. See the
[resource log](../../vehicle/media/console-race-validation-20261113/hard-exit.log).

Fixture recovery was performed and checked: shutdown used the **exact private
Unix socket returned by that fixture**, and deletion used only its exact Redis
and camera directories. Those directories were absent afterward. No PID-only
kill, wildcard deletion or automatic application reaper was added. Application
recovery after a hard exit remains manual; the worker does not yet persist a
durable resource inventory for safe orphan Redis/native-descendant recovery.
GPU/native-descendant recovery and host-power-loss recovery were not tested.
An uncertain launcher record also needs operator inspection before admission
can be released. The warning reports uncertainty; it does not claim recovery.

## Frontend and privacy regressions

The production frontend build was served against the actual Django console
API with controlled pipe workers. This is explicitly a lifecycle fixture;
its MP4 upload contains fixture bytes and no video inference is performed.
The browser verified MP4 Start/Stop, repeat Start returning 409, two RTSP Starts
with distinct private snapshots, cleared URI fields, normal cleanup, a terminal
worker keeping Start disabled, and a hard exit displaying the cleanup warning.
The RTSP URI appeared in each Start POST body and was absent from request URLs,
public responses, browser console and browser storage. API tests also cover
private file permissions, per-Start URIs, Popen arguments and preview delivery.

All passed: 14 API/lifecycle tests, one isolated Redis resource probe, five
projection tests, three frontend lifecycle tests, the browser scenario,
ESLint, Vite production build and Django check. Logs are retained alongside
the failing-before evidence. No database fixture was skipped to claim success;
the Django suites use `SimpleTestCase` and perform no database setup.

The earlier real MP4/Plate/Colour/manufacturer verification remains recorded in
[the original console report](vehicle_console_results.md). It was not rerun
with GPU models for this concurrency fix. Live-camera verification remains
separate and unperformed; RTSP stays selectable and its private control path
is covered with an unopened loopback URI.

## Exact validation commands

Commands ran from `/home/intern/part-time/aziz/pipeline` unless `cd frontend`
is shown. Red command was run before the fix; the same label now contains the
expanded race suite. Output redirects used the evidence directory above.

```bash
# Failing before the fix (initial single regression)
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python vehicle/manage.py test api.test_console_lifecycle --verbosity 2

# Passing after
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python vehicle/manage.py test api.test_console_lifecycle api.test_console --verbosity 2
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python vehicle/manage.py test api.test_console_resources --verbosity 2
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_console_projection
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python vehicle/manage.py check

cd frontend
/home/intern/part-time/mohamed/VehicleRec/.runtime/tooling/node-v22.16.0-linux-x64/bin/node --test tests/runLifecycle.test.mjs
/home/intern/part-time/mohamed/VehicleRec/.runtime/tooling/node-v22.16.0-linux-x64/bin/node node_modules/eslint/bin/eslint.js .
VITE_API_URL='' /home/intern/part-time/mohamed/VehicleRec/.runtime/tooling/node-v22.16.0-linux-x64/bin/node node_modules/vite/bin/vite.js build
cd ..

# Empty VITE_API_URL builds same-origin API requests for the fixture server;
# the existing frontend/.env was left untouched.
export CONSOLE_TEST_NODE=/home/intern/part-time/mohamed/VehicleRec/.runtime/tooling/node-v22.16.0-linux-x64/bin/node
export CONSOLE_TEST_PLAYWRIGHT=/home/intern/part-time/mohamed/VehicleRec/.runtime/browser/node_modules/playwright
export CONSOLE_TEST_CHROMIUM=/home/intern/part-time/mohamed/VehicleRec/.runtime/browser/browsers/chromium_headless_shell-1161/chrome-linux/headless_shell
export CONSOLE_TEST_SCREENSHOT=/home/intern/part-time/aziz/pipeline/vehicle/media/console-race-validation-20261113/cleanup-attention.png
export CONSOLE_TEST_RESULT=/home/intern/part-time/aziz/pipeline/vehicle/media/console-race-validation-20261113/browser.json
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python scripts/console_browser_regression.py
```

## Files changed in this follow-up

These are incremental changes to the dirty workspace, not a claim that its
earlier uncommitted feature work originated in this fix.

| File | Change |
|---|---|
| `vehicle/common/console_runs.py` | Reservation, admission, process lock, owner identity, gated launch and failure cleanup |
| `scripts/vehicle_console_worker.py` | Launch pipe gate and synchronous pre-launch Stop handling |
| `vehicle/api/console_views.py` | Fixed HTTP 503 response for unsupported second web process |
| `vehicle/api/test_console.py` | Repeat-Start fixture now retires the previous worker before release |
| `vehicle/api/test_console_lifecycle.py` | Deterministic concurrent lifecycle and deployment regressions |
| `vehicle/api/test_console_resources.py` | Separate isolated native Redis hard-exit/recovery probe |
| `vehicle/api/console_worker_fixture.py` | Pipe-controlled child without inference |
| `frontend/src/App.jsx`, `frontend/src/App.css` | Honor backend active slot; visible cleanup warning |
| `frontend/src/runLifecycle.js`, `frontend/tests/runLifecycle.test.mjs` | Cleanup/slot projection and regressions |
| `scripts/console_browser_regression.py`, `scripts/console_browser_regression.cjs` | Actual-API browser regression harness |
| `frontend/README.md` | Enforced deployment model and recovery limits |
| `docs/t1/vehicle_console_race_results.md` | This report |
| `vehicle/media/console-race-validation-20261113/` | Baseline, before/after logs, browser results and screenshot |
