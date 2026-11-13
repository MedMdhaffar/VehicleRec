# WAYCON Vehicle Console

The console starts the existing T1 Input → CombinedEngine → Colour/Plate →
Aggregator pipeline and joins the manufacturer review decision to the same
vehicle lifetime. It supports uploaded MP4s and a fresh RTSP URI on every Start,
Stop, a backend frame preview with track IDs, run history and per-run status.

## Start locally

From any checkout directory inside Linux/WSL:

```bash
bash scripts/run_vehicle_console.sh
```

First use `bash scripts/run_vehicle_console.sh --check` to inventory missing
prerequisites. It installs nothing. With prerequisites present it verifies
actual CUDA model inference, run-owned Redis, and disposable DB1 delivery.
See the [WSL handoff checklist](../docs/t1/vehicle_wsl_handoff.md) for the branch,
one-time prerequisites and separately supplied approved assets.

This foreground launcher verifies CUDA and local model artifacts, probes the
existing run-owned Redis lifecycle, starts a private PostgreSQL 16 instance on
an available loopback port, and exercises the existing disposable P4 fixture,
schema and required registrations before declaring ready.
It then starts Django on loopback port 8002 and Vite on loopback port 5173,
printing one browser URL: `http://127.0.0.1:5173/`. It opens no video or camera.
Choose a browser MP4 upload or enter a fresh RTSP URI, then press Start.
Manufacturer is selectable, defaults off, and remains review only when enabled.
RTSP transport is explicit (TCP by default, UDP available); there is no fallback.

Rerunning the command reuses the running launcher. Ctrl-C, or a second terminal
running the following command, requests Stop and waits for the active run slot:

```bash
bash scripts/run_vehicle_console.sh --stop
```

Only owned web processes and the launcher's private PostgreSQL are stopped. If the slot cannot be confirmed released,
services remain running with an explanation. A drain marker blocks new Starts
during shutdown. Shared PostgreSQL and system Redis are never stopped or
reconfigured. Redis starts automatically on a private Unix socket for each run;
the startup probe is removed immediately and no shared broker is added.

Configuration may be supplied through owned mode-0600
`~/.config/waycon/console.json` or environment overrides. The WSL default is
`postgres_mode=owned`: no credentials or connection to Analytic are required.
It initializes a fresh temporary cluster, generates a private random password,
and removes that cluster after owned shutdown. It never adopts an existing
PostgreSQL listener or system Redis.

Analytic preserves its installed paths and `postgres_mode=existing`,
`pg_port=5433` through its existing private configuration. Only this explicit
mode reads `WAYCON_ADMIN_DSN_FILE` (owned mode-0600 libpq DSN) or private
`DB1_P4_ADMIN_DSN`. Credentials are excluded from the repository, argv, status
and reports. Remote database hosts are refused in both modes. All schema, registrations, facts and receipts are confined
to fresh disposable P4 databases using the existing fixture and restricted
application logins; fixture databases and roles are removed after verification.

Missing DB1 prerequisites/access fail startup clearly. `bash scripts/run_vehicle_console.sh
--console-only` is an explicit alternative, with a visible full-stack-not-ready
banner and no PostgreSQL delivery. There is no automatic downgrade. Startup
checks and private service logs live in `vehicle/media/waycon-launcher/`.

### Manual console-only development

For development without the full launcher, start one backend process using the environment that
contains the existing T1 detector, Paddle OCR, Django and Redis packages:

```bash
T1_CONSOLE_DB1=console-only PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python vehicle/manage.py runserver 127.0.0.1:8002 --noreload
```

In another terminal:

```bash
cd frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

Open `http://127.0.0.1:5173/`. In development, the browser always requests
same-origin `/api` routes; Vite proxies them to the verified Django listener at
`http://127.0.0.1:8002`. Forward **only port 5173** from your computer to
Analytic, using your usual SSH target and connection options:

```bash
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:5173:127.0.0.1:5173 intern@Analytic
```

`VITE_API_URL` remains available for production builds. The development console
uses its proxy even if that variable is present in an existing environment or
`.env` file; those deployment settings are left intact. Existing dependencies
are already installed on Analytic. Its inspected Node executable can also run
Vite directly:

```bash
/home/intern/part-time/mohamed/VehicleRec/.runtime/tooling/node-v22.16.0-linux-x64/bin/node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5173
```

Choose **Analyze every Nth frame** before Start: a positive integer, default 1
for MP4 and 5 for RTSP. New runs save `sample_every`; historical frame-limit
records remain readable. The API rejects the obsolete `max_frames` parameter.
MP4 decodes sequentially through EOF and analyzes source indexes 0, N, 2N, ….
Its blocking, one-frame handoff applies backpressure, so slow inference does
not discard selected frames. There is no default run timeout. An explicitly
entered timeout, Stop, or an error produces a partial outcome with its reason;
only settled EOF runs report full-video completion.

