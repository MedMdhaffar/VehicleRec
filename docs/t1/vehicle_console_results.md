# Vehicle Console connected to T1

Completed 2026-11-13. Real MP4/browser verification ran on 2026-11-11; final
artifact and acknowledgment checks ran on 2026-11-13.

| Check | Verdict |
|---|---|
| Console → T1 MP4 execution, preview and joined track results | **PASS** |
| Manufacturer on/off, review marking and truthful delivery states | **PASS** |
| RTSP selection, fresh private URI per Start, Stop and connection failure | **PASS — loopback verification** |
| Authorized live-camera preview/recognition | **NOT VERIFIED — no live camera URI supplied** |
| PostgreSQL commit / Redis DB1 consumer ACK | **Not configured / Not attempted**, displayed independently |

No DB1 catalogue, supervisor SQL, fact schema or production database changes
were made. The frontend is a local React/Vite console backed by Django and the
existing T1 components. No hosted Site deployment, commit or push was performed.

## Behavior

The console offers MP4 and RTSP inputs, Start, Stop, latest backend frame
preview, frame/run status, run history and one row per vehicle lifetime. Preview
uses the same decoded frame as Engine; current runs draw the existing tracker
IDs onto a copy for display. There is no preview decoder or second vehicle
tracker. Existing standalone API routes remain available.

`POST /api/t1/runs/` starts an owned worker. The worker uses the existing
`MP4Input` or `RTSPInput`, receipt clock, `CombinedEngine`, detector/trackers,
Colour metadata, Plate-owned progressive request protocol (`V2Publisher` and
`RequestWorker`), Aggregator and versioned Redis machinery. A separate projection
joins the manufacturer policy decision to that same run/track/frame/crop.
`GET /api/t1/runs/` lists bounded history; run detail, Stop and preview are under
`/api/t1/runs/<run-id>/`. No source URI is part of these route URLs.

One local backend process owns the run launcher. The current shared Engine
profile accepts 1–8 frames per run and a 5–300 second admission window. The UI
shows those limits. It does not promise continuous, unlimited camera operation.
Completed run data remains private under `vehicle/media/t1-console`; an exited
worker is shown as interrupted and is never automatically restarted.

Type, colour and Plate values are taken from canonical T1 results. Manufacturer
policy provides the make without replacing type/colour or generating a score.
The table shows `Unknown` for an unknown brand and `Disabled` for a disabled
branch. Timeouts, malformed output and failures have explicit display states.
An accepted prediction is marked **Review only**. Its model output is not an
accuracy measurement or independently confirmed vehicle identity.

Stop signals the existing input lifecycle, interrupts the owned manufacturer
process, prevents further manufacturer reservations and waits for admitted Plate
work to retire. It does not release an executing crop merely because Stop was
requested. Cleanup state remains visible on the run. This adds optional Stop
hooks to the manufacturer runtime/ledger without changing default opt-in or
at-most-once behavior.

## Delivery states

The top-level status and each vehicle row carry separate fields:

- `postgres_commit`: `not_configured` in this console.
- `redis_ack`: `not_attempted`, referring specifically to the DB1 consumer ACK.
- `review_ack`: `pending`, `acknowledged` or `disabled`, for manufacturer review.

Existing canonical vehicle/Colour/Plate deliveries are produced in private
Redis. Their input handoffs do not establish a PostgreSQL commit. The console
has no configured DB1 consumer and does not claim DB1 ACK or write success.
Canonical payloads stay in private run files for inspection; raw OCR readings
and native VLM text are excluded from public status and the table.

Review ACK is tracked for each exact request: its result must exist and its
original source entry must no longer be pending. A zero pending count alone is
insufficient because later messages can still be unread. Native Redis tests
cover unread input, a published result before ACK, and repair that confirms the
original ACK without another inference. Pure metadata workers release their
lease after each processing turn so Engine model execution between turns cannot
leave an expired lease masquerading as current ownership.

