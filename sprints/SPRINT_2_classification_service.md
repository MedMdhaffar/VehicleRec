# Sprint 2 — vehicle_classification service

**Phase:** A (vehicle_classification)
**Depends on:** Sprint 0 (scaffold), Sprint 1 (weights)
**Goal:** Turn the scaffold into a working stateless service: implement the real `common/detector.py`, finalize the model→service taxonomy mapping, and make `/classify` (full frame) and `/classify_crop` (pre-cropped vehicle) return real **type + confidence + annotated image** in the standard envelope.

---

## Why this sprint

Sprint 1 produced a model; Sprint 0 produced an empty service. This sprint connects them and locks the **public behaviour** of the module: what types it reports, how it filters, what the annotated output looks like. Colour is added next sprint into the same envelope (the schema already has nullable colour fields).

---

## Scope

**In scope**
- Real `VehicleDetector.predict()` loading `common/models/vehicle_yolo26.pt`.
- Model class-id → **service taxonomy** mapping (the boss's 6 types + decisions on person/camper/tractor).
- `/classify`: full frame → all vehicles with type + confidence + annotated image.
- `/classify_crop`: a pre-cropped vehicle → its type.
- Confidence + size thresholds, configurable.

**Out of scope**
- Colour (Sprint 3).
- Tracking / voting (that's traffic_analytics / plate, not this stateless module).

---

## Taxonomy mapping (the key decision)

The model knows 9 classes; the module exposes vehicle types. Mapping (set in `common/config.py`, single source of truth):

| Model class | Service output | Rule |
|-------------|----------------|------|
| car | `car` | pass |
| truck | `truck` | pass |
| bus | `bus` | pass |
| van | `van` | pass |
| motorcycle | `motorcycle` | pass |
| cyclist | `bicycle` | rename to the boss's "vélo" term |
| camper | `van` *(default)* or `camper` | **merge to van** by default (camper is van-like, sparse); configurable |
| tractor | `truck` *(default)* or drop | merge to truck (near-zero data); configurable |
| person | *filtered out by default* | not a vehicle; `include_person:true` request flag can surface it |

The mapping is **config-driven** so it can change without code edits. Document it in `config.py` and in the service's OpenAPI description.

---

## Deliverables

```
common/detector.py        # real VehicleDetector (load yolo26, predict → mapped Detections)
vehicle_classification/
  app.py                  # /classify + /classify_crop fully implemented
  config.py               # thresholds, taxonomy flags, port
  schemas.py              # ClassifyRequest options (return_image_inline, include_person, conf override)
```

---

## Tasks

1. **`common/detector.py` — real implementation**
   - Load `VEHICLE_MODEL_PATH` once at startup (module-level singleton; thread-locked `predict` like `counting_service`/`live_inference` do, so it's safe under the API server's threads).
   - `predict(image)`: run yolo26 → for each detection apply (a) `conf >= CONF_THRESHOLD`, (b) optional min-box-size gate, (c) **taxonomy mapping** → emit `Detection(id, bbox, type, type_confidence)`.
   - Apply per-class confidence overrides if configured (e.g., a higher bar for weak classes from Sprint 1's report).
   - `model_loaded` flag surfaced to `/health`.

2. **`/classify` (full frame)**
   - Decode image → `detector.predict()` → assign sequential `id`s → build `ClassifyResponse` (`vehicle_count = len`).
   - Annotate (boxes + `type conf%`) → save → set `annotated_image` (and `annotated_image_b64` if `return_image_inline`).
   - Honour request overrides: `conf`, `include_person`, `return_image_inline`.

3. **`/classify_crop` (pre-cropped vehicle)**
   - Input is assumed to be a single vehicle crop. Run `predict()` on it; pick the **highest-confidence, most-central** box as the vehicle (handles padding/background in the crop). If nothing passes threshold, return one low-confidence `unknown` detection rather than an empty list (the caller asserted it's a vehicle).
   - Same envelope shape; `vehicle_count` typically 1.

4. **Thresholds & config**
   - `CONF_THRESHOLD`, optional `PER_CLASS_CONF`, `MIN_BOX_AREA_FRAC`, taxonomy merge flags — all in config, request-overridable where it makes sense.

5. **Smoke validation** (not formal Sprint-1 eval)
   - Run a handful of real street images through `/classify`; eyeball that cars/vans/buses/trucks/motos/bikes get sensible boxes + types, and that **van** is correctly distinguished from car on at least a few examples.

---

## Acceptance criteria

- `GET /health` → `model_loaded:true`.
- `POST /classify` with a real multi-vehicle street image → 200 + envelope with correct per-vehicle `type` + `type_confidence`, boxes drawn, and a saved annotated image that visually matches.
- **van is correctly identified** on sample images containing a van (the headline capability).
- `POST /classify_crop` with a cropped vehicle → returns its type.
- Taxonomy mapping behaves per the table (cyclist→`bicycle`, person filtered unless `include_person`, camper→van by default); changing a config flag changes the output with no code edit.
- `colour` fields present in the response as `null` (schema unchanged; Sprint 3 fills them).

---

## Notes / risks

- **Single shared model under a threaded server:** guard `predict` with a lock (proven pattern in the existing services) to avoid CUDA races.
- `/classify_crop` semantics: a crop is "trust the caller, it's a vehicle" — bias toward returning a type even at lower confidence, unlike `/classify` which filters.
- Don't bake colour into annotation yet — Sprint 3 appends it to the same label string.
- Keep all type strings stable from here; downstream agents/tools will key on them.
