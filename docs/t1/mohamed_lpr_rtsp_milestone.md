# First opt-in RTSP milestone

Completed 2026-11-09. **Implementation and independent validation: PASS.
Authorized live-camera CUDA recognition: BLOCKED / not attempted.** The user
will enter the camera URI when ready. No live plate write is claimed.

## Starting state and preservation

Read Phase 5, Input/receipt clocks, Engine/Plate lifecycle, the opt-in v3 runners,
Aggregator/DB1 fixture delivery and `db1_alignment.md`. Starting status and 248
scoped file hashes are retained outside the checkout in
`/home/intern/part-time/aziz/waycon-rtsp-milestone-validation-20261108/`.
The directory name records the start of this work; completion was November 9.

Archive SHA256 remains
`b2c9da439afabe55346dd38066f895760296a703afdf0a9e55d1ea01f91f5589`.
Of its 270 reviewed files, 269 still match; the sole existing difference is
Phase 5's optional reader in `db1_consumer.py`, unchanged by this patch.
MP4 Input, existing runners, T1 v2 defaults, Phase 1–5 policy/contract/native
bindings, catalogue contracts and supervisor SQL are unchanged.

Mohamed's reference initially verified clean at
`f64ac4cc61db4a3531e041cc2d7a6a877b4a4f16`; the initial CUDA/artifact/Redis
preflight passed. Later `/home/intern/part-time/mohamed/pipeline/` became
unavailable. Current source/artifact reverification and audited-MP4 replay are
therefore **blocked**, not claimed current passes. No reference files were
edited, restored or copied. Initial and subsequent checks are retained separately.

## Interface and lifecycle

Manual Start uses a **hidden terminal prompt**. `--rtsp-uri-file` is optional
for automation: a URI-only regular file owned by the current user, mode 0600.
The runner never deletes that caller-owned file. A private mode-0700 temporary
directory holds the immutable per-Start decoder configuration. Neither the URI
nor credentials enter argv, summaries, diagnostics or logs; decoder native stderr
is suppressed. Echo fallback is rejected. No camera endpoint is hardcoded.

`stage_camera(uri, directory, camera_id=None, timezone=...)` is the common Python
Start boundary for a future backend. Without an explicit identity it allocates
an opaque source identity for that Start, not a production camera registration.
The backend can use the same `RTSPInput` / `run_connections` interface; no frontend
or HTTP endpoint was added. Switching cameras requires Stop, cleanup, then a
new Start. Editing an external URI file does not hot-switch an existing snapshot.

Input returns the existing `DecodedFrame(receipt, pixels)`. UTC and monotonic
receipt times are sampled together on the host after decoded-frame handoff,
before inference. `media_pts` remains separate decoder presentation time, never
camera capture time. T1 retains its `host_receipt` context and
`host_frame_receipt` delivery-diagnostic labels.

One spawned decoder owns blocking OpenCV/FFmpeg calls. There is one shared frame
slot and no decoded-frame queue/prefetch in the adapter. Bounds include frame
bytes, open/read deadlines, reconnect count, backoff, total admission time,
FFmpeg socket/probe/analysis buffers and Engine/crop budgets. Transport is selected
explicitly (`tcp` by default); there is no transport fallback. Stop interrupts a
stalled decoder, confirms process death and unlinks shared memory. Cleanup first
requests normal exit, then terminates/kills only that owned decoder if needed.
Native GPU work must retire before crops/models can be released; an unknown
retirement blocks reconnect and is reported as retained work rather than rerun.

An observed outage closes the old Input before Engine settlement and records
`camera_outage`. Already-buffered packets can be decoded before the failure is
observed; they remain in the old run. Every reconnect constructs a fresh run ID,
receipt clock, vehicle tracker, plate tracker, v3 Engine scope and Redis namespace.
Camera identity is stable within a Start. Raw tracker-ID or PTS reuse cannot
reuse a vehicle identity. Old receipts/results are rejected; sealed decisions
remain immutable. A host time budget closes as `service_stop`, not camera outage.

Retained crops follow the existing v3 request/execution/retirement protocol.
Plate still schedules and seals; Engine owns pixels and models. Missing usable
crop evidence and interrupted work are explicitly incomplete, never `no_plate`.
Closed parent work is acknowledged only after recoverable review output and any
requested fixture delivery. Incomplete closures are counted separately.

