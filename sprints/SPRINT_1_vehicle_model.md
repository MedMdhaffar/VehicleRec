# Sprint 1 — Vehicle model (training)

**Phase:** A (vehicle_classification)
**Depends on:** Sprint 0
**Goal:** Produce the fine-tuned **yolo26** vehicle detector from the streetdet dataset, evaluate it honestly (in-distribution on streetdet val for *all* classes incl. van, and cross-domain on KICT for bus/truck only), and deliver the weights + class map into `common/models/`.

---

## Why this sprint

This is the model the whole module hangs off. The service layer (Sprint 2) is thin; the quality lives here. The two non-obvious things this sprint must get right: **(a) not poisoning the model by deleting labels**, and **(b) an honest evaluation** that separately reports van (our gap class) and cross-domain generalization.

---

## Inputs

- **Training data:** `vehicle_services/vehicle_class1/` (streetdet, YOLO format).
  - 16,902 train / 1,498 valid images. **No test split.**
  - 9 classes: `['bus','camper','car','cyclist','motorcycle','person','tractor','truck','van']`.
  - Train instances: car 15,073 · person 8,813 · motorcycle 4,006 · cyclist 2,536 · truck 2,495 · van 1,408 · bus 1,226 · camper 444 · **tractor 33 (dead)**.
- **Cross-domain test data:** KICT at `C:\Users\malek\Desktop\vehicle_cllass` (PASCAL VOC, Korean highway CCTV). Used **bus/truck only** via remap.

---

## Key decisions (read before coding)