## RTSP privacy

The browser sends the URI only in the Start request body, then clears the
password input. It stores no URI in local/session storage or run history. A new
Start creates a new private camera snapshot through the existing `stage_camera`
interface. The snapshot is mode 0600 inside an owned mode-0700 temporary
directory and is removed at run cleanup. No endpoint appears in worker argv.

The HTTP API returns fixed errors and public status projections. Worker
stdout/stderr are suppressed; diagnostic files contain exception type and stack
locations without exception text or request values. The existing RTSP decoder
suppresses native connection errors. Preview and status use UUID routes with
`Cache-Control: no-store`. Browser tests checked status bodies, request URLs,
backend/frontend logs, storage and snapshot cleanup with ephemeral test
credentials; all checks passed.

Two actual browser Starts used different loopback-only RTSP endpoints on a
closed port. The first was stopped; the second returned `input_unavailable`.
Both snapshots were distinct and removed, and both runs reported complete
resource cleanup. This verifies the connection/Stop/privacy paths, not live
camera frames. A user can enter an authorized camera URI directly in the
console for the separate live-camera check.

## Real MP4 evidence

Evidence directory:
[`vehicle/media/console-validation-20261111`](../../vehicle/media/console-validation-20261111/).
The MP4 was uploaded by the browser, not selected by a backend fixture:
`vehicle/license_plate_service/run_video_pipeline/test_video.mp4`, 2381732 bytes,
SHA-256 `36067a473c1afd4d4f35d63224a5f0f110213be7bc1c8f75f80adbe23caaffa2`.
There were no browser API mocks or demo rows in these runs.

Manufacturer-enabled run: `052c0447-cac6-4f25-a0b2-2a34daf08ba4`.
It completed four frames in 70.691s with four vehicle detector calls, four Plate
detector calls, one actual whole OCR call and one marker call. It produced two
canonical Redis deliveries and two manufacturer review results.

| Vehicle track | Type | Colour | Plate decision | Manufacturer |
|---:|---|---|---|---|
| 1 | car | blue, decided | read, private | Timed out; no make asserted |
| 2 | car | black, decided | omitted: no usable crop | Ford, Review only |

The two manufacturer attempts were bound to their original crops. The first
reached the configured 30-second deadline; its owned child was killed and
reaped. The second completed and returned the accepted Ford identifier. The
Plate read and both Colour decisions still settled despite the manufacturer
timeout. No recognition accuracy claim follows from these results.

`independent-bindings.json` checks each row against the durable manufacturer
reservation/result and its private canonical delivery: complete run identity,
service track, selected frame, request digest, crop digest and canonical
vehicle/colour/Plate fields all match. Browser checks compare the rendered rows
to those backend values, require the Review only marker and separate delivery
columns, confirm actual Plate invocation counters and verify that no percentage
score is displayed. Desktop and 390px mobile screenshots were captured; browser
errors were empty and the mobile document had no horizontal overflow.

The initial browser assertion incorrectly expected every unaccepted make to
render as Unknown. It was corrected to respect the backend timeout outcome.
The same completed run was reinspected; inference was not repeated to repair a
browser assertion. The initial result is retained as `browser-mp4-initial.json`.

A second completed MP4 run with recognition disabled:
`95df7190-a4fd-4b9f-ae2b-710c8525c688`. Both tracks displayed **Disabled**,
manufacturer call count was zero, and the four-frame detector/Plate/Colour
results and two canonical deliveries completed. The preview included the same
tracker IDs used by the table. An earlier disabled validation attempt
(`1bf74755-a835-4fc0-818f-296ef5a1604c`) exposed the metadata lease issue and is
retained as a failed run; it is not counted as a passing execution.

Useful artifacts:

