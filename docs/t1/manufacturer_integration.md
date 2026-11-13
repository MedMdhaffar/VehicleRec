# Mohamed manufacturer recognition → WAYCON T1

Validated 2026-11-11 on Analytic. This is an explicit opt-in **review capability**.

| Verdict | Result | Evidence |
|---|---|---|
| SOURCE REPRODUCTION | **PASS** | Actual source `ManufacturerService` loaded the BF16 model/projector on CUDA and completed A/B/A, three calls, with native state/image references reset after each call. |
| T1 MANUFACTURER INTEGRATION | **PASS (review-only)** | Actual target Engine, four MP4 frames, one decoder, nine same-lifetime attempts, eight accepted vocabulary labels and one unknown; nine Redis review results, zero pending; duplicate delivery made no additional calls. |
| DB1 DELIVERY | **BLOCKED** | No approved registration binds this exact VLM/runtime/configuration to `dim_model`; no approved mapping binds the recognizer's brand identifiers to catalogue names. No manufacturer row or PostgreSQL commit-before-ACK success is claimed. |

This smoke establishes execution and attribution, **not recognition accuracy**.
“Accepted” means valid JSON and a supported unhedged brand identifier, not a
measured probability or independent visual confirmation. Vehicle model and
confidence remain null. Existing class/colour facts remain byte-identical.

## Starting state and preservation

Target HEAD: `a8950aa9f974bf8e46178e2973b3a2d632a86fce`. The starting tree already
contained modified Plate, manufacturer/classification and Colour runtime files,
tracked bytecode changes, and untracked `vehicle/common`, contracts, docs,
scripts, tests and other artifacts. These are existing work, not all additions
from this task. `starting-state.json` preserves exact Git status and existing
Python fingerprints before edits. Only three pre-existing Python files changed:
`vehicle/common/aggregator.py`, `vehicle/common/real_video/engine.py`, and
`vehicle/common/real_video/plate_engine.py`. No existing Python file disappeared.

Source VehicleRec HEAD: `672d169cca38e3d137c98efb1509685554849312`. Its starting
working tree had changes in the Django launcher, pipeline/service/config/schema/
annotation, README/evaluation notes, deleted `roi.py`, and tracked caches. The
manufacturer adapter audited here is the actual working-tree implementation;
HEAD alone does not identify all launcher/pipeline behavior. Source status and
all existing Python fingerprints were unchanged at completion.
`vlm-validation` exists but is not a Git repository; its inspected files are
identified by hashes. Both requested source paths were present, so no substitute
project was used. `services/` standalone copies in the target were not edited.

Evidence is under
[`vehicle/media/manufacturer-audit-20261111`](../../vehicle/media/manufacturer-audit-20261111/):
starting snapshot, source symbol/hash index, reproduction JSON/log, focused and
regression logs, failed and corrected MP4 summaries, template fingerprint and
preservation check. These are local audit artifacts, not DB1 media registrations.

Read the Phase 5 and RTSP milestone reports, Engine/CombinedEngine, receipt/Input
interfaces, Aggregator, Redis journal/codecs, canonical builders/consumer, DB1
alignment, row schema, fixture documentation and unmodified archived seed SQL.
Phase 5's accepted Plate delivery does not register a manufacturer model. The
RTSP report's old unavailable LPR source path does not prevent reproduction of
these separate, currently available manufacturer sources.

## Actual source execution path

Let **S** mean
`/home/intern/part-time/mohamed/VehicleRec/vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service`.
Line numbers refer to the captured working tree, with hashes in
`source-audit-index.json` and `vehicle/common/manufacturer/assets.json`.