1. **Train on all 9 classes as-is — do NOT delete labels.**
   streetdet is a *detection* dataset: every object in an image is labelled. If we delete `person`/`camper`/`tractor` labels but keep the images, those objects become **unlabelled background**, which teaches the model to *suppress* them — poisoning training (the classic partial-label trap). So we keep all labels. **Taxonomy reduction (to the boss's 6 types) happens at the service layer in Sprint 2 (filter/merge), not by deleting training labels.**
   - *Optional, allowed:* **merging** labels is safe (the object stays labelled): e.g. `tractor→truck`, `camper→van`. A remap script may do this. Deleting is the only forbidden operation.
   - **tractor (33 instances)** will barely be learned regardless; either leave it (harmless) or merge into `truck`. Default: leave as-is for v1.

2. **Fix dataset paths.** `data.yaml` uses `../train/images` (Roboflow assumes the yaml sits one level above the splits). Since the yaml is *inside* the dataset folder, create a training yaml with correct paths (`train: vehicle_class1/train/images`, `val: vehicle_class1/valid/images`), and **remove the `test:` line** (no test split exists).

3. **Model size.** Use **yolo26l** (consistent with the existing services that run `yolo26l.pt`). If training/inference is too slow on target hardware, fall back to yolo26m. Record the choice.

4. **Class imbalance.** car (15k) dwarfs bus/van/truck (1.2–2.5k). Start with default ultralytics training + monitor **per-class** metrics. If van/bus recall is weak, apply mitigation (oversample rare-class images, or class weighting) — don't pre-optimize.

5. **KICT is test-only, not training data** (for v1). It's the cross-domain probe. Promoting it to a training booster is a deferred decision (`ROADMAP.md` §8) taken only if streetdet-trained bus/truck underperform.

---

## Deliverables

```
training/
  prepare_streetdet.py     # build corrected train yaml; print class distribution; sanity-check labels
  voc_to_yolo_kict.py      # KICT PASCAL VOC → YOLO; remap 02→bus, 03–07→truck; drop 01 and 08–12
  train.py                 # yolo26 fine-tune (ultralytics), configurable
  eval.py                  # streetdet val (all classes) + KICT (bus/truck) report
  configs/
    streetdet_train.yaml   # corrected paths, nc, names
    kict_eval.yaml         # KICT remapped to service class indices (bus/truck)
  runs/                    # ultralytics outputs (git-ignored)
common/models/
  vehicle_yolo26.pt        # best weights (delivered)
  class_map.json           # model class-id → name (and the Sprint-2 service taxonomy mapping)
EVAL_REPORT.md             # metrics: overall + per-class mAP, van highlighted, KICT bus/truck
training/eval_tool/        # real-footage manual evaluation tool (see §"Real-footage eval tool")
  backend.py               # video → sample frames → detect → serve frames+boxes → score
  frontend/                # simple UI: paths in, frame-by-frame review, metrics out
  test_sets/               # saved labeled real-domain test sets (reusable across model versions)
```

---

## Tasks

1. **`prepare_streetdet.py`**
   - Generate `configs/streetdet_train.yaml` with corrected paths and no `test:` line.
   - Print/save the per-class instance distribution (train + valid) for the report.
   - Sanity-check: every label file's class ids ∈ [0,8]; bbox coords normalized ∈ [0,1]; image/label filename parity.

2. **`voc_to_yolo_kict.py`** (the cross-domain test converter)
   - Parse KICT `train/xml/*.xml` (PASCAL VOC, absolute pixel boxes; image size in `<size>`).
   - **Remap to the *service* class space:** KICT `02 → bus`, KICT `03,04,05,06,07 → truck`. **Drop** KICT `01` (car/van conflated — unscoreable) and `08–12` (dead).
   - Convert boxes to normalized YOLO format; write labels + a `kict_eval.yaml` whose `names`/indices match the trained model's `bus`/`truck` ids.
   - Note: KICT lives on the Windows desktop; read from `/mnt/c/Users/malek/Desktop/vehicle_cllass` (WSL) or copy into `training/datasets/kict/`.

3. **`train.py`**
   - Ultralytics yolo26l fine-tune from a pretrained checkpoint.
   - Configurable: `epochs` (start ~80–100, early-stop on val), `imgsz` (start 640; consider 960 for small/distant vehicles), `batch` (per GPU memory), `device`, augmentation (mosaic on, plus standard flips/HSV).
   - Save best checkpoint → `common/models/vehicle_yolo26.pt`; write `class_map.json`.

4. **`eval.py`**
   - **In-distribution:** evaluate on streetdet `valid` → overall mAP@50 / mAP@50-95 **and per-class** metrics. **Highlight van** (precision/recall/mAP) — this is the gap class we built this for.
   - **Cross-domain:** evaluate on the KICT-remapped set → **bus and truck only** mAP/precision/recall. Clearly label this as Korean-highway generalization, not target-domain.
   - Emit `EVAL_REPORT.md`.

5. **Deliver** weights + `class_map.json` into `common/models/`; point `common/config.VEHICLE_MODEL_PATH` at it.

---

## Real-footage eval tool (the third leg of evaluation)

Beyond streetdet val (in-distribution) and KICT (cross-domain), this is the **target-domain** check: run the model on a recorded CCTV video, hand-label ground truth, and score it. This is how we "note" each model version on footage that actually resembles deployment.

**Methodology (decided):**
- **A row = one detected vehicle (crop)** with predicted class + confidence.
- **Frame-centric review** — frames are sampled (~1/sec, configurable) to avoid scoring thousands of near-duplicate consecutive frames; the human reviews each sampled frame with the model's boxes overlaid.
- Per box, ground truth ∈ {a real class, **false_positive**}. The `false_positive` option is mandatory or precision can't be measured.
- **Missed vehicles** are captured by a **click (logging class)** or a per-class missed tally on the frame — no full-box drawing required. This gives true recall.
- **"Should-detect" policy** (written into the tool + report): only count a vehicle as ground truth if clearly identifiable and above a minimum size / within the usable zone — so recall isn't punished by un-detectable tiny/occluded vehicles. Applied consistently across sessions.
- **Metrics from the table:** `FP` = box marked false_positive, or predicted≠truth; `FN` = missed + (truth=c but predicted other); `TP` = predicted==truth. Then per-class `precision`, `recall`, `F1`, plus a confusion matrix, **van highlighted**.
- **Persist** every `{frame_id, crop, bbox, predicted, confidence, ground_truth}` + the missed list to `test_sets/` (CSV/JSON) → a reusable real-domain test set scored against future model versions with zero re-labeling.

**Tool tasks:**
1. **`backend.py`** — inputs: `video_path`, `model_path`, `sample_rate`, `conf`. Sample frames → run detector → store per-frame detections (crops + boxes + conf) → serve to the UI → receive human labels → compute metrics → write `test_sets/<run>.json` + a metrics summary.
2. **`frontend/`** — simple UI: fields for `video_path` + `model_path` (+ sample rate, conf); a frame-by-frame review screen showing the frame with overlaid boxes, a class dropdown (incl. `false_positive`) per box, and a click/counter to log missed vehicles; a final metrics view (per-class precision/recall/F1 + confusion matrix + van highlighted).
3. *(optional)* reference-detector assist to pre-suggest candidate missed boxes; human confirms.

This is a **dev/eval utility, separate from the production API** — it never ships inside the `vehicle_classification` service.

---

## Acceptance criteria

- `train.py` runs to convergence; `runs/` contains the training curves and confusion matrix.
- `EVAL_REPORT.md` exists and reports: overall mAP, **per-class** mAP, an explicit **van** number, and **KICT bus/truck** cross-domain numbers.
- A baseline is established for every one of the six target classes (car/truck/bus/van/motorcycle/cyclist) — even if some are weak, the numbers are recorded so Sprint 2 thresholds and any later data-boost decision are data-driven.
- `common/models/vehicle_yolo26.pt` + `class_map.json` present and loadable by a quick ultralytics `YOLO(path)` smoke test.
- **Real-footage eval tool runs end-to-end:** given a CCTV video + model path, it samples frames, detects, lets the human resolve boxes + mark missed vehicles, and outputs per-class precision/recall/F1 + a confusion matrix (van highlighted), then saves the labeled test set to `test_sets/` for reuse.

---

## Notes / risks

- **Domain gap:** streetdet is general web/street imagery, not target (Tunisia) footage. Numbers here are a baseline; plan a later fine-tune on real target frames (Phase D / ROADMAP §8).
- **van** at ~1,558 instances is trainable but modest — watch its recall; this is the metric that justifies the whole custom-model effort.
- **tractor** will be near-zero AP; acceptable (or merge to truck).
- **yolo26 availability:** ensure the installed ultralytics version exposes yolo26 weights; if not, pin the version or substitute the available current YOLO (record it).
- KICT remap must land in the **same class indices** the model uses for bus/truck, or `eval.py` will mis-score.
