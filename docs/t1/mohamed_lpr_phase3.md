# Mohamed LPR phase 3: opt-in CUDA Engine bindings

Implemented and validated 2026-11-08 (Africa/Tunis). **The bounded real-model MP4
run passed.** T1 v2 and its existing defaults/delivery remain unchanged. The new
CLI is an explicit local v3 review path, with no Redis publication, DB1 records,
PostgreSQL writes, category mappings, RTSP, provider fallback, commit or push.

## Verified starting state

Source is the active Django tree in `/home/intern/part-time/mohamed/pipeline`,
HEAD `f64ac4cc61db4a3531e041cc2d7a6a877b4a4f16`, clean before/after. The ONNX and
DINO weights match their approved source constants. Tracked Arabic exports,
index, metadata, configuration, marker and source code match the pinned checkout.
The vendor DINO revision and its Python tree are fingerprinted. Registration
exports match all three files in the existing official PP-OCRv6 cache; source
and target marker weights are byte-identical. **No fine-tuning claim is made.**

The approved reference manifest's `images/` directory is absent at its configured
location. All eight exact-hash images were found elsewhere in the same read-only
source checkout and verified; their explicit paths/hashes are locked. The source
classifier loads the existing frozen index without reopening those images.
This adapter does the same. It neither regenerates the index nor replaces its
references. The reference/index category counts remain Idara 3, Shorta 2, Tunis 3.

Target review archive SHA256:
`b2c9da439afabe55346dd38066f895760296a703afdf0a9e55d1ea01f91f5589`.
ZIP CRC and all **270** reviewed workspace files pass. Initial baseline
differences: **zero**. The eight Phase 1/2 files match the preceding verification.
Starting dirty Git state is recorded outside the checkout; existing changes are
preserved. Final verification rehashes the baseline and every starting non-cache
file under `vehicle/common`, `contracts`, `docs/t1` and `scripts`.

Six repository additions:

| File | Responsibility |
|---|---|
| `vehicle/common/mohamed_lpr_assets.json` | Pinned artifact, source, vendor, index/category and exact reference-image fingerprints. No weights or images copied. |
| `vehicle/common/mohamed_lpr_native.py` | Explicit CUDA preflight, artifact verification and model bindings. Lazy module imports; no capture or delivery. |
| `vehicle/common/mohamed_lpr_engine.py` | Bridge from T1-owned crops/receipts to v3; native lane, actual-start/provenance checks, retirement, request/result cache and crop release. |
| `scripts/vehicle_mohamed_lpr_mp4.py` | Explicit opt-in bounded local MP4 driver and sanitized report. Requires a Tunis mode and all admission budgets. |
| `vehicle/common/tests/test_mohamed_lpr_engine.py` | 25 independent synthetic adapter/ownership/artifact/runtime tests. No GPU/model/media requirement. |
| `docs/t1/mohamed_lpr_phase3.md` | This report. |

## Bound execution and ownership

The driver reuses `CombinedEngine`, `VehiclePipeline`, the existing vehicle
YOLO26/ByteTrack binding, `PlateLedger`, same-frame `associate_plate`, crop
geometry/quality, `EngineCrop`, `MP4Input` and `FrameReceiptClock`. Vehicle and
plate tracking counters remain isolated by T1's `TrackerIDs`. The approved ONNX
plate backend replaces plate detection only in this new path. Source tracker,
capture loop, Django API, writer and external fallback are not ported.

Each successfully decoded frame receives one paired original receipt before
inference. Plate detections are already mapped by the approved backend from
640-square letterbox space to original frame coordinates; T1 clips once and
copies original pixels. Association uses that frame's active vehicle boxes.
Ambiguous/unassociated detections remain diagnostics and supply no recognition
crop. Original bbox, exact receipt object, vehicle lifetime, plate tracker epoch,
segment, candidate, crop bytes/hash and frame hash stay bound through admission.

Recognition starts after bounded frame observation/closure, using T1's retained
best crop per associated segment. Better crops may replace unused allocations
before recognition starts; no executing crop can be replaced by the driver.
`bind_crop` verifies parent, association, exact receipt object, shape/bytes/hash
and minimum size. Vehicle lifetime identity and raw plate track identity remain
separate. The scalar frame ID fingerprints the paired receipt; its separate
frame hash fingerprints the original BGR8 frame allocation.

