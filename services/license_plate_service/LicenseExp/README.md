# Tunisian plate visual-embedding proof of concept

This standalone experiment compares frozen `facebook/dinov2-small` CLS embeddings for exactly three Tunisian license-plate crops. It supports already-cropped plates or YOLO detection followed by padded cropping. It does not train a model, run OCR, classify plate categories, or use alternative encoders.

The default preprocessing preserves every selected plate pixel. It uses proportional variable-size resizing, minimal patch-alignment padding, DINOv2's built-in rectangular positional-embedding interpolation, float32 CLS extraction, L2 normalization, and cosine similarity.

## Setup

Python 3.10 or newer is recommended:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Model loading uses local Hugging Face cache files by default. Add `--allow-download` only when a download is intended. CUDA is selected automatically when available; otherwise inference uses CPU.

## Resolution modes

Select a mode with `--preprocessing-mode`:

- `budget` (default) starts with `sqrt(target_area / crop_area)`, proportionally resizes both dimensions, limits the resized long edge, and reduces scale further when necessary to respect the padded patch budget. Defaults are `--target-area 50176`, `--max-long-edge 1008`, and `--max-patch-count 512`.
- `native` performs no resizing and adds only patch-alignment padding. Patch-count and long-edge limits apply only to budget mode.
- `original` reproduces the model processor's unchanged short-edge resize and 224×224 center crop. It is retained for reproducibility and can discard most of a wide plate.

The model configuration supplies the patch size; DINOv2-small currently reports 14×14. Variable modes round proportional dimensions half-up, then center the image in the smallest patch-aligned rectangle. When padding is odd, the extra pixel goes to the right or bottom. Default fixed padding is RGB `(124, 116, 104)` and is configurable with `--padding-rgb R G B`. Bicubic resizing is the default; use `--resize-interpolation` to choose another explicit policy.

These resolution defaults are experimental choices, not established optimal values. They do not impose a fixed width, height, or aspect ratio.

## Run the updated experiment

Already-cropped plates:

```bash
python compare_plate_embeddings.py \
  --cropped-plates \
  --image1 nt1.jpg --image2 nt2.jpg --image3 tunis1.jpg \
  --output-dir variable_outputs_run1
```

Full vehicle images:

```bash
python compare_plate_embeddings.py \
  --image1 data/car1.jpg --image2 data/car2.jpg --image3 data/car3.jpg \
  --yolo-weights best.pt --det-conf 0.25 --crop-padding 0.05 \
  --output-dir variable_outputs_cars_run1
```

Native and historical preprocessing are explicit alternatives:

```bash
python compare_plate_embeddings.py ... --preprocessing-mode native
python compare_plate_embeddings.py ... --preprocessing-mode original
```

Output directories must be new; the tools refuse to overwrite existing directories. Outputs include clean crops, resized images, padded or center-cropped inputs, exact-tensor display reconstructions, a comparison figure, and machine-readable similarity results.

## Compare all preprocessing methods

Run all three methods on identical decoded sources and identical selected crops with one loaded model:

```bash
python compare_preprocessing_methods.py \
  --cropped-plates \
  --image1 nt1.jpg --image2 nt2.jpg --image3 tunis1.jpg \
  --output-dir preprocessing_comparison_run1
```

The comparison saves per-image crop-stage figures, clean intermediate PNGs, `overall_comparison.png`, `comparison_table.md`, and `comparison_results.json`. The JSON records source hashes, detection/crop coordinates, model revision, processor and dependency settings, dimensions, padding, patch grids/counts, tensor hashes, embedding norms, similarities, and differences from original preprocessing.

Methods have different patch counts and therefore different compute and memory costs. The comparison does not measure latency.

## Verification

Run focused preprocessing checks with:

```bash
python -m unittest discover -s tests -v
```

The checks cover wide, tall, tiny, odd-sized, and patch-aligned images; padding arithmetic; full-region preservation; aspect-ratio rounding; long-edge and patch-count limits; invalid configurations; and exact tensor-display reconstruction. The runtime comparison additionally verifies rectangular model inference and 384-dimensional embeddings.

## Evaluate a labeled plate folder