| Stage | Source evidence |
|---|---|
| Django launcher | Parent app `service.py:20` (`run_classification`) enables the recognizer using `recognition_enabled`, serializes jobs, constructs `VehicleAnalysisService`, and closes it in `finally`. This launcher is not imported into T1. |
| Construction | `S/service.py:18` constructs `ManufacturerService`/`TrackRecognizer` only when enabled; `:69` has the source's own video runner. Neither its decoder nor tracker was ported. |
| Vehicle ownership | `S/pipeline.py:26` tracks vehicles and passes frame/bbox/lifetime information to `TrackRecognizer.observe`; `:81` finalizes source profiles. |
| Source crop selection | `S/manufacturer/tracking.py:49` clips bbox, requires >=32px, copies/resizes according to configuration, evaluates crop quality/signatures, and stages a candidate. `:98` executes attempts, including formatting retries. Default configuration is three attempts, initial eight frames, gap eight. These scheduling/consensus choices were not imported. |
| Preprocessing/prompt | `S/manufacturer/service.py:28` accepts uint8 OpenCV BGR, converts to RGB exactly once, encodes PNG, uses the original resolution by default, and supplies `S/manufacturer/prompts.py:3`. |
| Native model call | `S/manufacturer/llama_backend.py:27` requires exactly one CUDA-visible device; `:44` checks binding source fingerprints/GPU support. `:63` constructs `LFM25VLChatHandler` and the LFM2 hybrid model, verifies native vision. `:113` resets native state, passes only the current in-memory PNG, performs chat completion, and clears/reset state in `finally`; `:138` closes resources. |
| Parsing/return | `S/manufacturer/service.py:50` checks native finish reason, rejects truncation and malformed choices, then `S/manufacturer/schemas.py:35` parses the three JSON string keys, normalizes make with `taxonomy.py`, and restricts it to `brands.py`. `RecognitionResult` at `schemas.py:20` returns make/body/colour/reason/raw output, with no car model or confidence field. |

`vlm-validation/test_llamacpp.py:29` is a separate HTTP experiment targeting a
localhost model server. `test_hftranformers.py`, `test_onnx.py`, `test_sglang.py`
and `test_vllm.py` are alternative experiments, not the current VehicleRec
runtime. `test_llamacpp_direct.py:99` imports the current direct adapter, checks
A/B/A/repetition/fresh instances and memory, and explicitly reports
`accuracy_evaluated=False` at line 157. The historical launcher/build commands
are leads; the present pinned checkout, installed binding, model assets and
native image execution were verified directly. No server was launched.

The reviewed manufacturer/validation files contain no labeled brand evaluation,
confusion matrix or held-out accuracy measurement. Image filenames, example
predictions, repeatability and memory/throughput logs do not establish accuracy.
Source body-type and colour output is descriptive VLM output; its custom
silver/grey→white and brown→black mappings are not applied to T1 Colour.

## Artifacts, runtime, template and settings

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| `LFM2.5-VL-450M-BF16.gguf` | 711486496 | `f780a432c7ed103ae35d3f20e76c35acd65777f6cc04fbe646f83efa42989b22` |
| `mmproj-LFM2.5-VL-450m-BF16.gguf` | 190305664 | `899e337967b74c825afeb2f8b441fadd9b3078ec1ec680fc3b3cadbee2af231d` |
| Pinned CUDA binding wheel | 145574874 | `8d535576f31136bf5debaa8a1b9042cfb2cadcce538579ed17ecddcdc7b43629` |
| Target MP4 | 35662657 | `5d3ab09ea6c2fd74e0c0067ffd8edf29d9a78037f6476f972f2d47a287f48077` |
| Existing vehicle detector | 44056921 | `f0574db3428cf12af8c07c40aaa6e042f9cf830f1b22d2ee9ea4dcd370acfc29` |
| Existing vehicle tracker config | 147 | `0629767f781ccae6a81f23c6df04df325912f6d31fd28bc226b5e4f82d41103d` |