RTSP continuously decodes, samples before inference, and retains one selected
frame slot, preferring the newest selected frame after the first handoff.
Decoded frames, analyzed frames, intentional sampling skips and overload drops
are separate counters. Preview updates independently of inference. Receipts
retain source indexes and capture timestamps; trackers age across source gaps.
Reconnect is a fresh Start after Stop/outage, with a newly entered private URI,
index zero and an empty tracking scope. There is no automatic camera reopen.

The console streaming profile removes the old frame, total-detection and
native-call run caps. Existing per-track evidence quotas and memory/resource
guards remain: detectors and Colour inference continue on selected frames,
while retained Class/Colour evidence and Plate candidates stay bounded. A
resource guard failure reports an error, never full-video completion. Standalone
bounded smoke runners keep their separate validation profiles.

One console run is active at a time. Stop ends input, cancels owned
manufacturer work and waits for other admitted native work to retire. Completed
runs remain selectable in Run history. An interrupted worker is reported and
is never automatically restarted or reinferred.

The supported deployment is **one Linux web process per canonical console run
root**, with concurrent request threads allowed. The first console request holds
an exclusive `flock` on `.server.lock` for the web process lifetime. A second web
process receives HTTP 503 on all console endpoints, including Start; workers do
not inherit this lock across exec. Use `runserver --noreload`, or one WSGI worker
with threads. Multiple WSGI/ASGI workers serving this console are unsupported.
Keep a stable, private local `T1_CONSOLE_RUN_ROOT`; do not unlink the lock file or
assign different roots to web workers to bypass admission.

Start reserves the slot before staging input or spawning. The worker waits on
an inherited pipe until its ownership record has been published. Stop during
launch is retained for that worker. The API's `active_slot` remains true until
the child exits, even if it has already published a terminal result. Failed
launches retain the slot through child reaping and private-file cleanup; failed
cleanup retains admission for manual inspection. No timer releases the slot.

## Results and delivery

The [sampling verification report](../docs/t1/vehicle_sampling_results.md)
records exact-index MP4, loopback RTSP, Stop/reconnect and separate real-model
PostgreSQL commit-before-ACK acceptance, with reproducible commands.

Each row is keyed by run and vehicle track. Type, colour and Plate come from T1
terminals; manufacturer is a local model prediction marked **Review only**.
`Unknown` means the recognizer could not identify a supported brand. `Disabled`
means recognition was off for that run. Timeouts and execution failures remain
visible as their own outcomes. The console displays no confidence scores.

### Track crops

New runs retain one vehicle thumbnail per track, preferring the exact original
BGR crop handed to the manufacturer runtime. When recognition is disabled or
has no eligible crop, the first retained vehicle observation supplies the image
and is labelled separately. The plate image is the crop selected by the Plate
policy, matched by attempt/candidate identity and pixel hash, rather than an
arbitrary detected rectangle. Images are lossless PNGs displayed at thumbnail
size. Source frame indices are **zero-based**; frame 0 is the first decoded
frame. Unknown source frames stay unknown.

When no selected plate crop exists, the console uses retained pipeline evidence
to distinguish **No plate detected**, **Not associated**, and **Crop rejected**.
Incomplete or missing diagnostics display **Unknown**. Unassociated plate
images are never attached to a vehicle as if they belonged to it.

Display artifacts live only in that run's private `crops/` directory and
`crops.json` sidecar. Image endpoints require the run, track and crop kind, serve
only those PNGs with `Cache-Control: no-store`, and reject missing/cross-run
symlinks. Canonical DB1 bundles, Plate policy, model input and delivery states
are unchanged. Older runs with no retained crop pixels show an unavailable
placeholder. For a settled MP4 with a manufacturer journal, explicit offline
recovery can verify the retained frame and crop hashes without rerunning models:

```bash
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python scripts/recover_console_crops.py RUN_UUID
```

The [crop verification report](../docs/t1/vehicle_console_crops_results.md)
records the inspected historical run, recovered crops and real MP4 check.

PostgreSQL commit, Redis DB1 ACK and manufacturer review handoff are distinct
states. The full launcher uses the existing canonical Engine → Redis →
Aggregator → natural DB1 consumer path. Its separate DB1 process commits each
delivery through the existing disposable fixture, then verifies exact facts and
immutable receipts on a separate restricted PostgreSQL connection while that
exact Redis entry remains pending. Only then does the existing consumer XACK.
The UI independently displays `Committed` and `Acknowledged`. A failed delivery
fails the run, with the last confirmed commit/ACK states retained. This does not
write manufacturer facts or register manufacturer predictions for production.

Private `db1-progress.json` evidence records counts, hashes, the before-ACK
connection and exact Redis entry, plus fixture cleanup. `db1-wire-*.json` retains
canonical input privately for inspection, including on delivery failure. There
is no automatic replay/reinference after failure. Demo database rows are
disposable; the evidence records their committed visibility before cleanup.
Console-only runs show `Not configured` / `Not attempted`. No shared DB1
catalogue changes are part of the launcher.

## Private RTSP Start

Enter the URI only in the password field. Start sends it in the POST body and
clears the field. The backend stages a new mode-0600 file inside an owned
mode-0700 temporary directory for that run. It removes that camera snapshot
when the worker finishes normally. The URI is absent from process arguments, URL paths,
query strings, status responses, logs and browser storage. Preview/status routes
contain only the opaque run ID. Enter another URI on the next Start.