Place already-cropped plates under one subdirectory per category, for example
`plates/idara/`, `plates/nt/`, `plates/shorta/`, and `plates/tunis/`. Evaluate every
unique image pair with all three preprocessing methods using:

```bash
TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1 python evaluate_plate_dataset.py \
  --dataset-dir plates --output-dir plate_dataset_evaluation_v1
```

The output includes all-pairs and similarity-matrix CSV files, machine-readable
JSON, exact-tensor input reconstructions, heatmaps, score distributions, a
preprocessing gallery, and a Markdown report. Category names come only from the
immediate parent directory. The tool refuses to reuse a non-empty output
directory, so choose a new run name when repeating an evaluation.

The reported same/different summaries, ROC AUC, and nearest-neighbor label match
are descriptive diagnostics on the supplied images—not held-out classification
accuracy. Small or related samples can overstate generalization.

## Controlled colour/grayscale/CLAHE preparation

`controlled_plate_manifest.json` defines the exact numbered inputs, labels,
registration metadata, marker boxes, and optional physical/group identifiers.
Marker boxes are validation metadata only; they are never used as crop geometry.
The numbered inputs are already complete plate crops, so this experiment does
not require bounding boxes, corners, further cropping, or perspective correction.

Validate the exact files without loading DINOv2:

```bash
python evaluate_plate_dataset.py \
  --controlled-manifest controlled_plate_manifest.json \
  --validate-only --output-dir controlled_validation_run4
```

Run historical P0 baselines only, or the complete controlled experiment:

```bash
python evaluate_plate_dataset.py \
  --controlled-manifest controlled_plate_manifest.json --baseline-only \
  --output-dir controlled_baseline_run1

python evaluate_plate_dataset.py \
  --controlled-manifest controlled_plate_manifest.json \
  --output-dir controlled_colour_gray_clahe_run1
```

Every output directory must be new. The complete run evaluates original,
native, and budget model-input baselines on unchanged colour P0. It then compares
P0 with replicated grayscale and fixed Lab-luminance CLAHE using the same budget
policy. It writes all 45 unordered pairs separately from leave-one-out 1-NN
category predictions. These ten development examples do not establish
generalization. Perspective correction is postponed.

## Interpretation and limitations

The installed DINOv2 implementation interpolates learned positional embeddings to each actual rectangular patch grid. This enables different input shapes, but it does not restore missing source detail, make embeddings resolution-invariant, or guarantee that one preprocessing mode is more accurate.

Cosine scores only rank the three embeddings in a run. Higher similarity is not a category-accuracy metric and does not establish category identity, generalization, or that preprocessing caused a score change. A later evaluation would require many labeled, leakage-controlled samples and an explicit accuracy protocol.

See [INPUT_AUDIT_REPORT.md](INPUT_AUDIT_REPORT.md) for the original center-crop audit.

## Fixed representation experiment

Run the separate colour-only R0–R3 experiment without changing historical
dataset evaluation behavior:

```bash
MPLCONFIGDIR=/tmp/matplotlib-representations \
TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1 \
python evaluate_plate_representations.py \
  --manifest controlled_plate_manifest.json \
  --prior-p0-results-dir controlled_colour_gray_clahe_v1 \
  --output-dir representation_experiment_v2
```

R0 is whole-plate CLS, R1 uniformly averages spatial patch tokens that intersect
the unpadded resized image, and R2 averages four fixed corresponding-view cosine
scores (whole, left, centre, right) with weight 0.25 each. R3 uses only the seven
supplied manual marker crops. R0/R1 share one forward output; R2 needs four image
views per plate. All embeddings are cached before pairwise and leave-one-out
evaluation. R3 is manual-localization-assisted and does not evaluate `idara`.

## Encoder, colour, and marker-shape experiment

Run the standalone comparison with cached checkpoints only:

```bash
MPLCONFIGDIR=/tmp/matplotlib-encoder-branches \
TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1 \
python evaluate_encoder_colour_shape.py \
  --manifest controlled_plate_manifest.json \
  --prior-representation-dir representation_experiment_v3 \
  --output-dir encoder_colour_shape_v3
```