The BF16 files in `vlm-validation` and VehicleRec are byte-identical. F16 and
ONNX alternatives exist, but were not selected. Full explicit paths, Python,
all seven packaged native libraries, binding sources, source adapter files and
settings are recorded in `vehicle/common/manufacturer/assets.json`; preflight
checks their hashes. The runtime is the existing isolated
`/home/intern/part-time/mohamed/.llamacpp-eval-ec56af2/env`, Python 3.11,
`llama_cpp_python 0.4.6`, JamePeng binding revision
`ec56af26d9c224e10983ef8b5f40ee34e941ec11`, native llama.cpp revision
`18ab62a8ec5144ae5d2f65e78374ba69652d75dd`. The pinned checkout is clean.
Recorded build evidence uses CUDA 13.2.86, GCC 14.4.0, CMake 4.4.5,
`GGML_CUDA=ON`, MTMD and CUDA architecture 80. No build or package installation
was needed, and the shared `cuda-dev` environment was not changed.

The actual chat handler is `LFM25VLChatHandler` at binding
`llama_cpp/llama_multimodal.py:4275`, including its own LFM2.5 chat template.
The 2429-byte `CHAT_FORMAT` string has SHA-256
`a4313c0527871a3210001ccaa3e53d31c75d633969ac8e08fcbeb1b65c646b80`.
Installed `llama.py` and `llama_multimodal.py` hashes are respectively
`eb3b7c7a7c3fd1ae3c2177ae8558d4285807536a8629ad1991a8aa142af2c805`
and `7cb3d4cabfdef8d7031bcea14a1b310c5c712b60f6a58ca44ecd0a3adadf6401`.
The source prompt file hash is
`7deda9dabbb6bf723d1f4acddd9f9542c497a5feeb9c3833ccab3cfce30997d7`;
the target preserves its prompt value exactly.

Generation: temperature 0, top-p 1, seed 1, maximum 128 tokens, context 4096,
batch 512, microbatch 128, one sequence, checkpoint cache disabled, all model
layers offloaded, vision GPU enabled. Target native CPU thread counts are one.
The guard requires actual LFM2 hybrid architecture and native image chunks;
overflow is rejected before visual prefill rather than dropping the image.
The source reproduction used the source's original thread defaults.

## Opt-in ownership and recovery

`Engine(..., manufacturer=None)` and `CombinedEngine(..., manufacturer=None)`
retain existing defaults. To enable, explicitly construct `LocalRuntime` with an
artifact lock and timeout, then `ManufacturerLedger(enabled=True, runtime=...,
journal=...)`, and supply that ledger to Engine. The new command requires
`--enable-manufacturer`; `MANUFACTURER_ENABLED` does not opt T1 in implicitly.
Existing MP4/RTSP interfaces, T1 v2, opt-in Plate v3, and the old separate Django
classification-service manufacturer guard are unchanged.

The existing Input supplies `DecodedFrame`; the existing detector/tracker and
lifetime allocator supply the same vehicle crop already made for Colour.
Selection is the first observed clipped bbox >=48px on both axes and <=8MiB,
ordered by the original frame processing. There is one reservation per complete
org/camera/run/service-track/raw-track/epoch/lifetime identity. A later crop
cannot replace it or trigger another attempt. The ledger permits at most 16
lifetimes. No pixels, crop path or pixel handle enters SQLite or Redis.

The request binding includes the complete vehicle identity, paired original
receipt, original-frame bbox and crop shape, crop/frame SHA-256 and the runtime
provenance fingerprint. Request ID hashes that entire binding. A private
one-shot CUDA subprocess receives PNG over its own pipe; its result belongs to
that exact invocation. It cannot return an asynchronous result to another
request. Engine retains bounded raw text (<=2048 UTF-8 bytes), execution outcome
and latency; Manufacturer alone interprets it. Aggregator checks the original
same-track class receipt, rejects mismatched/stale lifetimes or altered policy
results, and preserves class/colour authority.

