# VLM-only vehicle attributes

Recognition now uses direct in-process llama.cpp Python bindings, with the
model-specific `LFM25VLChatHandler`. The exact BF16 model/projector passed the
CUDA compatibility test in the supplied `evidence.tar.gz`. See
[LLAMA_CPP_MIGRATION.md](LLAMA_CPP_MIGRATION.md) for pins, evidence, limitations
and installation. No HTTP inference, model server or Transformers fallback runs.

Processing: upload → YOLO boxes → ByteTrack → selected in-memory vehicle crops →
local LiquidAI/LFM2.5-VL-450M → manufacturer/body/color → video rendering → H.264.
YOLO class IDs filter proposals; public vehicle attributes come from the VLM.
Crop selection, attempts, aggregation, result fields and annotation semantics
remain unchanged.

## Model setup and pipeline integration

The runtime needs the pinned CUDA binding, Pillow, numpy, OpenCV, Django/DRF,
PyTorch and Ultralytics. PyTorch remains required for detection. Existing
Transformers dependencies are retained for other application/validation code.
Use the separately built wheel described in the migration document; installing
upstream `llama-cpp-python` by version alone does not select this fork.

Default assets are the two BF16 GGUF files in this package's `models/` directory:
`LFM2.5-VL-450M-BF16.gguf` and `mmproj-LFM2.5-VL-450m-BF16.gguf`.
`MANUFACTURER_MODEL_PATH` and `MANUFACTURER_PROJECTOR_PATH` override individual
files. Old overrides pointing at a Transformers directory must be updated.
Both files are validated before native initialization; inference never downloads
assets. `MANUFACTURER_CONTEXT_SIZE` defaults to 4096; complete text, visual input
and the output allowance must fit or the attempt fails with `ContextBudgetError`.
No visual context is silently shifted or discarded.

CUDA is required. Expose one GPU/MIG instance with `CUDA_VISIBLE_DEVICES` before
starting Django; `MANUFACTURER_DEVICE` must be `cuda:0` in that visible namespace.
Language layers and the vision projector are offloaded to GPU. The adapter uses
batch/microbatch 512/128, one sequence, disabled checkpoints and seed 1. These are
llama.cpp settings, not vLLM encoder-cache budgets.

Django calls VehicleAnalysisService → VehiclePipeline → TrackRecognizer →
ManufacturerService → LlamaBackend in the same process. Each enabled video job
loads one model and projector. All selected crops reuse them. BGR is converted to
RGB once; lossless in-memory PNG transport bypasses the binding's JPEG conversion.
There are no temporary image files. Explicit crop-size caps remain opt-in.

A recognizer lock serializes calls and cleanup. Every request resets native hybrid
state and clears image/cache references. Native image chunks/bitmaps are released
on success or failure. Job cleanup explicitly frees the projector before the model,
including startup failures, exceptions and cancellation. The existing process-local
GPU job lock remains. Separate Django workers/processes can each allocate their
own model; this lock does not coordinate GPU allocations across processes.

FFmpeg is still used only for final browser video encoding. Recognition-disabled
jobs do not initialize the VLM. Enabled jobs fail clearly on missing assets,
unsupported bindings, CUDA failure or initialization failure; there is no fallback.
Optional per-crop failures retain the existing attempt-budget/error policy.

## Django and frontend

POST multipart video to /api/classification-manufacturer/analyze/. Optional
max_frames limits processing; enable_manufacturer=true/false overrides the default.
The vehicle frontend explicitly enables recognition. It displays VLM attributes,
status, timing, selected crops, raw responses and failures. Early/best and submitted
crops are saved under media/vehicle_classification/<job_id>/crops/. Jobs can be
reopened through GET /api/classification-manufacturer/jobs/. Existing report, video
and validated crop URLs are unchanged. Reports are files, not database records.

## Type and color semantics (result schema version 2)

vehicle_type now means the VLM body category, matching vlm_vehicle_type. This is
an intentional change from the earlier detector-coarse API meaning requested for
VLM-only classification. vehicle_type_source identifies local_lfm or none.
The compatibility fields detector_vehicle_type and type_confidence are null.
There is no calibrated VLM confidence. YOLO type disagreement diagnostics have
been removed. ROI analytics also use the available VLM category.

Body categories: sedan, hatchback, suv, wagon, coupe, convertible, van, pickup,
bus, truck, motorcycle, bicycle, unknown. Minivans map to van, minibuses/coaches
to bus, scooters to motorcycle, station wagons to wagon. car is not a body category.

Consumer brands normalize through manufacturer/taxonomy.py and are checked against
manufacturer/brands.py. Unknown and unsupported brands remain unknown.

Colors: white, black, red, blue, green, yellow, orange, unknown. Gray/grey/silver,
off-white and ivory map to white; brown to black; burgundy/maroon to red; navy to
blue; olive to green. Unknown/unsupported values never default to white or black.
The prompt asks for dominant painted body color, ignoring windows, tyres, wheels,
lights, plates, background and reflections, and unknown for unreliable/infrared-only
color evidence. Labels still depend on the model interpreting the image correctly.