`Harness.advance()` remains the sole follow-up planner. The Engine adapter only
executes retained, admitted, exactly bound requests, copies requested rectangles
from original crop bytes and submits results through unchanged v3 envelopes.
No normalization, category selection, number parsing or final acceptance moves
into Engine. Idempotent request replay returns the cached result without another
model call. The real run also round-tripped every result through the strict v3
codec and replayed the final operation without changing its seal or call counts.

| Stage | Binding and preprocessing |
|---|---|
| Plate detector | Pinned source `onnx_detector.LocalPlateBackend`, exact YOLOv9 t-640 end-to-end ONNX, open-image-models 0.6.0. Confidence .25, native floor .001/NMS .45, application dedup IoU .8, T1 tracking/association padding .18. |
| Arabic category OCR | Local Arabic PP-OCRv5 export. Preserve the source classifier's R2-budget **resized, unpadded** BGR submission, then native recognition. Its raw text/native score reach Plate; Engine never parses NT. |
| DINO R2 | Pinned `DinoV3Encoder`, frozen compatible R2 index, exact whole/left/centre/right preprocessing and normalized concatenated features. Engine returns all eight native reference cosines in index order; Plate selects the category/tie. No threshold, clipping or probability conversion. |
| Whole registration OCR | Existing matching official PP-OCRv6 export/dictionary, original requested BGR region, native unrounded score. Phase 1 retains its own source rounding/rules. |
| Tunis marker | Existing matching target weights; direct 512-square stretch and T1's compatible marker decoder mapping back into original crop coordinates. Explicit CUDA inference at .05 floor. |
| Regional OCR | Shared PP-OCRv6 runtime; exact left/right full-height rectangles requested by Plate, copied from the original crop. This run did not request regions; synthetic tests verify both modes and region lineage. |

Artifact/configuration verification precedes invocation. Actual loaded OCR path,
model name and initialized `PaddleStaticRunner` device/configuration are checked;
Torch parameter devices and DINO compatibility are checked. Effective OCR runner
settings contribute to the actual provenance hash. Wrong artifacts/index/model
placement block execution instead of masquerading as negative NT/failed OCR.
An actual retired Arabic inference failure still permits Phase 1's DINO fallback.

The existing T1 `LocalArtifact`/`NativeCPU` contracts require CPU and only support
private-plate operation kinds. They are preserved. The additive CUDA registry and
v3 provenance bind the additional kinds without weakening those contracts.
The source's legacy absolute detector-config imports are scoped/restored during
loading; DINO helpers use a private namespace, without importing its classifier
or replacing T1's package alias.

The adapter holds the existing process-wide native lane and its crop lease until
the runtime explicitly acknowledges no further pixel use. CUDA synchronization
is required even on exceptions. Returned-but-unretired work emits no v3 result,
cannot seal/release the crop and keeps the lane charged. Deadline expiry stops
new work; it does not cancel or imply native retirement. After a local seal,
retired crop release is idempotent. No release depends on a new DB1 write.

## Runtime and artifact fingerprints

The normal `/home/intern/.venv` imports CPU-only Paddle and lacks
open-image-models. It was used for independent tests, not for model fallback.
The run used `/home/intern/miniforge3/envs/cuda-dev/bin/python` (Python 3.11.15):
Torch 2.11.0+cu128, torchvision 0.26.0+cu128, Paddle GPU 3.3.1 (CUDA 11.8),
PaddleOCR 3.7.0, PaddleX 3.7.2, ONNX Runtime GPU 1.25.1,
open-image-models 0.6.0 and Ultralytics 8.4.46. Actual GPU:
NVIDIA A100-SXM4-80GB MIG 1g.10gb.

OCR predictors resolve to `gpu:0`; DINO, marker and vehicle parameter devices are
`cuda:0`. ONNX resolves with CUDA first. ORT also registers CPU in its provider
list, but the approved backend asserts `session.disable_cpu_ep_fallback=1`,
calls `disable_fallback()` and rejects missing CUDA/incorrect session options.
There is no configured CPU or external-provider retry.

