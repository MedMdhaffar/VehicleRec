# Mohamed LPR phase 4: isolated Redis Streams exchange

Implemented 2026-11-08. Native Redis with injected results **passes**. The sole
CUDA MP4 command **failed before recognition** because the CUDA interpreter has
no Python `redis` package. Full CUDA-over-Redis validation remains blocked by
that dependency; there is no real-model decision/parity claim for this phase.
The independent transport, recovery and prior-phase tests pass as detailed below.

T1 v2 remains the default. No existing repository file was edited, including
Phase 1–3, current request/admission/recovery code, the private-plate route,
Aggregator or DB1 consumer. No PostgreSQL connection/write, catalogue change,
RTSP, commit or push occurred. New results are review evidence, never DB1 records.

## Starting state and changes

Read the Phase 1–3 reports, pure policy/tests, v3 contract/harness, real Engine
adapter and existing T1 fenced journal, fair consumer, admission and ownership
code. Mohamed's read-only checkout remains clean at
`f64ac4cc61db4a3531e041cc2d7a6a877b4a4f16`. Its active reference is the Django
`vehicle/license_plate_service/run_video_pipeline/` tree. There is no source drift.

The final-review ZIP SHA256 remains
`b2c9da439afabe55346dd38066f895760296a703afdf0a9e55d1ea01f91f5589`.
ZIP CRC and all **270** manifested workspace files match before and after.
Existing dirty Git changes and untracked deliveries predate this patch; they
are recorded rather than replaced. All starting non-cache files under
`vehicle/common`, `contracts`, `docs` and `scripts` (253 files) remain byte-identical.

Seven new repository files:

| File | Purpose |
|---|---|
| `vehicle/common/mohamed_lpr_stream_codec.py` | Closed bounded scalar envelopes, logical identity/scope, review terminal and opaque public crop IDs. |
| `vehicle/common/mohamed_lpr_stream_policy.py` | Replayable Plate reducer using the unchanged Phase 1/2 rules and a separate in-memory comparison helper. |
| `vehicle/common/mohamed_lpr_streams.py` | Isolated fenced journals, Plate worker, same-live-Engine peer, native tickets and settlement/cleanup. Includes explicit Plate-worker CLI. |
| `vehicle/common/mohamed_lpr_redis_runtime.py` | Transport preflight and owned Unix-socket Redis with private temporary AOF storage. |
| `vehicle/common/tests/test_mohamed_lpr_streams.py` | 24 native Redis test methods, injected scalar/native doubles and real worker kill boundaries. |
| `scripts/vehicle_mohamed_lpr_redis_mp4.py` | Explicit bounded opt-in CUDA/MP4 review driver, one decoder and scalar-only parity replay. |
| `docs/t1/mohamed_lpr_phase4.md` | This report and recovery/rollout limits. |

Evidence is outside the checkout at
`/home/intern/part-time/aziz/mohamed-lpr-phase4-validation-20261108/`.
`starting-git-status.txt` and `baseline.json` record the starting state;
`commands.json`, command JSONs and private logs record argv/results;
`final-verification.json` records preservation and final hashes.

## Versioned exchange and ownership

Envelope version: `vehicle-mohamed-lpr-streams-v3`. Inner requests/results retain
the unchanged `vehicle-mohamed-lpr-v3` contract and Phase 1 policy version. The
closed executable transport specification is `Codec.validate/read`; it does not
replace or reinterpret the existing v2 schemas. Keys have the isolated prefix
`<namespace>:{mohamed-lpr-v3.<org_uuid>}:`. Trusted scope pins org, run, camera and
the live Engine boot ID. Hashes identify content; they are not authentication.