- `browser-mp4.json`, `independent-bindings.json`: enabled run and track checks.
- `browser-controls.json`: completed disabled run and both RTSP checks.
- `console-mp4-desktop.png`, `console-mp4-mobile.png`: enabled results.
- `console-disabled-desktop.png`: disabled results and annotated preview.
- `console-rtsp-controls.png`: RTSP control/error state.
- `starting-state.json`, `final-verification.json`: preservation and file hashes.
- `preflight-20261113.json`: current locked runtime/model assets verified again
  after the pause, without starting inference.

## Validation

All executed tests passed:

| Validation | Result |
|---|---|
| Django console API | 4 tests passed |
| Console projection, Stop, metadata lease and manufacturer regressions | 17 tests passed on Nov 11 |
| Final projection plus exact review ACK regression | 5 tests passed on Nov 13 |
| Affected T1/Input/RTSP/Plate/Aggregator/delivery regressions | 155 tests passed; 2 PostgreSQL fixture-class setups skipped |
| Browser MP4 and control checks | Passed against real backend responses |
| ESLint / Vite production build | Passed |
| Django system check / Python syntax / `git diff --check` | Passed |

The five final projection tests overlap the earlier projection suite; these
counts are not a claim of additional unique coverage. PostgreSQL skips do not
establish real database commit or commit-before-ACK success. Final ACK changes
were validated with owned native Redis and injected recognition evidence;
the real MP4 browser runs above remain the Nov 11 executions.

Commands from the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python vehicle/manage.py test api.test_console -v 2
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_console_projection vehicle.common.tests.test_manufacturer -v
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_real_video vehicle.common.tests.test_mp4_colour_plate vehicle.common.tests.test_mp4_publication_order vehicle.common.tests.test_rtsp_input vehicle.common.tests.test_rtsp_v3 vehicle.common.tests.test_plate_requests vehicle.common.tests.test_colour_aggregator vehicle.common.tests.test_plate_metadata vehicle.common.tests.test_mohamed_lpr_delivery -v
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python vehicle/manage.py check
git diff --check
```

Frontend checks from `frontend/` used the existing Node executable:

```bash
/home/intern/part-time/mohamed/VehicleRec/.runtime/tooling/node-v22.16.0-linux-x64/bin/node node_modules/eslint/bin/eslint.js .
/home/intern/part-time/mohamed/VehicleRec/.runtime/tooling/node-v22.16.0-linux-x64/bin/node node_modules/vite/bin/vite.js build
```

The actual browser drivers are retained in the evidence directory as
`browser-mp4.cjs` and `browser-controls.cjs`; they used the existing Playwright
and Chromium installation. The retained MP4 driver reinspects the original
completed run. It does not silently start another model run.

## Changed files and operation

- `frontend/src/App.jsx`, `App.css`, `frontend/index.html`, `vite.config.js`:
  unified console, controls, preview, history, truthful results/delivery display.
- `frontend/README.md`: startup instructions and backend contract.
- `vehicle/api/console_views.py`, `urls.py`, `test_console.py`: HTTP run API,
  private Start handling, preview and API checks.
- `vehicle/common/console_runs.py`, `console_projection.py`: worker ownership,
  public track projection and exact review ACK observation.
- `scripts/vehicle_console_worker.py`: shared T1 execution and owned cleanup.
- `vehicle/common/manufacturer/engine.py`, `local.py`: optional Stop hooks.
- `vehicle/common/tests/test_console_projection.py`: attribution, Stop and
  delivery-state regressions.
- This report.

The earlier working-tree changes were preserved. Existing shared pipeline,
Plate v3, catalogue, SQL and contract files were not reformatted or replaced.
Only the two manufacturer modules above changed within the prior manufacturer
implementation. No dependencies or models were installed into shared runtimes.

Every completed verification run reported its decoder, models, owned Redis,
private camera snapshot and native children cleaned up. Public run history,
private uploads, canonical deliveries and manufacturer journals are retained
for review. No preview crop files were exported. The temporary browser and web
servers from validation are no longer running after the pause; use the
[startup instructions](../../frontend/README.md) to open the console again.
