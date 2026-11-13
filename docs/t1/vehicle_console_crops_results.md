# Vehicle Console track crops — 2026-11-13

The console at http://127.0.0.1:5173/ now shows run-scoped vehicle and selected
plate thumbnails with zero-based source frame numbers. It retains the exact
manufacturer-runtime input when available and labels vehicle-observation
fallbacks separately. No DB1 schema, catalogue, canonical bundle or recognition
policy was changed. No camera, production database, commit or push was used.

## Inspecting the last completed run first

The latest completed MP4 at the start of this task was
`37e9b02b-e16c-47c8-b479-157cd3161541`: eight frames, 15 vehicle tracks,
manufacturer recognition enabled. It retained:

- The uploaded `input.mp4` and one full-frame `preview.jpg`.
- A manufacturer SQLite journal with 14 crop bindings: original frame, bbox,
  shape, original-frame hash and crop-pixel hash. Crop image files were absent.
- Fifteen canonical deliveries with Plate provenance and omission reasons.
  Every Plate omission was `unassociated_crop`; none had a selected attempt.

Explicit offline recovery decoded only the retained MP4 frames needed by those
bindings, checked original-frame and cropped-BGR hashes, and retained 14
lossless PNGs under that same run. No model, detector, OCR or tracker was rerun.
All 14 served PNGs were decoded and checked against their original manufacturer
binding hashes and frame indices. One track has no recoverable binding; its
image remains **Crop unavailable** with source frame unknown. No crop was
invented for it. Select this run in Run history to inspect it.

The historical browser check loaded 14 images across 15 rows, displayed one
unavailable placeholder and showed **Not associated** on all 15 plate rows.
These are not reported as “No plate detected”. The original MP4, manufacturer
journal and canonical deliveries were not rewritten; recovery added display
artifacts only.

## New-run capture and evidence

Console-only wrappers retain the first vehicle observation and intercept the
exact crop argument at the manufacturer runtime call. A later observation does
not replace the chosen manufacturer crop. The same crop is stored losslessly
without resizing or drawing overlays. Manufacturer runtime/policy source and
its at-most-once ledger remain unchanged.

Plate candidate pixels are copied before the publisher releases their private
resources. After Plate selection, the selected attempt identifies its candidate;
the retained PNG must match that candidate's pixel hash. Only that selected
image is exposed on the track. Temporary unselected candidate images are
removed by exact filename during normal cleanup.

Completed Plate metadata maps `no_detection` to **No plate detected**,
`unassociated_crop`/`ambiguous_association` to **Not associated**, and
`rejected_crop` to **Crop rejected**. Completed detection/admission evidence is
required. A known associated geometry/size rejection can also survive as a
console diagnostic when no terminal Plate result can be produced. An admitted
crop still awaiting selection does not inherit an earlier rejection reason.
All other missing/incomplete evidence remains **Unknown**.

Images are scoped by run UUID, track ID and `vehicle`/`plate` kind. The API
verifies that the track belongs to that run and serves only the deterministic
PNG path from the run-local manifest. Missing files, unrelated kinds and
symlinks cannot borrow another run's image or expose configuration/delivery
files. Headers are `image/png`, `no-store`, and `nosniff`. The frontend also
checks that each crop URL matches its displayed run, track and kind.

## Real MP4 and browser verification

The known-good `vehicle/license_plate_service/run_video_pipeline/test_video.mp4`
was uploaded through Chromium and the existing port-5173 Vite proxy, with
manufacturer recognition off and the normal four-frame limit.

Final verification run: `830470f0-2a7b-4ff8-9e63-9ffc4e0ef912`.

- Start returned HTTP 202; frames advanced 0 → 1 → 2 → 3 → 4 and finished
  `completed` with all cleanup flags true, worker exited and Start enabled.