| Stream suffix | Producer → consumer | Contents |
|---|---|---|
| `lpr.engine.crops.v3` | Engine → Plate | Exact scalar crop/frame/vehicle/track/generation binding, explicit Tunis mode and thresholds. |
| `lpr.plate.requests.v3` | Plate → Engine | One follow-up request, or the two independent regional siblings. |
| `lpr.engine.executions.v3` | Engine → Plate | Exact matching request(s), retired execution outcome, actual provenance and scalar observations. |
| `lpr.plate.seals.v3` | Plate → Engine | Plate's immutable proposal: crop, ordered operation/result hashes and decision hash. |
| `lpr.engine.settled.v3` | Engine → Plate | Exact proposal echoed only after Engine retirement/set validation. |
| `lpr.plate.results.v3` | Plate → review / Engine cleanup | Sealed Phase 1 decision plus the same binding/proofs. **No DB1 format or publication.** |
| `lpr.plate.diagnostics.v3` | Plate → review | Bounded code, source stream and rejected-input hash. |
| `lpr.engine.diagnostics.v3` | Engine → review | Separate diagnostic writer, with the same bounded shape. |

Consumer groups are `mohamed-plate-v3` and `mohamed-engine-v3`; no v2 group or
stream is used. A single `json` field carries each message. Closed shapes,
bounded strict JSON, full content hashes and configured scope are checked before
reduction. Maximum wire size is 262,144 bytes; batches contain at most two
operations. Message IDs include scope/kind/session/generation and batch identity
where applicable. Retries retain logical identity and exact payload checksums.

Plate alone plans follow-ups and computes/seals decisions. It replays a bounded
accepted-event log through Phase 2 `PlateSession` and a scalar retirement proof
ledger. Engine owns pixels, native execution, artifact/index verification and
actual retirement. The Engine's proof ledger never interprets number/category
rules. Category OCR failure fallback, explicit NT precedence, conditional DINO,
all four number paths, missing/failed/invalid distinctions and score semantics
remain those documented in Phase 1. Category success cannot accept an invalid
number. Both Tunis modes are explicit; no live mode default is selected.

Registration raw text, normalized registration, display fields and DINO cosine
scores remain distinct. The private Redis instance necessarily carries raw OCR
as scalar evidence; shared summaries never export those message bodies, readings,
crop bytes, footage or credentials. No pixels, paths to pixels, or private crop
handles cross Redis. Public crop/candidate IDs are opaque hashes. The transport
uses a stable vehicle/plate association segment across better-frame generations;
the exact original per-frame association, coordinates and paired receipt remain
in Engine. Frame/crop hashes still bind each observation individually. This
bridge leaves the Phase 3 local identity behavior unchanged.

An important serialization adaptation: local v3 operation IDs distinguish JSON
`1.0` from `1`, whereas the existing T1 canonical serializer folds them together.
The transport preserves original numeric types after bounded validation and
stores v3 state as a canonical JSON string inside the existing journal wrapper.
The native score/threshold identity test verifies exact parity across recovery.

## Recovery and settlement

The new journal subclass reuses the existing Lua fencing, state/intent prepare,
fixed-ID XADD reconciliation, duplicate ACK and output-before-ACK scripts without
editing them. One pending intent per role/partition prevents later outputs from
overtaking recovery. State, dedupe progress and exact output intent are prepared
together; publication is reconciled at its fixed ID; ACK follows verification
that every required output is recoverable. A lost ACK reply re-verifies and
re-ACKs. Old lease owners cannot publish or ACK under a replacement's fence.
Regional siblings share one atomic output batch to fit the existing one-output
repair profile, while their independent results may be ordered either way.

Engine validates a complete request batch in a probe ledger before admission or
native-intent mutation: scope, vehicle/association, known crop/frame/generation,
operation hash/sequence, policy, exact dependency IDs/result hashes and regions.
Plate likewise rejects wrongly bound execution evidence without changing valid
policy state. Conflicting evidence for an already bound operation blocks an
unfinished generation; malformed/unrequested evidence is diagnosed. Terminal
decision/state is immutable under late, stale or conflicting inputs.

A fenced `may-start` intent precedes native execution. Inputs remain pending
until retired results and outputs are recoverable. Live tickets distinguish
scheduled, entered and retired work; the Phase 3 adapter retains results and any
active invocation/crop lease. Completed replay returns retained results, never a
second call. A superseded operation known never to have started is explicitly
retired as `missing/generation_superseded_before_start`. Started old-generation
work must actually retire before sealing/cleanup. New evidence cannot authorize
rerunning an operation whose start/outcome is uncertain.

