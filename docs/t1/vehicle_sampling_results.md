# WAYCON configurable sampling verification — 2026-11-13

| Area | Result | Evidence |
| --- | --- | --- |
| MP4 | PASS | Exact source indexes at N=1/N=5; EOF, Stop, timeout, next Start, 101-frame run; real-model EOF run. |
| RTSP loopback | PASS | Real decoder API fixture plus native CUDA browser runs at N=1/N=5; advancing preview, bounded drops, Stop and fresh tracking scopes. |
| DB1 delivery | PASS | Separate PostgreSQL connections verified committed facts, acceptance and row receipts while exact Redis entries were pending, followed by real ACK. |
| Real camera | BLOCKED | Camera access unavailable; no live-camera claim. |

## Behavior and scope

The frontend, console API and worker now use `sample_every`: an integer >=1,
defaulting to 1 for MP4 and 5 for RTSP. The chosen value is retained in run
configuration, status and history. Historical records keep their original data
and are labelled historical. Obsolete `max_frames` requests receive HTTP 400.
Manufacturer remains off by default and review only when enabled.

MP4 uses one sequential decoder and blocking inference, retaining one input
frame at a time. It reads to EOF and does not drop selected frames. There is no
default timeout. An explicit timeout reports `partial/time_limit`; Stop reports
`stopped/controlled_stop`; errors report a failed partial run. Full-video
completion requires EOF and successful settlement. A decoder failure before the
declared frame count raises an error instead of reporting EOF.

RTSP samples in its owned decoder before inference. One shared frame slot plus
one host copy bounds buffering. Frame zero is retained until the first handoff;
subsequent selected frames overwrite the slot and increment overload drops.
Nonselected frames increment sampling skips instead. Independent live JPEG
previews continue while inference is busy. Receipts retain decoded source
indexes, paired capture UTC/monotonic times and decoder PTS. ByteTrack advances
through source-frame gaps with bounded empty updates. Reconnect uses the
existing explicit fresh Start after Stop/outage, with a fresh private URI,
index zero, run context and empty tracker; there is no automatic camera reopen.

The console's explicit streaming profile disables the old 4/8-frame,
180-second, aggregate raw-detection and native-call run budgets. Standalone
smoke runners retain their separate bounded profiles and are not console run
paths. Per-track retained Class/Colour evidence (20) and Plate candidates (16),
pixel budgets and lifetime capacity guards remain bounded. Native vehicle and
Plate detection and Colour inference continue on every selected frame;
nonadmitted evidence is counted. Resource guard violations fail the run as
partial instead of claiming full completion. No catalogue, contract, model or
DB1 delivery policy was changed.

The single-run launch gate, Stop-during-Start protection, same-origin proxy,
private URI handling, run-owned Redis and existing disposable DB1 consumer path
remain in use. PostgreSQL commit and Redis ACK remain separate fields; stopped
tracks without a publication are `not_attempted`, never falsely committed.

## Executed acceptance

The exact-index fixture uses the console HTTP API, real FFmpeg/OpenCV decoding,
the shared Engine and run-owned Redis, with an explicitly enabled server-side
empty-inference fixture. It cannot be selected through the API and refuses
DB1-enabled operation. It establishes selection and lifecycle behavior, not
recognition accuracy or DB1 success.

Primary evidence: [API index and lifecycle evidence](../../vehicle/media/sampling-validation-20261113/api-acceptance.json).
The complete API acceptance was repeated after the final Stop/decoder change:
[final repeat](../../vehicle/media/sampling-validation-20261113/final-api/api-acceptance.json),
also PASS for MP4 and RTSP loopback, including forced outage and fresh Start.

* N=1: exactly 21 decoded; actual successful Engine handoffs were
  `[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]`.
* N=5: exactly 21 decoded; actual handoffs were `[0, 5, 10, 15, 20]`, with 16
  sampling skips. Both reached EOF, settled and released the active slot.
* Deliberate Stop at four analyzed frames was partial, before EOF. A subsequent
  N=5 Start completed. An explicit 0.01-second timeout was partial/time_limit.
* A separate 101-frame fixture reached EOF with all indexes 0 through 100.
  The tracked Engine regression additionally executes 101 vehicle, Plate and
  HSV inference calls, retaining bounded evidence beyond the old quotas.
* RTSP fixture N=1 selected `[0, 54, 56, 63, 65, 67, 74]`; N=5 selected
  `[0, 35, 40, 45, 50, 55]`. Overload drops were separate from sampling skips.
  Killing the owned source produced `camera_outage`; a fresh private Start
  selected `[0, 15, 20]` with a new run and fresh service track scope.