A hard exit can bypass cleanup and leave the private camera file and the run's
owned Redis server behind. Terminal runs with missing, empty, partial or failed
cleanup show **Cleanup needs attention**. This warning does not claim recovery.
There is no automatic PID reaper or wildcard directory deletion. Inspect and
recover only the exact resources belonging to that run. A backend restart with
unpublished ownership fails closed with `ownership_uncertain` and retains the
slot for manual inspection; it never guesses that a launch has timed out.

## Local configuration

Optional backend environment variables select existing local resources:

| Variable | Purpose |
|---|---|
| `T1_CONSOLE_PYTHON` | Launcher/backend/worker Python 3.11. The shell uses the project `.venv`, active environment or PATH to bootstrap private configuration selection. |
| `T1_CONSOLE_RUN_ROOT` | Private run metadata/uploads; defaults to `vehicle/media/t1-console`. |
| `T1_MANUFACTURER_LOCK` | Existing verified model/runtime lock; defaults to `vehicle/common/manufacturer/assets.json`. |
| `T1_OCR_DIR` | Local OCR export; defaults to checkout `models/PP-OCRv6_medium_rec`. |
| `T1_REDIS_SERVER` | Existing Redis executable; defaults to `redis-server` on PATH. |
| `WAYCON_CONFIG` | Owned private JSON configuration; defaults to `~/.config/waycon/console.json`. Explicit environment values take precedence. |
| `WAYCON_ADMIN_DSN_FILE` | Existing private fixture provisioning DSN file; no credentials in CLI arguments. |
| `WAYCON_NODE` | Installed Node executable; defaults to PATH. Node 22.12+ or 20.19+ is required. |
| `T1_CONSOLE_DEVICE` / `CUDA_VISIBLE_DEVICES` | Explicit CUDA ordinal (default `cuda:0`) and optional standard GPU visibility mask. There is no MIG-specific setting or CPU fallback. |
| `WAYCON_EXPECTED_GPU` | Optional expected GPU name substring; the WSL example requires `RTX 5080`. |
| `WAYCON_POSTGRES_MODE` | `owned` (default) or explicit `existing` for a configured local test listener. |
| `WAYCON_PG_BIN` / `WAYCON_PG_PORT` | PostgreSQL 16 binary directory and local port; owned mode chooses a free port by default. |
| `WAYCON_API_PORT` / `WAYCON_WEB_PORT` | Loopback Django/Vite ports, default 8002/5173. Vite receives the corresponding same-origin proxy target. |
| `T1_VEHICLE_MODEL`, `T1_VEHICLE_TRACKER`, `T1_PLATE_MODEL`, `T1_PLATE_TRACKER`, `T1_MARKER_MODEL` | Optional local path overrides; defaults are the checkout's existing T1 files. |

Private JSON keys map to these variables: `python`, `node`, `redis_server`,
`ocr_dir`, `manufacturer_lock`, `admin_dsn_file`, `device`, `cuda_visible_devices`,
`expected_gpu`, `postgres_mode`, `pg_bin`, `pg_port`, `api_port`, `web_port`,
`vehicle_model`, `vehicle_tracker`, `plate_model`, `plate_tracker`, `marker_model`.
Manufacturer recognition defaults off. Its optional verified manifest uses
paths relative to that manifest, so a relocated runtime needs no other user's
checkout. Missing optional manufacturer files are listed in prerequisites.json;
enabling an unavailable manufacturer runtime is rejected explicitly.
Plate recognition uses the existing explicit CPU binding. Missing resources
fail the run; the console does not install models or select another provider.

## Verification

The [full launcher report](../docs/t1/vehicle_launcher_results.md) records the
browser MP4/DB1 proof, owned RTSP checks, startup/reuse/shutdown and current limits.

`npm run build` builds the frontend; `npm run lint` runs ESLint. Backend API
checks use `python vehicle/manage.py test api.test_console`; shared projection
checks use `python -m unittest vehicle.common.tests.test_console_projection`.
The deterministic race suite is `python vehicle/manage.py test api.test_console_lifecycle`
and needs no Redis, database, GPU or camera. The separate
`python vehicle/manage.py test api.test_console_resources` hard-exit probe uses
an explicitly owned native Redis fixture (`T1_REDIS_SERVER` selects its binary).
Frontend lifecycle checks use `node --test frontend/tests/runLifecycle.test.mjs`
from the repository root.

The [verification report](../docs/t1/vehicle_console_results.md) records the real
browser-started MP4 runs, native Redis delivery checks, screenshots, privacy
checks, commands and remaining live-camera verification limit.
The [race-fix report](../docs/t1/vehicle_console_race_results.md) includes failing
and passing barrier evidence, browser regressions and cleanup recovery limits.
The [single-port proxy report](../docs/t1/vehicle_console_proxy_results.md)
records the verified port-8002 listener, same-origin browser regression and
real MP4 run through port 5173.