| Failure boundary | Supported behavior / limit |
|---|---|
| Plate worker replaced | Rebuild from its accepted scalar log, reclaim PEL, repair exact output/ACK; no pixels needed. Eight actual subprocess kills test this. |
| Engine peer replaced / transport reconnect | Requires the **same live Engine object**, crops, native tickets and adapter caches. Retained results reconcile publication without inference retry. |
| May have entered, but no result or active retirement proof | Keep PEL/crop and refuse blind rerun. The injected gap test proves zero extra native calls. |
| Native returned but retirement not acknowledged | No result publication, seal or pixel release; keep the native lane/crop charged until explicit retirement. |
| Engine process lost | **No crop recovery claim.** A permanent owner marker refuses a new Engine in an accepted namespace; use a fresh namespace and an explicit future lost-crop policy. Redis cannot reconstruct pixels or a CUDA invocation. |
| Redis service restart/failover | Not exercised here. The owned instance uses AOF/`appendfsync always`; no general failover durability claim is made. |

Plate seals only after all planned work, including earlier-generation admitted
work, has retired. Engine checks the exact operation/result set and echoes the
proposal. Plate then publishes one review result. Engine journals completion
before releasing all retained crop generations; repeated cleanup is idempotent.
Cleanup requires no DB1 write/ACK. Accepted state is bounded, retained and not
trimmed/expired to make room: 512 input events/stream entries, 128 Plate log events,
1 MiB state, eight live sessions and sixteen retained crop handles; capacity
failure retains unfinished intent rather than silently evicting it.

## Native Redis validation

Owned server executable:
`/home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server`.
Python test client: Redis 7.4.0 under `/home/intern/.venv/bin/python`.
Server binary SHA256:
`d33bac2739f92dc7b3de5940ca8349176b810d7fc97af0310cff5183ed906393`.
Every server uses port 0, a mode-0700 private Unix socket/directory,
`appendonly yes`, `appendfsync always`, `save ""`. Only the owned PID is stopped;
its temporary directory/AOF is removed. No shared server is contacted/flushed.

**24 tests passed, no skips**, including:

- All four categories in both explicit modes, exact canonical full-decision
  equality with an independent Phase 2 in-memory replay, operation counts,
  zero pending entries and exactly-once crop release.
- Expected successful per-crop recognition operations: NT 2; Idara 3; Shorta 3;
  legacy Tunis 4; marker-first Tunis 5. Each role executes once. DINO is skipped
  for explicit NT; marker-first never schedules whole registration OCR.
- Invalid numbers for each successfully classified category remain unresolved;
  Arabic execution failure follows the pinned visual fallback.
- Physical duplicate requests/results/offers/seals/settlements/review results,
  reversed regional siblings, execution delivered before its offer, stale
  better-frame generations and contradictory retries before/after sealing.
- Correctly hashed but wrong crop, vehicle, generation and dependency messages;
  no wrong native call or poisoning of valid Plate state.
- Worker replacement before/after a native call, uncertainty at the entered
  marker, unretired native return, premature seal, stale-owner fencing, six
  lost-publication/ACK-reply variants and eight real Plate-process kill boundaries.
- v2/DB1 key isolation, private-handle exclusion, missing-client preflight,
  operation settlement and owned Redis/AOF/crop cleanup.

The final native run takes 20.564 seconds inside unittest. Earlier development
failures found a handle-test substring false positive, a shared diagnostic stream
collision between fixed-ID writers and an incorrect fencing-test assumption
about empty read-only repair. They were corrected; separate diagnostic streams
and explicit fenced PUBLISH/FINISH assertions are now tested. Earlier logs are
retained separately and are not represented as successful final runs.

## Sole CUDA MP4 attempt and exact blocker