Native browser evidence: [RTSP browser metadata](../../vehicle/media/sampling-validation-20261113/native-rtsp/browser-rtsp.json)
and [owned-source/privacy check](../../vehicle/media/sampling-validation-20261113/native-rtsp/owned-rtsp.json).
Both real-model runs used CUDA device 0, rendered a preview that advanced while
native inference was busy, and stopped with all cleanup flags true:

| N | Decoded | Analyzed source indexes | Sampling skips | Overload drops |
| --- | ---: | --- | ---: | ---: |
| 1 | 184 | `[0, 112, 143]` | 0 | 180 |
| 5 | 197 | `[0, 120, 155]` | 157 | 36 |

One selected frame remained unconsumed at Stop in each run; it is neither an
intentional sampling skip nor an overwrite drop. The source, private camera
configuration, decoder, native work and run-owned Redis were released. Both
native trackers started at frame_id=0, tracked=0, lost=0 with distinct scopes.
This was an owned loopback replay, not a real camera. Stopped native RTSP runs
published no DB1 deliveries and correctly reported `no_deliveries`; RTSP DB1
delivery is not established by these Stop checks.

The real MP4 acceptance is separate from the empty fixture:
[redacted DB1 and native runtime evidence](../../vehicle/media/sampling-validation-20261113/real-db1-final.json).
Run `7c4521cb-f403-48b6-aa62-a57cfdf8337c` uploaded a 21-frame excerpt of the
known Tunisian MP4 through the full-stack same-origin API. It analyzed actual
indexes 0 through 20, reached EOF, settled, released its slot and retained its
preview and two track results in console history. Manufacturer calls were zero.
Vehicle inference used the actual CUDA model. Processing exceeded the former
180-second demo window without truncation.

Two restricted application PostgreSQL connections, distinct from each writer,
verified two vehicle facts, two Colour facts, one Plate fact, two acceptance
receipts and five row receipts across fresh disposable fixture databases.
Each exact Redis entry was pending during that verification, then returned
ACK=1. Final pending and lag were zero. Both disposable databases and roles
were removed. The authorized existing PostgreSQL 16 listener on loopback 5433
and shared/system Redis were not reconfigured or stopped. Raw plate readings,
camera URIs, credentials and footage are excluded from this report.

An initial native MP4 attempt exposed the installed ByteTrack attribute
`max_frames_lost` rather than `max_time_lost`. That attempt failed truthfully
and cleaned up; compatibility was fixed, covered against the installed tracker,
and the successful native MP4/RTSP runs above used the correction.

## Validation commands

Commands below were run from the repository root. Logs and private metadata
are in `vehicle/media/sampling-validation-20261113/`. Python is 3.11.

```bash
export PYTHONDONTWRITEBYTECODE=1
export PATH=/home/intern/part-time/mohamed/VehicleRec/.runtime/tooling/node-v22.16.0-linux-x64/bin:$PATH

/home/intern/.venv/bin/python vehicle/manage.py test api.test_console api.test_console_lifecycle api.test_console_stack api.test_console_crops api.test_console_resources --noinput
# 23 passed

/home/intern/.venv/bin/python -m unittest -v vehicle.common.tests.test_mp4_colour_plate vehicle.common.tests.test_real_video vehicle.common.tests.test_rtsp_input vehicle.common.tests.test_console_projection vehicle.common.tests.test_console_crops
# 70 passed at that checkpoint; receipt/early-decode regressions added afterward

/home/intern/.venv/bin/python -m unittest -v vehicle.common.tests.test_real_video vehicle.common.tests.test_console_sampling
# 12 passed, including early decoder failure and paired decoder capture times

/home/intern/.venv/bin/python -m unittest -v vehicle.common.tests.test_mp4_colour_plate.CombinedVideoTests.test_console_profile_processes_101_frames_with_bounded_admitted_evidence vehicle.common.tests.test_console_sampling
# 3 passed at the checkpoint before the additional capture-time regression

/home/intern/.venv/bin/python -B - <<'PY'
import os, subprocess, sys
from vehicle.common.console_stack import configure
configure()
os.environ['DB1_P4_EXISTING_SERVER']='1'
subprocess.run([sys.executable,'-B','-m','unittest','-v',
    'vehicle.common.tests.test_console_db1','vehicle.common.tests.test_db1_consumer'],check=True)
PY
# 11 passed against disposable PostgreSQL fixtures

node --test frontend/tests/*.mjs
# 7 passed
(cd frontend && npm run lint && npm run build)
/home/intern/.venv/bin/python vehicle/manage.py check
bash -n scripts/run_vehicle_console.sh
git diff --check
```

