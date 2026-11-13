# P1 corrections and P2a foundations — 2026-10-08

## Executed results

Existing VM interpreter: `/home/intern/.venv/bin/python`, Python 3.11,
jsonschema 4.26.0. No packages installed.

1. Complete P1 validator ran before P2a implementation: **109 passed, 0 failed**.
2. Final complete P1 validator after extracting the shared association boundary
   and adding exponent/offset boundary cases: **114 passed, 0 failed**.
3. P2a unittest suite: **18 tests passed** (parameterized cases additionally
   cover every run-end cause and multiple clock faults).
4. Existing Django `vehicle/manage.py check`: **no issues**.
5. `git diff --check`: passed. Supervisor `db1_contract.zip` SHA-256 matched its
   pre-edit baseline; its SQL, checker, seed and diagram were not modified or run.

Commands, from repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python contracts/db1/validate_fixtures.py
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest discover -s vehicle/common/tests -v
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python vehicle/manage.py check
git diff --check
```

P1 now canonicalizes exact bounded Decimal tuples without context rounding,
requires matching plate outcomes, rejects fractional precision above six digits
before parsing, and selects executed unreadable evidence even with null quality.
Measured finite nonnegative quality is preferred; fallback and ties use ordinal
then attempt ID. No-crop omission stays intact. A nullable-score positive row and
an unreadable delivery resolving to no_plate with null text are covered.
Canonical byte representation changed; no persisted outbox is present in this
scope. Limits and selection are documented in `contracts/db1/README.md`.

P2a files: `vehicle/common/__init__.py`, `db1_context.py`, `frame_receipt.py`,
`vehicle_observation.py`, `plate_association.py`, `README.md`, and
`tests/{__init__.py,test_foundations.py}`. The sole association implementation
was extracted from P1 reference helpers into `contracts/db1/association.py`;
P1 and the runtime boundary both reuse it.

## Remaining integration limits and untested behavior

No inference loop is wired. Real decode ordering, shared vehicle tracking across
producers, OCR submission/future failure ledger, agent text completeness,
plate-parent segmentation and colour evidence provenance remain P2b work.
No model/video/GPU/RTSP execution, classifier/colour numerical change, API test
suite requiring a database, database connection, upload, SQL or transport work
was performed. There is no row builder, persistence, caller authorization or
registration lookup. Synthetic provenance is an explicit supplied marker, not
an authentication guarantee. Raw reuse requires a loss/reset/reconnect signal;
unannounced reuse is not detectable. Clock recovery must be externally decided
and construct a new run; the foundations do not claim stability. State is
single-owner/in-memory, with closed snapshots retained and no crash recovery.
The repository-root pure policy package must be included in later deployment.

## Exact P2b wiring entry points

All paths below are relative to `vehicle/`. They were inspected and remain
unchanged. Capture the receipt after each successful decode, before inference.

| Path | Symbols and required wiring |
|---|---|
| `vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/service.py` | `VehicleAnalysisService.process_video` successful `capture.read()` at line 69, then `process_frame` at line 35; construct and propagate one run/receipt owner. `finish` at line 38 must distinguish EOF, limit/stop and process error. |
| `vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/pipeline.py` | `VehiclePipeline.process_frame` line 53 owns vehicle sightings; replace raw-ID lifetime ownership through `VehicleLifecycle`. Preserve `aggregate_vehicle_type` line 13 / `update_track_vehicle_type` line 32 as class authority. `_finalize` line 92 maps tracker loss; `finish` line 85 closes remaining lifetimes. |
| `license_plate_service/service.py` | `run_video_analysis` line 27, `RTSPManager.start` line 88, `_run` line 126, `stop` line 150: accept explicit supplied context; distinguish recoverable reconnect and terminal stop. |
| `license_plate_service/run_video_pipeline/run_video_prototype.py` | `VideoPrototypeRunner.run` line 711 (`capture.read()` line 732), `process_frame` line 618: propagate common receipt and same-frame vehicle candidates. `TrackState.consider` line 193 and `RecognitionAttempt` line 150 retain crop receipt; `_submit` line 578 records operation before dispatch; `_recognize` line 529 and `_drain` line 600 preserve actual executed-model evidence and failed futures; `finalize_track` line 611 / `finish` line 687 close segments. |
| `license_plate_service/run_video_pipeline/prototype_pipeline.py` | `PrototypePlatePipeline.process_crop` line 47 carries selected crop receipt and actual route rather than processing-completion time. `schemas.py:UnifiedPlateResult` line 64 retains evidence. Agent response completeness remains separate P2b work. |
| `license_plate_service/run_rtsp_pipeline/run_rtsp_prototype.py` | `RTSPFrameReader.read` line 261, `run_rtsp` line 412 (successful read line 491), generation transition line 494 / `_build_live_runner` line 307: close old lifetimes, advance epoch, retain context on recoverable reconnect. `_retire_stale_tracks` line 78 maps loss; terminal paths close the run. |
| `vehicle_color_service/service.py` | `run_color_analysis` line 7 delegates to colour `main`; receive explicit context at this boundary. |
| `vehicle_color_service/vehicle_color_service/detector.py` | `main` line 304 (`capture.read()` line 322), `ColourPipeline.process_frame` line 223: consume shared receipt/vehicle identity. `_observe` line 198 retains valid-sample and best-crop receipt; `_fallback_if_needed` line 162 retains actual agent-submitted crop/route; `finalize_all` line 275 closes evidence without changing colour algorithms. |

P2b must install a fault handler that calls `VehicleLifecycle.clock_fault()` even
when no later observation arrives. Camera version changes, EOF, configured
limits, explicit reset, terminal outage and process errors need explicit mapped
boundaries. The duplicate `services/` copies are not the inspected Django
`vehicle/` execution paths and were not changed.

This is staged P2a work authorized independently of the plan's later integration
gate. T1 remains a review candidate; no freeze, commit or push was performed.
