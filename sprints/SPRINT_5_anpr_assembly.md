# Sprint 5 — ANPR assembly (plate_recognition service)

**Phase:** B (plate_recognition / ANPR)
**Depends on:** the three ANPR components (built ad-hoc — Sprint 4 was never formalized; this doc captures what was built and how).
**Goal:** Wire the three validated components — **plate detector + digit OCR + plate-type classifier** — into one stateless REST service `plate_recognition` that, given a frame or a plate crop, returns each plate's **number (digits) + type (tounes/other)** as JSON + an annotated image. Mirrors the structure of the working `vehicle_classification` service.

> **Note on Sprint 4:** there is no Sprint 4 doc. We skipped the planned "OCR engine + plate detector from scratch" because we already had a finetuned plate detector and an off-the-shelf OCR, and we discovered the right plate-type approach (word-spotting) through experimentation. This doc records the final components so the service can be built without re-deriving them.

---

## 1. The three components (already built & validated)

### 1a. Plate detector
- **What:** finetuned YOLO, single class `licence_plate`, tight plate boxes.
- **Weights:** `plate_recognition/models/best_matricule.pt` ✅ (already in place).
- **Inference:** `model.predict(frame, conf=0.25, imgsz=960)` → boxes `(x1,y1,x2,y2,conf)`. Run on a full frame (finds plates) or on a vehicle crop.
- **Source to port from:** `test_plate_detection.py` → `detect()`, and `extract_plate_crops.py`.

### 1b. Digit OCR
- **What:** PaddleOCR **PP-OCRv6** recognition, reads the plate **digits** (the registration number). Arabic is NOT OCR'd (it hallucinated — the Arabic/type is handled by 1c instead).
- **Engine — CRITICAL:** use `TextRecognition(model_name="PP-OCRv6_medium_rec", engine="transformers")`. The default PaddlePaddle inference engine crashes with `ConvertPirAttribute2RuntimeAttribute … onednn_instruction.cc`. The `transformers` engine runs through torch and avoids it.
- **Models:** auto-downloaded by PaddleOCR on first run (PP-OCRv6 det+rec); cached. No local file to manage.
- **Source to port from:** `test_plate_detection.py` → `class PlateReader` (copy it verbatim: the `__init__` with the transformers→pipeline fallback, and the defensive `_parse` handling `rec_texts` / `rec_text` / legacy formats). `read(crop) -> (text, mean_conf)`.
- **Deps:** `pip install paddleocr` + `transformers` (already installed in the venv).

### 1c. Plate-type classifier (word-spotting)
- **What:** detect the تونس word on the plate → present = `tounes` (civilian), absent = `other`.
- **Weights:** `plate_recognition/models/tounes_word_detector.pt` ✅ (already in place; copy of `runs_word/tounes_word/weights/best.pt`).
- **Recipe — CRITICAL preprocessing:** **stretch the plate crop to 512×512** (`cv2.resize(crop, (512,512))`) before detection. Letterboxed input drops recall to 43%; stretched gives ~96% (the training data was Roboflow "Resize 512×512 Stretch"). Then `model.predict(stretched, conf=0.5, imgsz=512)`. **conf ≥ 0.5** → `tounes` (type_conf = max box conf), else `other`.
- **Validated:** civilian recall 92.4%, references 7/7. conf 0.5 chosen so `nt` (`ن ت`, shares ت/ن, fires ~0.40) reads `other`.
- **Source to port from:** `word_classify.py` → the stretched `detect()` (the version with `cv2.resize(im,(512,512))`).

---

## 2. Models — locations & names (no manual args required)

Both models are already in `plate_recognition/models/`. The service config must point to them **by default** so imports succeed with zero CLI arguments:

| Component | File (canonical) | Config constant |
|-----------|------------------|-----------------|
| Plate detector | `plate_recognition/models/best_matricule.pt` | `PLATE_DETECTOR_PATH` |
| Type classifier | `plate_recognition/models/tounes_word_detector.pt` | `WORD_DETECTOR_PATH` |
| Digit OCR | (PaddleOCR PP-OCRv6, auto-download) | `OCR_REC_MODEL = "PP-OCRv6_medium_rec"` |

- Resolve paths **relative to the config file** (`Path(__file__).resolve().parent / "models" / "best_matricule.pt"`), with an **env override** (`PLATE_DETECTOR_PATH`, `WORD_DETECTOR_PATH`) — same pattern as `common/config.VEHICLE_MODEL_PATH`.
- **Relocation:** none needed — both `.pt` files are present. (If `tounes_word_detector.pt` is ever missing, copy it: `cp runs_word/tounes_word/weights/best.pt models/tounes_word_detector.pt`.)
- `models/plate_category_cls.pt` exists but is the **abandoned** synthetic classifier — do **not** use it.

---

## 3. Service architecture (mirror vehicle_classification)

```
plate_recognition/
  app.py            # FastAPI: /health, /read_plate, /read_plate_crop   (port 5021)
  plate_detector.py # PlateDetector(best_matricule.pt) → boxes
  ocr.py            # PlateReader (PaddleOCR PP-OCRv6, transformers engine) → digits
  plate_type.py     # classify_type(crop) → ('tounes'|'other', conf)   [stretch512 + word detector]
  config.py         # PORT=5021, model paths, thresholds
  schemas.py        # PlateDetection, PlateRecognitionResponse
  models/           # best_matricule.pt, tounes_word_detector.pt  (present)
```
**Reuse `common/`:** `image_io.load_image` (decode multipart/base64/url), the `annotate`/results-dir pattern, and the `config.RESULTS_DIR`. The pipeline mirrors `vehicle_classification/app.py` (which is the reference for the FastAPI structure, `_decode_image`, `return_image_inline`, envelope).