Exact-index acceptance server and command (isolated run root, console-only by
explicit choice; no full-stack downgrade):

```bash
WAYCON_CONSOLE_TEST_PROFILE=empty WAYCON_CONSOLE_TEST_DELAY=0.08 \
 T1_CONSOLE_DB1=console-only \
 T1_CONSOLE_RUN_ROOT="$PWD/vehicle/media/sampling-validation-20261113/fixture-runs" \
 /home/intern/.venv/bin/python vehicle/manage.py runserver 127.0.0.1:8003 --noreload

/home/intern/.venv/bin/python scripts/console_sampling_acceptance.py \
 --run-root vehicle/media/sampling-validation-20261113/fixture-runs \
 --output vehicle/media/sampling-validation-20261113
```

Real-model MP4 acceptance used this command after privately preparing exactly
21 frames from the existing known clip (no changes to the original):

```bash
/home/intern/.venv/bin/python scripts/console_sampling_db1_acceptance.py \
 --video vehicle/media/sampling-validation-20261113/real-21.mp4 \
 --output vehicle/media/sampling-validation-20261113/real-db1-final.json

/home/intern/.venv/bin/python scripts/console_owned_rtsp_check.py \
 vehicle/media/sampling-validation-20261113/native-rtsp
```

The owned-source coordinator supplies its private URI and inspected browser
runtime to `scripts/console_rtsp_acceptance.cjs`; neither command exposes a URI.
The read-only UI audit below verifies defaults, invalid inputs, values above
eight, per-mode settings, empty timeout, manufacturer off, retained real MP4
history/preview and separate commit/ACK columns without starting a run:

```bash
CONSOLE_TEST_PLAYWRIGHT=/home/intern/part-time/mohamed/VehicleRec/.runtime/browser/node_modules/playwright \
 CONSOLE_TEST_CHROMIUM=/home/intern/part-time/mohamed/VehicleRec/.runtime/browser/browsers/chromium_headless_shell-1161/chrome-linux/headless_shell \
 CONSOLE_TEST_OUTPUT=vehicle/media/sampling-validation-20261113/browser-ui.json \
 node scripts/console_sampling_ui.cjs
```

## Files changed for this task

Existing files containing earlier work were edited in place; unrelated dirty
files and history were preserved. The full launcher and DB1 consumer were not
rewritten.

* UI/documentation: `frontend/src/App.jsx`, `frontend/src/frameSampling.js`,
  `frontend/src/runLifecycle.js`, `frontend/tests/frameSampling.test.mjs`,
  `frontend/tests/runLifecycle.test.mjs`, `frontend/README.md`, this report.
* Console/API: `vehicle/api/console_views.py`, `vehicle/api/test_console.py`,
  `vehicle/common/console_runs.py`, `vehicle/common/console_sampling.py`,
  `vehicle/common/console_crops.py`, `scripts/vehicle_console_worker.py`.
* Runtime: `vehicle/common/frame_receipt.py`,
  `vehicle/common/real_video/input.py`, `vehicle/common/real_video/local.py`,
  `vehicle/common/real_video/plate_local.py`,
  `vehicle/common/real_video/latest_rtsp.py`,
  `vehicle/common/real_video/engine.py`,
  `vehicle/common/real_video/plate_engine.py`.
* Tests/acceptance: `vehicle/common/tests/test_real_video.py`,
  `vehicle/common/tests/test_mp4_colour_plate.py`,
  `vehicle/common/tests/test_console_sampling.py`,
  `vehicle/common/tests/console_empty_runtime.py`,
  `scripts/console_sampling_acceptance.py`,
  `scripts/console_sampling_db1_acceptance.py`,
  `scripts/console_sampling_ui.cjs`, `scripts/console_rtsp_acceptance.cjs`,
  `scripts/console_owned_rtsp_check.py`, `scripts/console_proxy_browser.cjs`.

## Launch and retained demo

```bash
cd /home/intern/part-time/aziz/pipeline
bash scripts/run_vehicle_console.sh
```

Browser: **http://127.0.0.1:5173/**. Windows PowerShell tunnel:

```powershell
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:5173:127.0.0.1:5173 intern@Analytic
```

The working full launcher is left running with no active input. Startup opens
no camera/video; rerunning the command reuses its services. The existing MP4
and history remain available. Only the isolated fixture server and owned test
sources are retired. No production writes, commits or pushes were made.

The final rerun printed the same browser URL and reused the existing services
without starting input. The isolated port-8003 server is closed. A separate
read-only maintenance query found zero disposable fixture databases and roles
remaining. [Final handoff checks](../../vehicle/media/sampling-validation-20261113/handoff.json).