The command ran **after the 22-test native Redis suite and affected v2 regressions
passed**. It used the audited clip once, explicit `legacy_whole`, four frames,
eight crops, 240 seconds admission, 8 MiB per crop and 32 MiB retained crop bytes.
Only one decoder was opened. No second decoding/inference attempt was made.
The final 24-test suite additionally verifies the later preflight/binding guards.

The unchanged artifact lock SHA256 is
`b6ba1ed9406c34162fa54d79bedc58c24d37ff5931d0fd071a668b389ecf661f`.
Its full source/vendor/export/config/dictionary/reference verification passed.
Clip SHA256:
`36067a473c1afd4d4f35d63224a5f0f110213be7bc1c8f75f80adbe23caaffa2`.
No model/reference assets were copied or changed. Key fingerprints and complete
lock material remain in [the Phase 3 report](mohamed_lpr_phase3.md) and
`vehicle/common/mohamed_lpr_assets.json`.
An independent post-run artifact check again verified every locked file, all
28 source code entries, vendor code and eight references, with zero model calls
or decoder opens; `artifact-verification.json` retains all individual file hashes.

Approved CUDA preflight passed: Python 3.11.15, Torch 2.11.0+cu128, Paddle GPU
3.3.1/cu118 and ONNX CUDA on NVIDIA A100-SXM4-80GB MIG 1g.10gb. Arabic PP-OCRv5,
DINOv3 R2, generic registration PP-OCRv6 and matching marker initialized; this is
not a fine-tuned registration export claim. The reference index identity is
`approved-dinov3-r2`, SHA256
`365ec93c88c9673f2cf3634eca42458e507aed75318e5838609e77bec16a2ca4`.
There was no CPU/provider fallback or runtime model download.

**Observed command result: exit 1, status `failed`, ModuleNotFoundError.**
Independent inspection confirmed missing module **`redis`** in
`/home/intern/miniforge3/envs/cuda-dev/bin/python`. The error arose on entering
the owned Redis helper, after decoding but before any recognition request.
Transport client preflight now runs before assets/models/decoder; a zero-model,
zero-decoder check reports `missing_runtime_package_redis`. The original run
summary/log is preserved, not rewritten as a successful or predecode-blocked run.
No environment package installation or second MP4 run was performed.

| Actual execution / cleanup | Result |
|---|---|
| Decoder | One open, four frames, 1920×1080 at 25 FPS; frame budget stopped admission. |
| Vehicle detector | 4 CUDA calls, total 0.766 s. |
| ONNX plate detector | 4 CUDA calls, total 0.522 s. |
| Same-frame associations / selected crops | 4 associated observations; 1 selected crop. |
| Arabic, DINO, registration, marker, regional recognition | **0 calls**, despite model initialization. |
| Decisions / categories | **No decisions**, no real category coverage; accepted 0, unresolved 0, recognition errors 0. These zeros are not completion evidence. |
| Redis scalar replay/parity | **Not reached**. No captured recognition evidence exists to compare. |
| Streams / pending / journal intents | **Not created**, so pending-entry counts are unavailable rather than claimed zero. |
| Native retirement / crop cleanup | 0 unretired operations; 1 allocation released once; 0 remaining allocations/handles; decoder, models and trackers closed. |
| Owned Redis | Missing-client failure preceded starting a server or creating its private AOF directory. |
| Cleanup / DB1 / external provider | 0 cleanup errors, 0 DB1 records/publications, 0 provider calls. |

Driver elapsed 10.395 s; command wall time 12.976 s. These partial-run timings
are not an end-to-end benchmark. The tested Redis client must be supplied in the
approved CUDA environment before a subsequent bounded smoke can finish real-model
Redis/in-memory parity validation. Redis synthetic parity passed independently;
it does not substitute for the blocked real-model test.

## Exact commands and results

Working directory: `/home/intern/part-time/aziz/pipeline`. Each recorded command
used `PYTHONDONTWRITEBYTECODE=1` and
`VEHICLE_REDIS_SERVER=/home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server`.
`commands.json` retains exact argv, cwd, environment, exit code, duration and log.

