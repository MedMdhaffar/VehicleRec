# Sprint 0 — Scaffold

**Phase:** A (vehicle_classification)
**Depends on:** —
**Goal:** Lay down the `vehicle_services/` structure and the shared `common/` building blocks, and stand up a runnable `vehicle_classification` FastAPI service with `/health` and a placeholder `/classify` that already returns the **standard result envelope**. No model, no real classification yet — this sprint is the skeleton everything else plugs into.

---

## Why this sprint

Every later sprint (and every later service — plate_recognition, traffic_analytics) imports from `common/`. Getting the shared contracts right *now* — the detector interface, the response envelope, image decoding, annotation/saving — means Sprints 1–3 only fill in behaviour, never restructure. The service must run and answer requests from day one, even with an empty detector.

---

## Scope

**In scope**
- Folder tree per `ROADMAP.md` §3 (the `common/` + `vehicle_classification/` + `training/` parts).
- `common/` shared modules (real, working): `config.py`, `schemas.py`, `image_io.py`, `annotate.py`, and `detector.py` as a **documented interface stub**.
- `vehicle_classification/` FastAPI app with `/health` and `/classify` (returns a valid but empty envelope until Sprint 1–2).
- Environment / dependencies file.
- `results/` directory for saved annotated images, with cleanup policy.

**Out of scope**
- The trained model (Sprint 1).
- Real detection/classification output (Sprint 2).
- Colour (Sprint 3).

---

## Deliverables

```
vehicle_services/
  common/
    __init__.py
    config.py          # ports, model paths, thresholds, results dir, class/taxonomy map
    schemas.py         # pydantic: Detection, ClassifyResponse (envelope), ErrorResponse
    image_io.py        # decode multipart / base64 / url → np.ndarray (BGR)
    annotate.py        # draw boxes+labels → annotated image, save to results/, return path
    detector.py        # VehicleDetector interface STUB (load + predict signature)
    models/            # (empty) trained weights land here in Sprint 1
    results/           # saved annotated images (git-ignored)
  vehicle_classification/
    __init__.py
    app.py             # FastAPI: /health, /classify (placeholder), /classify_crop (placeholder)
    config.py          # service-specific config (port 5020, thresholds)
    schemas.py         # service request models (ClassifyRequest options)
  requirements.txt
  .gitignore           # results/, __pycache__, *.pt
```

---

## Tasks

1. **Folder + package init.** Create the tree above with `__init__.py` so `common` is importable from each service.

2. **`common/config.py`** — central config (env-overridable):
   - `RESULTS_DIR` (default `common/results`), `MODELS_DIR` (`common/models`).
   - `VEHICLE_MODEL_PATH` (points to where Sprint 1 will drop weights).
   - Default thresholds: `CONF_THRESHOLD = 0.35`, `MIN_BOX_AREA_FRAC` (size gate, used later by plate).
   - **Taxonomy map** (model class-id → service-facing type) — placeholder now, finalized in Sprint 2. Document the 9 training classes and the 6 target types here so it's the single source of truth.
   - Service ports: `VEHICLE_CLASSIFICATION_PORT = 5020`.

3. **`common/schemas.py`** — pydantic v2 models (the API contract from `ROADMAP.md` §"vehicle_classification"):
   - `Detection`: `id:int, bbox:[int,int,int,int], type:str, type_confidence:float, colour:str|None=None, colour_confidence:float|None=None`.
   - `ClassifyResponse` (the envelope): `request_id, timestamp, image_size:[w,h], vehicle_count:int, detections:list[Detection], annotated_image:str|None, annotated_image_b64:str|None`.
   - `ErrorResponse`: `request_id, error, detail`.
   - Keep `colour` fields nullable so the same schema is valid in Sprint 2 (no colour) and Sprint 3 (with colour).

4. **`common/image_io.py`** — `load_image(...)` accepting any of: multipart `UploadFile`, base64 string, or URL/local path; returns a BGR `np.ndarray`. Raise a clean error on undecodable input. (Multipart + base64 are the primary paths per the API design.)

5. **`common/annotate.py`** — `annotate_and_save(image, detections, request_id) -> path`: draw each bbox + label (`type conf%`, colour appended later), write JPEG to `RESULTS_DIR`, return the relative path. Implement a simple retention cap (keep last N per service, evict oldest) mirroring `counting_service`'s snapshot policy.

6. **`common/detector.py`** — define the **interface** that Sprint 1/2 will implement:
   ```python
   class VehicleDetector:
       def __init__(self, model_path: str, conf: float): ...
       def predict(self, image: np.ndarray) -> list[Detection]: ...   # bbox+type+conf, taxonomy-mapped
   ```
   Stub `predict()` to return `[]` (and log "model not loaded") so the service runs before Sprint 1. Document that the real implementation loads yolo26 weights.

7. **`vehicle_classification/app.py`** — FastAPI app:
   - `GET /health` → `{status:"ok", service:"vehicle_classification", model_loaded:bool}`.
   - `POST /classify` → decode image via `image_io`, call detector stub, build `ClassifyResponse` (empty detections for now), annotate+save, return envelope. Honour a `return_image_inline:bool` request flag (base64 in `annotated_image_b64` when true).
   - `POST /classify_crop` → same envelope shape; placeholder.
   - CORS enabled (consistent with the other services).

8. **`requirements.txt`** — `fastapi`, `uvicorn`, `python-multipart`, `pydantic>=2`, `opencv-python`, `numpy`, `ultralytics` (for Sprint 1), `requests`. Note: reuse the project venv at `/home/malek/workflow/.venv_linux` (already has torch) or document a dedicated venv.

9. **`.gitignore`** — `results/`, `**/__pycache__/`, `*.pt`, large dataset dirs.

---

## Acceptance criteria

- `uvicorn vehicle_classification.app:app --port 5020` starts cleanly.
- `GET /health` → 200, `model_loaded:false`.
- `POST /classify` with a real image (multipart) → 200 + a **schema-valid** `ClassifyResponse` (empty `detections`, `vehicle_count:0`, a saved `annotated_image` path that exists on disk).
- `from common import schemas, detector, image_io, annotate, config` imports with no error from a service module.
- Folder tree matches `ROADMAP.md` §3.

---

## Notes / risks

- **Port 5020** chosen to avoid existing services (5001/5003/5004/5005/5008/5011/5100/7870). Recorded in `sprints/README.md`.
- Keep `common/` free of any service-specific logic — it's shared by plate_recognition and traffic_analytics later.
- The nullable colour fields are deliberate: one envelope schema serves all three sprints.