- Both tracks retained vehicle-observation crops from source frame 0.
- Track 1 retained the policy-selected Plate crop from source frame 2.
- Track 2 had no selected crop and correctly reported **No plate detected**.
- Chromium loaded the vehicle/plate images through their run-scoped endpoints.
- All three served PNGs were decoded and their pixel hashes verified against
  the recorded crop hashes.
- Type/Colour/Plate continued to work. Manufacturer calls stayed zero and its
  row results stayed Disabled. Commit remained Not configured and Redis DB1 ACK
  Not attempted; local canonical publication is not confused with either state.

No screenshots, footage or raw plate strings are included in the evidence.

## Checks and artifacts

All passed: 16 console API/lifecycle tests; 11 crop/projection tests; 95 existing
Manufacturer, Engine Plate, Plate Metadata and real-video boundary regressions;
five frontend tests; ESLint; Vite production build; the historical browser
check and real MP4 browser checks. The barrier race, Stop, repeat Start,
second-process rejection and RTSP URI privacy remain covered.

Evidence is in
[`vehicle/media/console-crops-validation-20261113`](../../vehicle/media/console-crops-validation-20261113/):
[recovery result](../../vehicle/media/console-crops-validation-20261113/recovery.json),
[historical pixel verification](../../vehicle/media/console-crops-validation-20261113/historical-pixel-hashes.json),
[historical browser](../../vehicle/media/console-crops-validation-20261113/browser-history.json),
[final real MP4](../../vehicle/media/console-crops-validation-20261113/browser-mp4-final.json),
[new crop pixel verification](../../vehicle/media/console-crops-validation-20261113/pixel-hashes-final.json),
[progress](../../vehicle/media/console-crops-validation-20261113/browser-mp4-final.log),
and API/unit/pipeline/frontend logs. Images themselves remain only in their
private run folders.

Commands, from the repository root except the frontend commands:

```bash
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python scripts/recover_console_crops.py 37e9b02b-e16c-47c8-b479-157cd3161541
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python vehicle/manage.py test api.test_console_crops api.test_console api.test_console_lifecycle --verbosity 2
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_console_crops vehicle.common.tests.test_console_projection
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_manufacturer vehicle.common.tests.test_engine_plate vehicle.common.tests.test_plate_metadata vehicle.common.tests.test_real_video

cd frontend
/home/intern/part-time/mohamed/VehicleRec/.runtime/tooling/node-v22.16.0-linux-x64/bin/node --test tests/*.test.mjs
/home/intern/part-time/mohamed/VehicleRec/.runtime/tooling/node-v22.16.0-linux-x64/bin/node node_modules/eslint/bin/eslint.js .
/home/intern/part-time/mohamed/VehicleRec/.runtime/tooling/node-v22.16.0-linux-x64/bin/node node_modules/vite/bin/vite.js build
cd ..

CONSOLE_TEST_PLAYWRIGHT=/home/intern/part-time/mohamed/VehicleRec/.runtime/browser/node_modules/playwright CONSOLE_TEST_CHROMIUM=/home/intern/part-time/mohamed/VehicleRec/.runtime/browser/browsers/chromium_headless_shell-1161/chrome-linux/headless_shell CONSOLE_TEST_RESULT=/home/intern/part-time/aziz/pipeline/vehicle/media/console-crops-validation-20261113/browser-mp4-final.json CONSOLE_PROXY_MODE=mp4 CONSOLE_TEST_CROPS=1 CONSOLE_TEST_MP4=/home/intern/part-time/aziz/pipeline/vehicle/license_plate_service/run_video_pipeline/test_video.mp4 /home/intern/part-time/mohamed/VehicleRec/.runtime/tooling/node-v22.16.0-linux-x64/bin/node scripts/console_proxy_browser.cjs
```

The idle console backend was restarted to load the new API route; Vite remained
running. Both are available on the documented loopback ports 8002/5173.

Changed files: console crop module, status/projection integration, worker display
hooks, API route/view and crop tests; frontend crop components/helper/styles and
tests; explicit recovery utility; extended browser regression; README and this
report. Existing unrelated work was preserved.