```bash
/home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_mohamed_lpr_streams -v
/home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_mohamed_plate_policy vehicle.common.tests.test_mohamed_lpr_harness vehicle.common.tests.test_mohamed_lpr_engine -v
/home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_plate_requests_redis -v
/home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_plate_metadata vehicle.common.tests.test_plate_requests vehicle.common.tests.test_engine_plate vehicle.common.tests.test_plate_local_executors vehicle.common.tests.test_redis_plate vehicle.common.tests.test_review_handshake vehicle.common.tests.test_mp4_colour_plate vehicle.common.tests.test_mp4_publication_order vehicle.common.tests.test_db1_consumer vehicle.common.tests.test_real_video
/home/intern/.venv/bin/python -m unittest discover -s vehicle/license_plate_service/run_video_pipeline/tests
/home/intern/.venv/bin/python contracts/validate_examples.py
/home/intern/.venv/bin/python contracts/db1/validate_fixtures.py
/home/intern/miniforge3/envs/cuda-dev/bin/python scripts/vehicle_mohamed_lpr_redis_mp4.py --enable-mohamed-lpr-v3 --source-root /home/intern/part-time/mohamed/pipeline --ocr-cache /home/intern/.paddlex/official_models --video /home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/test_video.mp4 --output /home/intern/part-time/aziz/mohamed-lpr-phase4-validation-20261108/mp4-review --redis-server /home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server --tunis-mode legacy_whole --max-frames 4 --max-crops 8 --max-seconds 240
/home/intern/miniforge3/envs/cuda-dev/bin/python /home/intern/part-time/aziz/mohamed-lpr-phase4-validation-20261108/transport_preflight.py
/home/intern/.venv/bin/python /home/intern/part-time/aziz/mohamed-lpr-phase4-validation-20261108/verify_artifacts.py
/home/intern/.venv/bin/python /home/intern/part-time/aziz/mohamed-lpr-phase4-validation-20261108/verify_final.py
```

| Final check / log | Result |
|---|---|
| `redis-final-24.log` | 24 methods passed, no skips; owned server/AOF removed. |
| `phase123-final.log` | 97 methods passed, no skips; unchanged Phase 1/2/3. |
| `v2-native-redis.log` | 9 methods passed, no skips; private owned server and injected commit/ACK recovery, no PostgreSQL. |
| `affected-t1.log` | 195 methods passed, no skips. |
| `legacy-plate.log` | 57 methods passed; provider timeout is mocked. |
| `event-contracts.log` | Existing schemas/18 valid/24 schema rejects/16 application rejects plus normalization/retry/strict-JSON/vocabulary matrices pass. |
| `db1-contracts.log` | 122 synthetic checks passed; no database. |
| `cuda-redis-mp4.log` | Exit 1; partial detector run failed on missing Python `redis`; detailed above. |
| `cuda-transport-preflight.log` | Exit 0 inspection; reports blocked/missing Redis, zero inference/decoder opens. |
| `artifact-verification.log` | Full post-run lock/source/vendor/reference verification passed, zero inference/decoder opens. |
| `final-verification.log` | Archive/source/starting files and artifact-lock preservation pass. |

## Before any DB1 consumption

First complete the CUDA transport dependency and the bounded real-model parity
smoke; regional/category modes remain synthetic-only unless separately exercised.
The review result is deliberately not a canonical delivery. A later approved
contract must define category vocabulary/catalogue IDs, normalization/display
mapping, actual model registrations/provenance, temporal vehicle/plate identity
and omission/conflict handling. Aggregator must construct and validate canonical
delivery; DB1 consumer remains the sole PostgreSQL writer and ACKs only after
confirmed commit, with separately tested retry/idempotency/commit-death behavior.
No v3 result may be subscribed directly into today's DB1 consumer.

Before rollout also define lost-Engine/crop handling, operational Redis durability
and retention/capacity policy, and explicit mode/feature/version admission. This
patch supports Plate-worker recovery and same-live-Engine reconnect only; it
neither reconstructs lost crops nor changes the verified v2 default or DB1 path.