| Artifact | SHA256 |
|---|---|
| `plate` | `c3c1026ca7d0585dd88084d68182dd897113712fa734ae1557ca70174440c076` |
| `dino` | `08c60483bc63c04f533611e34bf70b120eedb7240f469bc16e9e20bf344b941d` |
| `arabic-inference.pdiparams` | `4b4271fd1dd89a40b2056e1a42e58de7c0df2fdcf1a97ca9a8916d7ec45f9143` |
| `arabic-inference.json` | `d43832804d89c0e18d59f8446a3babd0ef60c44bc335efddac3176caf71d50af` |
| `arabic-inference.yml` | `21368419e6c016c31db55d316d59e11c128e1913e6e6fe10287084710043d3a6` |
| `registration-inference.pdiparams` | `1b01c79a914587933f615569e75de54f2e638ebb5d3f3b3c1b38c24ede8c7319` |
| `registration-inference.json` | `0b2e25e990bd072f1bf77d59d67d508bce6c4bd44af6624e0fb27d6da2cd00e8` |
| `registration-inference.yml` | `991b700facf5b50a7de193468207d5f4255b538dde0d312ae3b7c7a9b6873129` |
| `marker` | `f8956bd78992661bf5a88905f355b48168c0e5d643c3f7635795cfc266d11293` |
| `index` | `aed2f0c0add768992716dd1e2457310ebd5edac4c5efc8117f457574ef9d5686` |
| `index-metadata` | `1b11a15ab2fd4d6f93258741ecf868d9d2f2aa8183138d26470339561796a154` |
| `reference-manifest` | `3c6f9515dc38dbd65263a74ccf391d5560660ff583a1eba34aa6b1dad2a2ea11` |
| `vehicle` | `f0574db3428cf12af8c07c40aaa6e042f9cf830f1b22d2ee9ea4dcd370acfc29` |
| `vehicle-tracker` | `0629767f781ccae6a81f23c6df04df325912f6d31fd28bc226b5e4f82d41103d` |
| `plate-tracker` | `2cf1cb6791ed4b60eb380ee4ac589d990bc816e2af8025488f33cadbc4020ea3` |

These individual file hashes, all export/config/dictionary files, source/vendor
fingerprints and verified reference-image hashes are in `mohamed_lpr_assets.json`.
`artifact-identities.json` outside the checkout retains complete configuration
material and model-level artifact/configuration/preprocessing/runtime identities.
An initialization-only inspection retained the OCR effective configuration
manifests after the run and reproduced **exactly** every captured recognition
configuration hash. It performed zero predictions and zero media decoding.
Detector configuration material is derived from the unchanged executed code and
captured placement/settings; it is explicitly labelled as such, rather than
pretending the detector produced a v3 OCR result.

## Sole MP4 run

Input: `services/license_plate_service/run_video_pipeline/test_video.mp4` in the
source checkout. SHA256:
`36067a473c1afd4d4f35d63224a5f0f110213be7bc1c8f75f80adbe23caaffa2`.

Explicit mode: **`legacy_whole`**, chosen only for this review. The source's
truthy `"0"` switch is not used. All native bindings initialize before opening
the sole decoder, so missing runtime/assets can block without decoding media.
Initialization is distinguished from actual model execution/category coverage.

Budgets: four decoded frames, 240 seconds of total admission time including
initialization, eight crop attempts, 8 MiB per crop and 32 MiB retained crops.
Existing T1 raw-detection/lifetime/segment/handle caps remain enforced. The stage
operation bound is `2*frames + 6*crops`; DINO R2 contains four CLS view forwards
within one scoring operation. The wall-time deadline is admission control,
not a native cancellation guarantee. No extra decoder/seek/replay or output
video/crop save occurs.

Result: **passed**, one decoder opened once, four frames decoded, four associated
eligible plate observations, one selected crop, no crop replacements. One local
`tunis` decision was accepted; unresolved **0**, execution errors **0**. These
are policy results, not independently labelled correctness/accuracy evidence.

Executed models: vehicle detector 4 calls; ONNX plate detector 4 calls; Arabic
OCR 1; DINO R2 scoring 1; whole registration OCR 1; Tunis marker 1. Whole OCR was
unambiguous, so Plate scheduled **no regional OCR**. Neither marker-first Tunis
nor Idara/NT/Shorta was exercised by real clip inference. Their tests are synthetic.

| Stage | Calls | Total execution time (s), including verification/synchronization |
|---|---:|---:|
| category_ocr | 1 | 1.002 |
| visual | 1 | 0.323 |
| registration | 1 | 1.394 |
| marker | 1 | 0.192 |
| vehicle | 4 | 1.618 |
| plate | 4 | 0.688 |

Model initialization was separately measured: Arabic 7.281 s, DINO 2.962 s,
registration .644 s, marker .206 s, ONNX .450 s. Driver elapsed 26.402 s;
including interpreter/import/exit overhead, command wall time 32.087 s.
These are one cold bounded run's timings, not throughput/latency benchmarks.