SQLite reserves and FULL-syncs the unique lifetime before entering native work.
Completed results are durable. A restart encountering a reservation without a
result yields `execution_uncertain`; it does not execute again. Identical crop
or message redelivery cannot rerun inference. Unknown, malformed, timed-out,
failed, uncertain and disabled terminals all let existing branches settle.
The Manufacturer policy only accepts a source-vocabulary make, rejects hedged
or make-plus-model labels, never assigns a score, and never treats verbal
certainty as probability. It returns no specific vehicle model.

The owned process has a <=60s configured deadline (30s exercised), CPU time
limit 60s, one native thread, context/sequence/token/image limits, 128 file
descriptors, 4096-byte result-file limit, and core dumps disabled. The parent
kills and waits for only that process group on timeout/interruption. Native
access uses the existing exclusivity gate; CombinedEngine also counts
manufacturer calls in its native API budget. No CPU/provider fallback exists.
The subprocess has an allowlisted environment and disabled networking;
in-process image loading accepts only the current PNG. There is no model server.

`vehicle-manufacturer-review-v1` uses the existing scalar codecs and bounded JSON
validation, with independent `engine.evidence.manufacturer.v1` and
`results.manufacturer.v1` streams and a separate fenced `Journal` scope/group.
It contains closed lifecycle/class/colour evidence and bounded manufacturer
evidence. The review worker replays policy and the existing Aggregator without
models, persists exact intent/result bytes, publishes, then ACKs. Duplicate
logical events do not add results. Changed redelivery is rejected and remains
pending. This Redis review ACK is **not** a DB1 commit acknowledgment. A stalled
review worker does not own or block the original parent/Colour/Plate workers.

`ReviewCodec.join(..., plate=terminal)` also joins an existing typed Plate
terminal through `join_with_plate`; tests exercise a failed manufacturer with
an enabled Plate result. The real MP4 used Plate disabled. No claim of a new
manufacturer-enabled live-camera or real Plate-v3-plus-manufacturer run is made.

## DB1 boundary

Unmodified `rows.v0.schema.json` defines `make_delivery`; `fact_vehicle_make`
allows null make/model confidence, so absence of a confidence score is not the
gap. A future truthful row could carry the accepted make, null vehicle model,
null scores, observations=1, the selected original receipt, and this exact
executed VLM identity. It still requires an approved model registration and
catalogue lookup. It must not borrow a vehicle-model ID or detector/OCR identity.

The supplied development seed registers `vehicle_make`, version 1, task
`classify`; it does not identify the verified LiquidAI VLM, BF16 files, template
and runtime. Its make names are Renault, Peugeot, Toyota and Volkswagen, while
source policy emits lowercase identifiers and supports many additional brands.
The real clip emitted BMW, Audi, Ford, Nissan and Volkswagen identifiers. No
approved mapping or deployed VLM registration was supplied or queried from
production. Seed names are not authorization to add production catalogue rows.

Current canonical `RowBuilder` and `NaturalConsumer` explicitly reject
manufacturer production; their guards, registrations, SQL and contracts were
left intact. Aggregator carries the manufacturer decision separately and builds
the existing parent/Colour/Plate bundle with the current unsupported DB1
manufacturer capability omission. The review result records the exact reason
`missing_approved_vlm_registration_and_brand_catalogue_mapping`. It does not
pretend that a make fact was delivered, or silently promote generic seed keys.

No private PostgreSQL provisioning mode/admin connection was configured.
Connection-free DB1 tests ran, while real PostgreSQL fixture classes explicitly
skipped. No database connection, new registration, fixture make row, receipt,
commit-before-ACK check or production write occurred in this task. Consequently
DB1 DELIVERY remains BLOCKED, independent of successful review transport. A
future authorized projection needs the approved bindings and canonical builder/
consumer support, followed by accepted/missing-registration/retry tests on the
existing disposable fixture; no supervisor SQL change is needed for null scores.

## Real execution and validation

Source reproduction: actual source `ManufacturerService` with explicit BF16
paths performed minivan/sedan/minivan, returning Mazda/Toyota/Mazda. Three
requests completed in 2.157s, 2.793s and 2.707s; initialization 0.868s. Reset and
image-reference cleanup checks passed. These image names are not labeled brand
ground truth.

