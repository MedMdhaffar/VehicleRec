# Vehicle Console single-port proxy verification — 2026-11-13

The console is available at **http://127.0.0.1:5173/** through the existing
port-5173 tunnel alone. The existing Django and Vite processes were healthy and
reused: backend PID `2528432`, frontend PID `2528433`. Both remain listening on
loopback. No unrelated service was stopped and no camera was opened.

## Diagnosis and fix

Observed on Analytic before the change:

| Check | Result |
|---|---|
| Django `127.0.0.1:8002/api/t1/runs/` | HTTP 200 |
| `127.0.0.1:8000/api/t1/runs/` | Connection refused; no HTTP response |
| Vite `127.0.0.1:5173/api/t1/runs/` | HTTP 502 from stale proxy target |
| API origin injected into the served frontend | `http://127.0.0.1:8002` |
| Polling response check | Incorrect `response.OK` |

The browser called port 8002 directly. On a PC with only port 5173 forwarded,
that loopback address is the PC rather than Analytic's Django listener. This
explains the reported failure before an HTTP response. The VM's Django service
was not missing. Switching to same-origin requests also required repairing the
Vite proxy, which still pointed to inactive port 8000. Independently, the
uppercase `response.OK` check treated healthy HTTP responses as offline.

Development now always uses relative `/api` URLs. Vite forwards those requests
to `http://127.0.0.1:8002` with `changeOrigin: true`. Polling correctly checks
`response.ok`. Vite picked up its configuration change automatically; the
listener PIDs stayed the same. Production builds continue honoring
`VITE_API_URL`; the existing `.env` and all Django deployment settings remain
unchanged. The launcher, worker and Start/Stop race-fix source files were not
modified; their baseline/final SHA-256 values match.

## Browser and real MP4 result

Playwright/Chromium was available on Analytic, so verification used the actual
browser UI and native worker, with no controlled/demo worker. The browser test
deliberately blocks direct access to port 8002 to model a single forwarded port.
Before the fix it reproduced failed direct requests and an offline console.
After the fix every console request used origin `http://127.0.0.1:5173`.
The run-list endpoint returned HTTP 200 through that proxy.

The browser uploaded the existing known-good
`vehicle/license_plate_service/run_video_pipeline/test_video.mp4` (2,381,732
bytes), leaving manufacturer recognition off and the default four-frame limit.

| Observation | Result |
|---|---|
| Run ID | `170b205d-c035-47d4-b770-6e547bdf24df` |
| Start through port 5173 | HTTP 202 |
| Frames observed | 0 → 1 → 2 → 3 → 4 |
| Final state | `completed`, 4/4 frames, no error |
| Preview | Loaded in browser; backend revision reached 8 |
| Vehicle tracks | 2 actual backend rows |
| Plate / Colour | One private Plate decision read; blue and black colour decisions |
| Manufacturer | Disabled on both rows; zero model calls |
| Cleanup | All five guarantees explicitly true |
| Worker / admission | Original worker exited; `active_slot: false`; Start enabled |
| Connection failures after fix | 0; React StrictMode's deliberate initial GET cancellation is excluded |

The test observed `completed` while the slot was still held, then waited for
actual worker exit and release before passing. PostgreSQL commit remained
`not_configured`, Redis DB1 ACK `not_attempted`, and review handoff `disabled`.
The two canonical publications were to the worker's owned temporary Redis;
they are not evidence of PostgreSQL commit or DB1 ACK. No production database
was used. No camera credentials, footage or raw plate text are in this report
or the browser result artifact.

## Focused checks and evidence

All checks passed:

- 14 API/lifecycle tests, including the barrier race, Stop, competing Start,
  repeat Start and RTSP URI privacy.
- Three frontend lifecycle tests.
- ESLint and Vite production build.
- Actual single-port browser MP4 acceptance described above.

Evidence is under
[`vehicle/media/console-proxy-validation-20261113`](../../vehicle/media/console-proxy-validation-20261113/):
[before endpoints](../../vehicle/media/console-proxy-validation-20261113/failing-before.json),
[before browser](../../vehicle/media/console-proxy-validation-20261113/browser-before.json),
[proxy HTTP 200](../../vehicle/media/console-proxy-validation-20261113/proxy-after.json),
[real MP4 browser result](../../vehicle/media/console-proxy-validation-20261113/browser-mp4.json),
[progress log](../../vehicle/media/console-proxy-validation-20261113/browser-mp4.log),
and API/frontend logs plus baseline/final hashes.

Commands ran from the repository root unless `cd frontend` is shown:

```bash
ss -ltnp '( sport = :8002 or sport = :5173 )'
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python vehicle/manage.py test api.test_console_lifecycle api.test_console --verbosity 2

cd frontend
/home/intern/part-time/mohamed/VehicleRec/.runtime/tooling/node-v22.16.0-linux-x64/bin/node --test tests/runLifecycle.test.mjs
/home/intern/part-time/mohamed/VehicleRec/.runtime/tooling/node-v22.16.0-linux-x64/bin/node node_modules/eslint/bin/eslint.js .
/home/intern/part-time/mohamed/VehicleRec/.runtime/tooling/node-v22.16.0-linux-x64/bin/node node_modules/vite/bin/vite.js build
cd ..

CONSOLE_TEST_PLAYWRIGHT=/home/intern/part-time/mohamed/VehicleRec/.runtime/browser/node_modules/playwright CONSOLE_TEST_CHROMIUM=/home/intern/part-time/mohamed/VehicleRec/.runtime/browser/browsers/chromium_headless_shell-1161/chrome-linux/headless_shell CONSOLE_TEST_RESULT=/home/intern/part-time/aziz/pipeline/vehicle/media/console-proxy-validation-20261113/browser-mp4.json CONSOLE_PROXY_MODE=mp4 CONSOLE_TEST_MP4=/home/intern/part-time/aziz/pipeline/vehicle/license_plate_service/run_video_pipeline/test_video.mp4 /home/intern/part-time/mohamed/VehicleRec/.runtime/tooling/node-v22.16.0-linux-x64/bin/node scripts/console_proxy_browser.cjs
```

The browser regression defaults to `health` mode without an MP4 run. The red
run used that mode and saved `browser-before.json`; `mp4` mode explicitly opts
into the native local pipeline. The direct endpoint checks also used Python's
`urllib.request`, preserving HTTP status or connection-error type without
dumping response bodies.

Changed files: `frontend/src/App.jsx`, `frontend/vite.config.js`,
`frontend/README.md`, `scripts/console_proxy_browser.cjs`, this report and its
validation artifacts. Existing work and the lifecycle race fix were preserved.
No commits or pushes.