The existing Phase 5 projection/consumer remains opt-in: only supported accepted
legacy-whole Tunis can reach `private` in a fresh disposable database. Other
categories, unresolved reads and unsupported selection shapes stay review-only.
Stream registrations use `source_kind=stream`, `source_media_key=NULL`; the
fixture's unrelated template MP4 row is not linked to the RTSP run.

## Validation

| Check | Observed result |
|---|---|
| New Input / v3 / PG tests | **30 methods passed**, no skips: final **17** Input tests and **13** v3/PG tests. A preceding full 29-method suite also passed. |
| Deterministic stalled readers | Read/open Stop, Stop between decode/admission, read/total deadlines, bounded retries, shared-memory/process cleanup passed. Controlled fake decoder entry is confirmed before stalled-read Stop. |
| Injected reconnect | **2 frames, 1 reconnect, 2 observed outages**, source PTS **100 → 0**, same raw tracker ID, distinct run/vehicle scopes. Both interrupted closures explicitly incomplete. |
| Scalar v3 | All four categories and both Tunis modes; exact memory/Redis decision parity, invalid number despite successful Tunis classification, inference failure, duplicate delivery, delayed old result, immutable seal and retirement tests passed. |
| Real Redis/PG with injected recognition | Vehicle **1**, colour **1**, private plate **1**, acceptance **1**, row receipts **3** visible from a separate restricted connection while Redis pending was **1**. XACK **1**, then pending **0**; owned DB/roles removed. No real-model recognition claim. |
| Phase 1–4 | **121** methods passed. |
| Existing Phase 5 | **22** methods passed with real PG16 fixture configuration, no skips. |
| Affected v2 / Engine / MP4 | **190** methods passed; **15** native Redis regressions passed. |
| Contracts | DB1 fixture checker and event/schema/normalization/retry checks passed. |

Owned VLC RTSP replay was also exercised in the CUDA interpreter with user-site
loading disabled: **11 decoded frames (9 old connection + 2 new), 1 forced
outage, 1 reconnect, 2 retired decoders**, fresh run IDs/frame indices, all shared
memory and owned source processes cleaned. It used generated H.264 color bars,
not unavailable audited footage. This Input-only probe has a separate bounded
32-frame first-connection budget to drain decoder/network buffering and observe
failure; it does not increase the live T1 Engine's 1–8 total-frame limit.
Model calls **0**, decisions **0**, Redis/PG delivery **not exercised by this
probe**. The injected Engine/Redis/PG tests above establish those separate checks.

VLC rejects TCP with RTSP **461 Unsupported transport**. The successful probe
explicitly selects UDP; no automatic fallback was added. Early failed probes
are preserved. The final Input probe imported OpenCV **4.10.0**, with its actual
module/build fingerprints in the summary. Distribution metadata reports
4.13.0.92; the new worker records imported OpenCV identity separately in runtime
provenance rather than treating metadata as proof of imported code.

Current CUDA preflight passed (A100 MIG, Torch CUDA 12.8, Paddle CUDA 11.8,
CUDAExecutionProvider available), and exact temporary Redis **7.4.0** import and
owned Unix-socket reachability passed. No shared environment was changed.
The full runner's automated pre-open gate returned **BLOCKED** with
`approved_source_checkout_unavailable`: **0 decoder opens, 0 frames, 0 model
executions, 0 DB1 rows**, owned Redis and temporary package/configuration removed.
Its outputs were checked for URI/credential leakage. Non-terminal hidden-prompt
invocation also fails closed. **No authorized live-camera smoke occurred.**

Evidence includes `starting-state.json`, `pg-rtsp-final-current.*`,
`pg-rtsp-retirement.*`, `cuda-input-clean/summary.json`,
`input-stop-final.*`,
`current-preflight.json`, `runner-source-gate/summary.json`, per-command argv /
exit / duration JSONs, failed probe directories and `final-verification.json`.
Summaries contain counts, timestamps, identities and hashes, not readings/pixels.

## Commands

From the target checkout, run this in a terminal when ready; it prompts without
echo. Use a fresh output directory for every Start. The approved read-only source
and locked assets must be available before a camera can open.