The first MP4 attempt is retained in `real-mp4/`: nine admitted attempts failed
at worker startup/result delivery, while all nine failure decisions still
settled through Redis and resources cleaned up. Isolated Python ignored the
environment's bytecode flag, and its file-size limit produced a truncated new
adapter cache. The correction is explicit `-B -I`; only the new adapter cache
was removed. Native diagnostics are redirected off the structured result pipe.
A separate source-image worker probe confirmed the fix. Original reservations
were not cleared or retried; corrected execution used a fresh run/journal.

Passing MP4 evidence: `real-mp4-corrected/summary.json`. Run
`manufacturer-da327156666e428981ff7332d6e1bc99`. All nine requests used frame 0, each with its own
track/crop/request digest, and the exact same original-frame digest. The
following table lists service track IDs; raw IDs were also 1–9 in this run.

| Track | Make decision | Attempt latency, seconds |
|---:|---|---:|
| 1 | bmw | 3.457 |
| 2 | audi | 4.019 |
| 3 | ford | 3.109 |
| 4 | nissan | 3.036 |
| 5 | audi | 4.941 |
| 6 | audi | 6.031 |
| 7 | ford | 4.711 |
| 8 | volkswagen | 7.184 |
| 9 | unknown | 4.292 |

Nine actual completed model calls; eight accepted, one unknown, zero malformed,
failed or timed-out outcomes. Four detector calls, four decoded frames, one
decoder open, nine review results, zero pending. Median attempt time 4.292s,
range 3.036–7.184s, sum 40.781s; capture/settlement window 45.020s, overall
57.238s. Attempt latency includes cold initialization, hashing, PNG transport
and child cleanup, not just generation. GPU measurements after each inference
were 1721.125MiB used of 9728MiB on the CUDA-visible A100 MIG instance; they
include detector/other processes and are snapshots, not an isolated VLM peak.
Manufacturer execution provenance: `74c2740d33e457767eb52a6bffb984e322a776a08f76a701f212142fee24c2e0`.
The final manufacturer module/configuration provenance was rechecked against
the successful run and matched. The driver's later cleanup-report hardening
and source-metadata reporting do not change model/crop execution.

Validation logs record:

- 13 focused manufacturer tests, all passed, including two vehicles, original
  Engine crops, unknown/malformed/duplicate JSON, timeout process death,
  disabled/no-crop, crash reservation, stale bindings, preserved Plate/Colour,
  CombinedEngine native serialization, real Redis reply-loss repair and
  conflicting redelivery rejection.
- 278 affected T1/Plate-v3/Input/RTSP/Aggregator/consumer tests passed; one
  PostgreSQL fixture-class setup skipped.
- 49 additional delivery/Engine/cleanup tests passed; PostgreSQL and an
  unconfigured Redis fixture initially skipped. The available Redis binary was
  then explicitly selected: all nine separate Plate request Redis regressions
  passed in 114.167s, with owned-server cleanup verified.
- Nine DB1 connection-free fixture-safety tests passed; three PostgreSQL
  fixture-class setups skipped. These skips do not establish DB1 integration.
- Django system check passed; DB1 contract fixtures: 122 passed, zero failed.
- `git diff --check` passed; all changed/new Python parsed successfully.

The full source/target revision/status, exact MP4 argv, raw artifact fingerprints,
track/crop bindings, result counts, latency and cleanup are in local evidence.
No secrets, camera URI or raw plate readings are printed by the new driver.
No frontend, production writes, commits or pushes were made. Owned CUDA children
exited, the MP4 decoder/detector and SQLite connections closed, and the private
Redis server/socket/AOF directory were removed. Metadata journals and summaries
are intentionally retained for audit and at-most-once recovery; no crop files
were exported.

## Changed files