---

## 4. Pipeline & endpoint contract

```
POST /read_plate        (frame OR plate crop)        → detects plates, reads each
POST /read_plate_crop   (already a tight plate crop) → skip detect; OCR+type on the whole crop
GET  /health            → {status, models_loaded:{detector,word,ocr}}
```

**/read_plate flow:**
```
image (multipart file | image_b64) → decode (common.image_io)
  → PlateDetector.predict(image, conf=PLATE_CONF, imgsz=DETECT_IMGSZ) → plate boxes
  → for each box:
        crop = image[y1:y2, x1:x2]
        number, digits_conf = PlateReader.read(crop)            # digits
        ptype,  type_conf   = classify_type(crop)               # stretch512 → word detector → tounes/other
  → annotate (box + "number | type conf%") → save to results dir
  → JSON envelope
```

**/read_plate_crop flow:** input is a tight plate crop → run OCR + type on the whole image directly (optionally still run the detector and take the best box). Never error on "no plate"; return the crop-level read.

**Response schema** (`PlateRecognitionResponse`):
```json
{
  "request_id": "...", "timestamp": "ISO8601", "image_size": [w, h],
  "plate_count": 1,
  "plates": [
    {"bbox": [x1,y1,x2,y2], "number": "2765125", "digits_conf": 0.94,
     "type": "tounes", "type_conf": 0.69}
  ],
  "annotated_image": "/.../common/results/<id>.jpg",
  "annotated_image_b64": null
}
```
- `number`: OCR digits, normalized (join tokens, strip whitespace, keep alphanumerics).
- `type`: `"tounes"` | `"other"`. `type_conf`: word-detector max box conf (0.0 if `other`).
- Honour `return_image_inline: true` (base64), `conf` override — same as vehicle_classification.

---

## 5. Config (`plate_recognition/config.py`)

```python
PORT               = 5021
PLATE_DETECTOR_PATH = env("PLATE_DETECTOR_PATH", models/"best_matricule.pt")
WORD_DETECTOR_PATH  = env("WORD_DETECTOR_PATH",  models/"tounes_word_detector.pt")
OCR_REC_MODEL       = "PP-OCRv6_medium_rec"
PLATE_CONF   = 0.25   # plate detector
DETECT_IMGSZ = 960    # plate detector (full frame); helps small/distant plates
WORD_CONF    = 0.5    # type classifier threshold (tuned)
WORD_IMGSZ   = 512    # type classifier (after stretch-to-512)
```

---

## 6. Pitfalls / hard-won gotchas (read before coding)

1. **OCR engine = `transformers`.** Default PaddlePaddle inference crashes (oneDNN/PIR). Use `TextRecognition(..., engine="transformers")`; keep the pipeline-with-`enable_mkldnn=False` fallback.
2. **Type classifier MUST stretch to 512×512** before detection. This is non-negotiable — letterboxed input ≈ 43% recall vs ~96% stretched.
3. **OCR reads DIGITS only.** Do not try to OCR the Arabic. Plate `number` = digits; plate `type` = word-spotting. They are separate signals, combined in the response.
4. **Type threshold 0.5.** Lower lets `nt`/partial-matches false-fire as `tounes`; higher costs civilian recall. 0.5 is the validated operating point.
5. **Plate detector:** single class `licence_plate`, conf 0.25; on full frames use imgsz≈960.
6. **Stateless service.** Per-plate voting across sequential frames is **deferred** (caller's concern or a future light buffer) — do not build it now.
7. Load all three models **once at startup** (module-level singletons), thread-locked `predict` (the FastAPI server is threaded) — same as `common/detector.py` / `vehicle_classification`.

---

## 7. Acceptance criteria

- `python -m uvicorn plate_recognition.app:app --port 5021` starts; `/health` shows detector + word + ocr loaded. **No manual model-path args needed** (config defaults resolve to `models/`).
- `POST /read_plate` with a real plate image → returns ≥1 plate with a digit `number`, a `type` (`tounes`/`other`), confidences, and a saved annotated image showing `number | type` on the box.
- On a **civilian** plate → `type: tounes`; on a special plate → `type: other`.
- Sanity test sources on hand: `tunisian_plate/` (refs), `civilian_crops/` (tight civilian crops), or any frame with a plate. (Validate `/read_plate_crop` on `civilian_crops/`.)
- Reuses `common/` (`image_io`, results dir, annotate pattern); structure mirrors `vehicle_classification/app.py`.

---

## 8. Deployment collection loop (build the hook now, even if minimal)

Log every plate classified `other` or below a confidence floor (crop + metadata) to a `plate_recognition/review/` dir (or via the push pattern). This accumulates real rare-type data over time so the type classifier can later be extended to `شرطة`/`حماية`/`الحرس الوطني`/… as additional word classes (one multi-word detector) — the "same for national guard" extension. Without this hook, the rare-class data never builds.

---

## 9. Out of scope (deferred)

- Per-plate temporal voting across frames.
- Multi-word type classifier (police/civil-protection/etc.) — needs the collected data from §8.
- Plate DB / watchlist matching.
- Integration into the agent/live_inference (Phase D, Sprint 10).