```bash
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python scripts/vehicle_mohamed_lpr_rtsp_smoke.py \
  --enable-rtsp-v3 \
  --admin-dsn-file /home/intern/.db1-p4-dsn.mLCSWr \
  --cuda-python /home/intern/miniforge3/envs/cuda-dev/bin/python \
  --source-root /home/intern/part-time/mohamed/pipeline \
  --ocr-cache /home/intern/.paddlex/official_models \
  --redis-server /home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server \
  --output /tmp/waycon-rtsp-manual-001 \
  --tunis-mode legacy_whole --rtsp-transport tcp \
  --max-frames 4 --max-crops 4 --max-seconds 90 \
  --open-seconds 5 --read-seconds 3 --reconnects 1
```

For automation add `--rtsp-uri-file /private/uri.txt`; never place a URI value in
the command. Ctrl-C requests Stop. Restart the command and enter the new URI to
switch cameras. The parent allows bounded initialization/cleanup time beyond
admission; it reports and retains an unretired native process instead of killing
it and falsely claiming crop recovery.

Exact test executables/arguments (all with `PYTHONDONTWRITEBYTECODE=1`):

```bash
/home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_rtsp_input vehicle.common.tests.test_rtsp_v3 -v
/home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_rtsp_input -v
/home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_rtsp_v3 -v
/home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_mohamed_plate_policy vehicle.common.tests.test_mohamed_lpr_harness vehicle.common.tests.test_mohamed_lpr_engine vehicle.common.tests.test_mohamed_lpr_streams
/home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_mohamed_lpr_delivery -v
/home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_db1_consumer vehicle.common.tests.test_db1_runner_ack vehicle.common.tests.test_db1_adapter vehicle.common.tests.test_colour_aggregator vehicle.common.tests.test_plate_metadata vehicle.common.tests.test_plate_requests vehicle.common.tests.test_redis_plate vehicle.common.tests.test_review_handshake vehicle.common.tests.test_real_video vehicle.common.tests.test_mp4_colour_plate vehicle.common.tests.test_mp4_publication_order
/home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_plate_requests_redis vehicle.common.tests.test_mp4_colour_plate_redis_integration -v
/home/intern/.venv/bin/python contracts/db1/validate_fixtures.py
/home/intern/.venv/bin/python contracts/validate_examples.py
PYTHONNOUSERSITE=1 /home/intern/miniforge3/envs/cuda-dev/bin/python scripts/vehicle_rtsp_loopback_probe.py --synthetic --rtsp-transport udp --vlc /usr/bin/cvlc --output /home/intern/part-time/aziz/waycon-rtsp-milestone-validation-20261108/cuda-input-clean
```

The retained `check.py` wrapper loads the already-authorized private PG DSN only
for PG-labelled commands, sets existing-server fixture mode and records argv/
exit/duration without credentials. Exact wrapper invocations are in the paired
command JSONs. Tests use the owned Redis executable shown in the runner command.
The Input probe needs no Redis package/model loading. `current_preflight.py`
and `runner_gate.py` retain the separate actual-interpreter/bootstrap checks.

## Files

| File | Change |
|---|---|
| `vehicle/common/real_video/rtsp.py` | Dynamic Start input, hidden prompt/private-file boundary, bounded spawned decoder and frame receipts. |
| `vehicle/common/real_video/rtsp_host.py` | Fresh connection/run scopes, Stop/outage/settlement/reconnect control. |
| `vehicle/common/real_video/rtsp_v3.py` | Existing combined Engine, scalar v3 Redis/parity, retirement and delivery hook. |
| `vehicle/common/real_video/rtsp_delivery.py` | Existing projector/consumer with private fixture handshake and independent commit-before-ACK checks. |
| `vehicle/common/mohamed_lpr_test_database.py` | Only pre-existing file changed: stream fixture run has no unrelated source-media link. File-run behavior retained. |
| `vehicle/common/tests/test_rtsp_input.py`, `test_rtsp_v3.py` | Process/lifecycle/identity/Redis/PG regressions. |
| `scripts/vehicle_mohamed_lpr_rtsp_smoke.py`, `vehicle_mohamed_lpr_rtsp_child.py` | Opt-in manual/automated test host and CUDA worker; no default route change. |
| `scripts/vehicle_rtsp_loopback_probe.py` | Bounded owned source replay, audited MP4 or explicitly generated synthetic input. |
| `docs/t1/mohamed_lpr_rtsp_milestone.md` | This report. |

No production writes, catalogue/supervisor-SQL changes, frontend work, commits
or pushes. Real-camera recognition/accuracy and a live committed plate remain
unverified until the user starts the manual smoke with restored approved assets.