Add `--allow-download` only when public model access and network connectivity are
available. The command never substitutes a different checkpoint: unavailable
encoders receive explicit invalid pair/query rows and availability records.

The exact DINOv3 checkpoint is gated. First accept its license at
`https://huggingface.co/facebook/dinov3-vits16-pretrain-lvd1689m`, authenticate
the approved Hugging Face account locally, and confirm that `huggingface.co` is
reachable. Then rerun with `--allow-download`. A complete previously downloaded
Hugging Face snapshot can instead be supplied with
`--dinov3-model-source /path/to/snapshot`; a raw Meta `.pth` backbone file is
not a Transformers snapshot and is not accepted by this adapter.

E0 uses the historical DINOv2-small budget input. E1 targets the exact
DINOv3-S/16 web checkpoint, and E2 targets the official SigLIP 2 base NaFlex
processor and image-feature path. C0 compares fixed joint HSV histograms; C1
compares fixed Otsu bright/dark-group HSV histograms. S0 and S1 compare the seven
manual marker crops with symmetric template correlation and edge chamfer,
respectively. Raw scores are not comparable between methods. Outputs include
long-form pair/query CSVs, explicit coverage, diagnostics, exact available-model
input views, caches, configuration, and a Markdown report. The ten images remain
development data, and manual-marker results do not demonstrate automatic marker
localization.

## Arabic category-marker OCR

The standalone Arabic experiment uses only manual marker crops 4–10 and the
official `arabic_PP-OCRv5_mobile_rec` recognizer. It bypasses text detection and
does not process registration digits. Keep Paddle dependencies isolated:

```bash
/home/mohamed/miniconda3/envs/ml/bin/python -m venv .venv-arabic-ocr
.venv-arabic-ocr/bin/python -m pip install paddlepaddle==3.2.0 \
  -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
.venv-arabic-ocr/bin/python -m pip install -r requirements-arabic-ocr.txt

MPLCONFIGDIR=/tmp/matplotlib-arabic-ocr \
.venv-arabic-ocr/bin/python evaluate_arabic_marker_ocr.py \
  --manifest controlled_plate_manifest.json \
  --previous-results encoder_colour_shape_online_v4 \
  --output-dir arabic_ocr_ppocrv5_run1 --device cpu
```

Use `--prepare-only` to validate and export the supplied crops when the OCR
runtime or model is unavailable. Every run requires a new output directory.
The fixed normalizer removes presentation-form differences, diacritics,
tatweel, and whitespace while preserving base letters and dot distinctions.
Only exact normalized `نت`, `شرطة`, and `تونس` outputs map to categories;
everything else remains `unknown`.

Compare the completed Arabic baseline with fixed Otsu and Canny inputs using:

```bash
MPLCONFIGDIR=/tmp/matplotlib-arabic-filters \
PADDLE_PDX_MODEL_SOURCE=BOS \
python evaluate_arabic_ocr_filters.py \
  --manifest controlled_plate_manifest.json \
  --baseline-dir arabic_ocr_ppocrv5_v4 \
  --output-dir arabic_ocr_filters_run1 --device cpu
```

This comparison preserves every crop's dimensions and tests only original,
Otsu, Canny `(100, 200, apertureSize=3)`, and Otsu followed by the same Canny
operation. Processed one-channel pixels are replicated into three channels;
there is no resize, blur, sharpening, morphology, inversion, decoder change, or
per-image selection.

For the expanded nine-marker dataset (four NT, two Shorta, three Tunis), use the
separate approved manifest and entry point:

```bash
MPLCONFIGDIR=/tmp/matplotlib-arabic-extended \
PADDLE_PDX_MODEL_SOURCE=BOS \
python evaluate_arabic_ocr_extended.py \
  --manifest arabic_ocr_extended_manifest.json \
  --baseline-dir arabic_ocr_ppocrv5_v4 \
  --output-dir arabic_ocr_extended_run1 --device cpu
```

The extended manifest preserves the corrected original boxes, maps renamed
legacy files back to their v4 IDs for reproduction checks, and records the
user-approved boxes for new NT images 6 and 7. It does not modify the historical
ten-image controlled manifest.