Cleanup: decoder closed, unretired operations **0**, retained crops **0**, all
model runtimes/trackers closed, cleanup errors **0**. The driver blocks socket
connections, runtime model retrieval and provider routes. Redis publications,
DB1 records and provider calls are all **0**. Shared evidence contains scalar
counts, hashes, configuration identities and timings; no OCR readings,
normalized/display registrations, footage, cropped pixels or secrets are exported.

No v3 wire-contract mismatch was observed in the exercised legacy Tunis path.
Native API adaptations were necessary: PaddleX 3.7.2 exposes actual execution
settings through `paddlex_predictor.runner._config`, and the source DINO matcher
returns an interpreted winner whereas v3 needs the full score vector. The new
binding handles both. Unexercised real paths remain validation limits.

## Commands and checks

All commands ran from `/home/intern/part-time/aziz/pipeline` with
`PYTHONDONTWRITEBYTECODE=1`:

```bash
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_mohamed_lpr_engine vehicle.common.tests.test_mohamed_lpr_harness vehicle.common.tests.test_mohamed_plate_policy -v
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest vehicle.common.tests.test_plate_metadata vehicle.common.tests.test_plate_requests vehicle.common.tests.test_engine_plate vehicle.common.tests.test_plate_local_executors vehicle.common.tests.test_redis_plate vehicle.common.tests.test_review_handshake vehicle.common.tests.test_mp4_colour_plate vehicle.common.tests.test_mp4_publication_order vehicle.common.tests.test_db1_consumer vehicle.common.tests.test_real_video
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python -m unittest discover -s vehicle/license_plate_service/run_video_pipeline/tests
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python contracts/validate_examples.py
PYTHONDONTWRITEBYTECODE=1 /home/intern/.venv/bin/python contracts/db1/validate_fixtures.py
PYTHONDONTWRITEBYTECODE=1 /home/intern/miniforge3/envs/cuda-dev/bin/python scripts/vehicle_mohamed_lpr_mp4.py --enable-mohamed-lpr-v3 --source-root /home/intern/part-time/mohamed/pipeline --ocr-cache /home/intern/.paddlex/official_models --video /home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/test_video.mp4 --output /home/intern/part-time/aziz/mohamed-lpr-phase3-validation-20261108/mp4-review --tunis-mode legacy_whole --max-frames 4 --max-crops 8 --max-seconds 240
PYTHONDONTWRITEBYTECODE=1 /home/intern/miniforge3/envs/cuda-dev/bin/python /home/intern/part-time/aziz/mohamed-lpr-phase3-validation-20261108/retain_identity_material.py
```

| Check | Result |
|---|---|
| Phase 1/2/3 (`phase123-final.log`) | 97 methods passed, no skips: 39 Phase 1, 33 Phase 2, 25 Phase 3. |
| Affected T1 (`affected-t1.log`) | 195 methods passed, no skips, including existing Engine, Plate, v2 admission, real-video injected ownership and DB1-consumer regressions. |
| Legacy Plate | 57 passed, no skips; provider timeout is mocked. |
| Event contract fixtures | Schema valid; 18 valid, 24 schema rejects, 16 application rejects; normalization 8, retry 3, strict JSON 4, vocabulary drift 3 passed. |
| DB1 contract fixtures | 122 synthetic checks passed; no database connection. |
| CUDA MP4 | Exit 0; passed as detailed above. |
| Identity-manifest inspection | Exit 0; exact captured configuration hashes reproduced, zero predictions/decodes. |

The independent tests cover original-coordinate clipping, ambiguous/unassociated
plates, cross-parent/wrong-receipt rejection, crop replacement/ownership, mutation
isolation, both Tunis modes/all categories, invalid numbers despite successful
category evidence, wrong artifact/index/placement, missing CUDA/package/provider,
malformed/native execution failure, retry caching, admission deadlines and
unretired-return/failure holding both the crop and global lane. Phase 2 retains
its stale/out-of-order/conflicting retry/seal tests unchanged.

Execution evidence:
`/home/intern/part-time/aziz/mohamed-lpr-phase3-validation-20261108/`.
`baseline.json` and `starting-git-status.txt` record starting changes;
`commands.json` records exact argv/exit/duration/logs; `mp4-review/summary.json`
contains the sanitized real run; `artifact-identities.json` retains fingerprint
material; `final-verification.json` records preservation and final file hashes.
Initial development fixture errors (using a nonexistent owner identity property
and receipts not captured by the supplied clock) were corrected before the
passing adapter/native runs; their log is retained and not counted as a pass.
