# vehicle_services — Roadmap

**Date:** 2026-06-18
**Status:** Phase A vehicle model trained; Phase B ANPR components all built & validated on real plates — assembly (Sprint 5) is the next step.
**Owner:** malek

---

## 1. Goal

Build `vehicle_services/`: a family of **API-first microservices** for vehicle analytics, each exposing a clean REST API and returning **structured JSON + an annotated image**. The services are **consumer-agnostic** — they are designed to be used by *any* external app, with the primary near-term consumer being an **AI agent** (the existing orchestrator) that either:

- **calls a service as a tool** (pull — synchronous request/response), or
- **receives pushed results/events** from a service (push — for continuous stream analytics).

The services must run standalone now and be embeddable into `live_inference` later via API calls. No coupling to any single consumer is baked into a service.

The full vehicle-analytics surface (mapping to the boss's email + the `suivi_video_analytique` sheet):
vehicle classification (type + colour), plate recognition (ANPR), bidirectional line-crossing counting, parking duration, restricted/illegal parking.

---

## 2. Core architectural principles

1. **Two service *types*, not five identical silos.**
   - **Stateless / per-image** — you send a frame (or a crop), you get an answer; no memory between calls.
     → `vehicle_classification`, `plate_recognition`
   - **Stateful / per-stream** — consumes a video stream, **tracks** vehicles across frames, holds temporal state, emits events over time.
     → `traffic_analytics` (counting + parking_time + restricted_parking, as separate endpoints over one tracker)

2. **Share the detector, don't clone it.** Every service needs vehicle detection. The fine-tuned **yolo26** detector lives once in `common/` as a shared library (weights + inference fn + class map). Every service imports it. If GPU memory later becomes tight, the detector is promoted to a single inference server (Triton / one process) — left as a future optimization, not a v1 concern.

3. **Consumer-agnostic REST.** A service exposes clean REST and returns `{JSON + annotated image}`. It does **not** know what an "agent" is. Integration is a thin outer layer:
   - *pull*: a tool-wrapper in the agent HTTP-calls the endpoint (same pattern the orchestrator already uses for its other backend tools);
   - *push*: the stream service POSTs to a `callback_url` the caller registered (or publishes to an event bus).

4. **Standard result envelope.** Annotated image is saved to a results dir; its path/URL is returned in the JSON. An optional `return_image_inline: true` flag returns base64 when the caller can't reach the filesystem. (Consistent with how `counting_service` saves snapshots and `face_tracking` serves crops by URL.)

5. **Pull vs push maps to the service type** — stateless services are pull-only (the response *is* the result); the stream service is where push matters (events happen asynchronously).

---

## 3. Folder architecture

```
vehicle_services/
  common/                    # shared across ALL services
    detector.py              # fine-tuned yolo26: frame → [box, class, conf]   ← THE shared model
    schemas.py               # pydantic request/response base types + result envelope
    image_io.py              # decode upload / base64 / url → ndarray
    annotate.py              # draw boxes + labels → annotated image, save to results dir
    config.py                # ports, model paths, thresholds, results dir
    results/                 # saved annotated images (served by URL/path)

  vehicle_classification/    # STATELESS · per-image · FIRST
    app.py                   # FastAPI service
    colour.py                # colour estimation on the vehicle crop (HSV baseline v1)
    schemas.py
    config.py

  plate_recognition/         # STATELESS · per-image (uses common.detector + plate head + OCR)
    app.py
    plate_detector.py
    ocr.py
    schemas.py

  traffic_analytics/         # STATEFUL · per-stream (counting + parking + restricted)
    app.py                   # FastAPI; separate endpoints per analytic
    tracker.py               # ByteTrack over the shared detector
    geometry.py              # polygon ROIs + virtual lines, point-in-poly, line-crossing
    state.py                 # per-track temporal state (dwell, zone membership, history)
    rules/
      line_crossing.py       # bidirectional counting (left↔right / in↔out)
      parking_dwell.py       # parking_time: how long a vehicle has been parked in a zone
      restricted_zone.py     # restricted_parking: vehicle parked in a forbidden zone
    push.py                  # callback/webhook emitter for async events
    schemas.py
    config.py

  training/                  # the yolo26 vehicle model lifecycle
    datasets/                # streetdet (primary), KICT (cross-domain test, remapped)
    prepare_streetdet.py
    voc_to_yolo_kict.py      # KICT VOC → YOLO remap (02→bus, 03-07→truck, drop 08-12)
    train.py                 # yolo26 fine-tune config
    eval.py                  # streetdet val (all classes incl. van) + KICT (bus/truck only)

  ROADMAP.md                 # this file
  sprints/                   # detailed per-sprint docs (created next)
```

---

## 4. The detector ↔ services relationship (the spine)

**Vehicle detection is the spine every service hangs off.** It is NOT its own microservice (yet) — it's a shared library in `common/`.

```
                         ┌───────────────────────────────┐
                         │  common/detector.py (yolo26)   │
                         │  frame → [bbox, type, conf]    │
                         └───────────────┬───────────────┘
            ┌────────────────────────────┼────────────────────────────┐
            │ (per-image)                 │ (per-image)                 │ (per-stream + tracker)
            ▼                             ▼                             ▼
  vehicle_classification          plate_recognition              traffic_analytics
  detect → crop → colour          detect → plate det → OCR        detect → TRACK → geometry/rules
  out: type + colour              out: plate string               out: counts / dwell / zone events
```

- **vehicle_classification** = detector (box + type) → per-vehicle crop → colour. Stateless.
- **plate_recognition** = detector (vehicle box) → plate detector on the crop (size-gated cascade) → OCR → per-track vote (when fed sequential frames). Stateless per call; voting is the caller's concern or a light internal buffer.
- **traffic_analytics** = detector + **ByteTrack** (this is what makes it stateful) → ROI/line geometry → one of three rule modules. The tracker and geometry are shared; the three analytics differ only in the rule applied to the track stream.

**Why traffic_analytics is one service with three endpoints, not three services:** counting, parking_time, and restricted_parking all run the *same* machine — detect → track → evaluate a geometric/temporal condition per track. They differ only in the rule:
- `line_crossing`: did the track's path cross a virtual line, and in which direction? → bidirectional count.
- `parking_dwell`: has a (stationary) vehicle's dwell-in-zone exceeded a threshold? → duration / over-stay.
- `restricted_zone`: is a vehicle parked inside a forbidden polygon? → violation event.
Running them as three processes would mean three trackers and three detector copies over the same stream — wasteful and inconsistent. One process, three endpoints.

---

## 5. Per-microservice goals

| Service | Type | Goal | Core pipeline | Output |
|---------|------|------|---------------|--------|
| **vehicle_classification** | st ateless | Identify each vehicle's **type** (car, truck, bus, van, motorcycle, bicycle/cyclist) and **colour** | detect → crop → type (yolo26) + colour (HSV) | JSON: per-vehicle bbox + type + colour (+ confidences) + annotated image |
| **plate_recognition** | stateless | **Read the plate number (digits) + classify plate type** (ANPR) | plate detect (dedicated YOLO) → per plate: OCR digits + word-spot type | JSON: per-plate {number, type=tounes/other, confidences} + annotated image |
| **traffic_analytics → counting** | stateful | **Bidirectional line-crossing count** — count vehicles crossing a virtual line, separated by direction (e.g. coming from the right vs from the left, or in vs out) | detect → track → line-crossing test + direction | per-direction, per-class counters; crossing events (push) |
| **traffic_analytics → parking_time** | stateful | **Parking duration** — measure how long each vehicle has been parked in a monitored zone | detect → track → stationary detection + dwell timer in ROI | per-vehicle dwell seconds; over-stay events (push) |
| **traffic_analytics → restricted_parking** | stateful | **Illegal/restricted parking** — flag any vehicle parked inside a forbidden zone | detect → track → vehicle parked inside forbidden polygon + dwell | violation events with vehicle id/type/clip (push) |

---

## 6. Build order & the first goal — fast wins first

**Priority (per boss): focus on fast wins.** The two **stateless / per-image** services are the fast wins — they reuse one shared detector, need no tracker/stream/temporal state, and ship behind a simple synchronous REST API. The **stateful / per-stream** service (`traffic_analytics`) is heavier (tracker + stream ingestion + ROI/line geometry + push events), so it goes **last**.

**Delivery order:**

1. **vehicle_classification** *(fast win #1)* — detection + type classification + colour, stateless REST returning JSON + annotated image. Backed by a yolo26 model fine-tuned on streetdet (covers car/truck/bus/van/moto/cyclist), evaluated on streetdet val (all classes incl. van) and cross-domain on KICT (bus/truck only, remapped).

2. **plate_recognition** *(fast win #2)* — same detector cascade (vehicle box → size-gated plate detector → OCR → normalize), stateless REST returning the plate string + annotated image. Reuses `common.detector`; the only net-new pieces are the plate detector + OCR engine.

3. **traffic_analytics** *(after the fast wins)* — the three stateful analytics on one detect→track core with configurable ROIs/lines per camera, pushing events to a registered callback:
   - **bidirectional counting** — vehicles crossing a virtual line, **direction-aware** (from the right / from the left, configurable per line), per class.
   - **parking_time** — dwell duration of vehicles parked in a monitored zone.
   - **restricted_parking** — vehicles parked in a forbidden zone → violation events.

So: **vehicle_classification → plate_recognition → traffic_analytics.**

---

## 7. Sprint sequence (high level — detailed sprint docs come next)

> These are milestones, not yet day-by-day plans. Each will get its own detailed doc under `sprints/`.

**Phase A — Foundation & vehicle_classification** *(fast win #1)*
- **Sprint 0 — Scaffold:** folder structure, `common/` (detector lib stub, schemas, image_io, annotate, config, results dir), FastAPI skeletons + `/health`, result envelope.
- **Sprint 1 — Vehicle model (training):** prepare streetdet; `voc_to_yolo_kict.py` remap; train yolo26; eval on streetdet val (all classes incl. van) + KICT cross-domain (bus/truck). Deliver weights into `common/`.
- **Sprint 2 — vehicle_classification service:** wire `common.detector` into `/classify` + `/classify_crop`; return type + confidence + annotated image.
- **Sprint 3 — Colour:** HSV-baseline `colour.py`, integrate into the response; (trained colour head deferred).

**Phase B — plate_recognition (ANPR)** *(fast win #2)* — **components BUILT ✅, assembly remaining**

The ANPR system turned out to be **three real components**, all built and validated on real Tunisian plates (this diverged a lot from the original guesses — see notes):

- **Sprint 4 — ANPR components (DONE ✅):**
  - **Plate detector** — `plate_recognition/models/best_matricule.pt` (finetuned YOLO, *tight* plate boxes; standalone, NOT a head on `common.detector`). Runs on a frame or vehicle crop → plate* box(es). Validated via `test_plate_detection.py`.
  - **OCR — digits** — PaddleOCR **PP-OCRv6 `TextRecognition`, `engine="transformers"`** (bypasses the PaddlePaddle oneDNN/PIR crash). Reads the plate **digits** reliably. *Arabic OCR was rejected* — `arabic_PP-OCRv5` hallucinated; the Arabic is handled by the classifier instead, not OCR.
  - **Plate-type classifier** — **تونس word-spotting**: **stretch crop to 512×512** → `models/tounes_word_detector.pt` (yolo26n trained on the Roboflow/HF تونس-word dataset) → **conf ≥ 0.5** → **`tounes` (civilian)** else **`other`**. Real-data validated: **civilian recall 92.4%, references 7/7**. Scripts: `train_word_detector.py`, `word_classify.py`. Synthetic-classifier / CLIP-similarity / one-class all FAILED (data gap) — word-spotting on real data is what worked.

- **Sprint 5 — ANPR assembly (TO DO):** wire the three into the `plate_recognition` REST service:
  `POST /read_plate` (frame or crop) → plate detect → for each plate: { OCR digits **+** word-spot type } → JSON
  `{ plates:[ {bbox, number, digits_conf, type: tounes|other, type_conf} ], annotated_image }`.
  - **Stretch-to-512 preprocessing** before the type classifier is mandatory (letterboxed input drops recall 96%→43%).
  - Optional per-plate vote across sequential frames (caller's concern or a light internal buffer).
  - **Deployment collection loop:** log `other` / low-confidence plates → accumulate rare-type data → later add `شرطة`/`حماية`/… as extra word classes (your "same for national guard").

**Phase C — traffic_analytics (stateful core + 3 analytics)** *(after the fast wins)*
- **Sprint 6 — Stream core:** stream ingestion, ByteTrack over the shared detector, per-track state, `geometry.py` (polygon ROIs + virtual lines), per-camera config, `push.py` callback emitter.
- **Sprint 7 — Bidirectional counting:** `line_crossing.py` — crossing test + direction (from right / from left), per-class per-direction counters, crossing events. Endpoint + config.
- **Sprint 8 — parking_time:** `parking_dwell.py` — stationary detection + dwell timer in ROI; duration query + over-stay events. Endpoint + config.
- **Sprint 9 — restricted_parking:** `restricted_zone.py` — forbidden-polygon occupancy + dwell → violation events with evidence. Endpoint + config.

**Phase D — Integration**
- **Sprint 10 — Agent/live_inference integration:** tool-wrappers for the stateless services (pull); callback wiring for traffic_analytics (push); optional `live_inference` API hookup.
- **Sprint 11 — Hardening:** batching, thresholds, per-class/per-camera config, Dockerfiles, OpenAPI docs, validation on real target footage.

---

## 8. Open / deferred decisions (recorded, not blocking)

- **Regional plate format** — **RESOLVED.** Tunisian plates (`NNNN تونس NNN` + special types شرطة/حماية/…). Solution: **digits via off-the-shelf PaddleOCR**; **plate type via تونس word-spotting classifier** (NOT full Arabic OCR — that hallucinated). No full ANPR fine-tune needed; only a small real-data word-detector was trained. Rare types deferred to deployment-time data collection.
- **Trained colour head** vs HSV baseline — deferred; revisit if HSV accuracy insufficient.
- **KICT as a training booster** (Option A: keep all labels, `01→car`/`02→bus`/`03-07→truck`, accept van-as-car noise) — only if streetdet-trained bus/truck underperform. Default: streetdet alone for v1.
- **Detector as shared inference server** (Triton) — optimization deferred until GPU memory pressure appears.
- **Plate per-track voting location** — inside plate_recognition (light buffer) vs caller's responsibility — decide in Sprint 9.

---

## 9. Datasets (decided)

- **Primary (vehicle type):** `streetdet` (Roboflow, v29, YOLO format, CC BY 4.0) — ~18.4k images, 9 classes incl. **van**; covers all six target classes. Train base.
- **Cross-domain test (bus/truck only):** `KICT_Traffic_Dataset` (PASCAL VOC, Korean highway CCTV) — remap `02→bus`, `03-07→truck`, exclude `01` (car/van conflated), drop dead `08-12`. Generalization probe, not training data (unless promoted per §8).
- **Colour:** v1 HSV heuristic (no dataset); optional later VeRi-776 / 8-colour set if a trained head is needed.