| Files | Responsibility |
|---|---|
| `vehicle/common/real_video/engine.py` | Optional manufacturer hook on the existing crop and closed scalar evidence; ledger cleanup. |
| `vehicle/common/real_video/plate_engine.py` | Optional hook forwarding and native API serialization/accounting. |
| `vehicle/common/aggregator.py` | Typed same-track manufacturer review alongside the canonical existing bundle. |
| `vehicle/common/manufacturer/{__init__.py,assets.json}` | Explicit capability namespace and audited artifact/runtime lock. |
| `vehicle/common/manufacturer/{llama_backend.py,prompts.py,taxonomy.py,brands.py}` | Small attributed adaptations of the inspected source native adapter/prompt/normalization vocabulary. |
| `vehicle/common/manufacturer/{engine.py,local.py,native_worker.py}` | Durable admission, Engine-owned execution, local process/resource limits. |
| `vehicle/common/manufacturer/{policy.py,streams.py}` | Manufacturer interpretation and versioned fenced scalar review join. |
| `vehicle/common/tests/test_manufacturer.py` | Focused behavior and native Redis coverage. |
| `scripts/vehicle_manufacturer_mp4.py` | Explicit real MP4 review driver. |
| `docs/t1/manufacturer_integration.md` | Audit, boundary verdicts, commands and limitations. |

## Exact commands

All test/verification commands ran from `/home/intern/part-time/aziz/pipeline`.

```bash
export PYTHONDONTWRITEBYTECODE=1
/home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_manufacturer -v
/home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_colour_aggregator vehicle.common.tests.test_real_video vehicle.common.tests.test_mp4_colour_plate vehicle.common.tests.test_mp4_publication_order vehicle.common.tests.test_rtsp_input vehicle.common.tests.test_rtsp_v3 vehicle.common.tests.test_mohamed_lpr_engine vehicle.common.tests.test_mohamed_lpr_harness vehicle.common.tests.test_mohamed_lpr_streams vehicle.common.tests.test_mohamed_plate_policy vehicle.common.tests.test_plate_requests vehicle.common.tests.test_plate_metadata vehicle.common.tests.test_db1_consumer -v
/home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_mohamed_lpr_delivery vehicle.common.tests.test_plate_requests_redis vehicle.common.tests.test_coordinator_cleanup vehicle.common.tests.test_engine_plate -v
VEHICLE_REDIS_SERVER=/home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server /home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_plate_requests_redis -v
/home/intern/.venv/bin/python -m unittest discover -s tests/db1_integration -v
vehicle/.venv/bin/python vehicle/manage.py check
/home/intern/.venv/bin/python contracts/db1/validate_fixtures.py
git diff --check
```

Source reproduction (the small driver is retained in the evidence directory):

```bash
PYTHONDONTWRITEBYTECODE=1 LD_LIBRARY_PATH=/home/intern/part-time/mohamed/.llamacpp-eval-ec56af2/env/lib OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /home/intern/part-time/mohamed/.llamacpp-eval-ec56af2/env/bin/python vehicle/media/manufacturer-audit-20261111/source_reproduce.py
```

Passing MP4 command (all arguments also retained in its summary):

```bash
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python scripts/vehicle_manufacturer_mp4.py --enable-manufacturer --assets vehicle/common/manufacturer/assets.json --video vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/video.mp4 --detector vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/models/vehicle_yolo26.pt --tracker vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/trackers/bytetrack_vehicles.yaml --redis-server /home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server --output vehicle/media/manufacturer-audit-20261111/real-mp4-corrected --max-frames 4 --max-seconds 180 --inference-timeout 30
```

Remaining blockers are the approved DB1 VLM/model registration and brand
catalogue mapping, followed by an explicitly configured disposable PostgreSQL
fixture and the canonical make projection/consumer extension. Recognition
accuracy remains unmeasured; a labeled evaluation is separate work. Runtime and
model paths are host-specific and deliberately fail when missing or altered.