```json
{"result_schema_version":2,"vehicle_type":"sedan","vlm_vehicle_type":"sedan",
 "vehicle_type_source":"local_lfm","manufacturer":"toyota","color":"white",
 "type_confidence":null,"detector_vehicle_type":null,
 "recognition_status":"recognized","recognition_attempts":1}
```

The latest complete valid response supplies all three attributes. One crop is
enough. Unknown fields remain unknown rather than being borrowed from older crops.
Failed attempts preserve the last valid response. Conflicts across attempts are
recorded as diagnostic reasons. States: disabled, pending, recognized (at least
one known field), unknown, error (no valid response from any attempted inference).
Malformed responses fail the attempt; bounded raw_output is kept for diagnosis.

## Alignment with standalone validation

manufacturer/prompts.py follows vlm-validation/test_hftranformers.py: the same
body categories, color mappings, 128 output-token limit and greedy generation. Crops retain original resolution by default: Django no longer
silently reduces every crop to 512 pixels. These changes remove known differences,
but a tight detector crop, compression, model revisions and different serving
engines can still change the result relative to a standalone full-image test.

MANUFACTURER_MAX_CROP_SIDE=0 means no client resize. A positive value from 48 to
8192 explicitly enables a maximum-side cap. The native vision encoder still performs
its model-specific tiling and preprocessing. Original crops require more RAM/VRAM and can
increase inference time; no throughput or accuracy improvement is guaranteed.

## Crop lifecycle, budgets and rendering

Crop collection runs at MANUFACTURER_CROP_GAP (default 8 frames), clipping valid
boxes against source frames. Existing quality scoring uses size, sharpness,
exposure and border clipping, with visual signatures to avoid duplicate submissions.
One best pending crop per track is retained, at most 64 candidates globally and
MANUFACTURER_MAX_PENDING_CROP_MB (default 256 MiB) total pending pixel storage.
Candidates over the memory budget are skipped with crop_memory_budget_exceeded.

Initial inference: MANUFACTURER_INITIAL_FRAMES observations (default 8). Recheck:
a distinct crop improves quality by more than 0.05. On lost/end-of-video tracks,
a remaining candidate is submitted. MANUFACTURER_MAX_ATTEMPTS defaults to 3
(maximum 5); failed attempts consume the budget. MANUFACTURER_MAX_NEW_TOKENS
is 128 by default (maximum 128). Inference is synchronous with no background queue.

Hard cuts reset ByteTrack and finalize prior tracks before the next scene.
scene_cuts records their frame indices. VEHICLE_SCENE_CUT_THRESHOLD defaults to
0.12; 0 disables it. This heuristic handles hard-cut slideshows, not arbitrary
transitions, and can split tracks on flashes/large camera moves.

Frame metadata is spooled to disk and a second pass draws final VLM decisions.
Django reuses license_plate_service.video.prepare_browser_video for H.264 encoding.
Historical reports retain their original results and semantics; reanalyze videos
to obtain schema-version-2 results. Original-resolution submitted images are saved
as JPEG previews; inference itself consumes the in-memory crop.

## Terminal logs and checks

Restart Django to pick up changes. Pipeline modules log directly through Django's
logging configuration. Stages include model/projector loading and CUDA offload, crop attempts,
raw invalid responses, selected labels, track finalization, frame/rendering
progress, encoding and completion time. Frame progress is throttled to five
seconds. These logs are not a browser progress endpoint.

```bash
python manage.py test vehicle_classification_manufacturer_service.tests.LocalRecognitionTests vehicle_classification_manufacturer_service.tests_llamacpp
```

Routine tests mock inference and check parsing, source attribution, native crop
size/budgets, finalization, rendering, API artifacts, direct pipeline integration,
per-job cleanup and failure recovery. Test real GPU inference with an uploaded video.
Recognition accuracy on Tunisian surveillance footage still requires independent
evaluation; a successful
standalone image test is not evidence of video accuracy.

## Malformed output retries and duplicate detections

When a VLM attempt returns invalid_model_json, that failed view is not marked as
successfully recognized. The next eligible observation (respecting the crop gap)
can retry it with an additional strict formatting instruction. Each retry consumes
one of the same per-track attempts; no unbounded retries or permissive JSON repairs
are used. Raw failures remain in the report even when a later retry succeeds.
These retries address response formatting, not recognition accuracy.

Before ByteTrack, nearly identical YOLO boxes are suppressed across class IDs at
IoU >= 0.90, keeping the highest-confidence box. This prevents duplicate class
proposals for the same object becoming two tracks. Separate vehicles with lower
overlap remain separate. Strongly occluded vehicles with near-identical boxes can
still be ambiguous; no geometry-only rule guarantees identity correctness.
