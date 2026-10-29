# License Plate Plugin

Reusable extraction of the last successful WAYCON Tunisian plate-category method. It accepts a **complete, tightly framed plate crop** and predicts one of:

- `idara`
- `nt`
- `shorta`
- `tunis`

The fixed method is DINOv3 ViT-S/16 with the R2 whole/left/centre/right representation, cosine reference matching, complete-plate Arabic PaddleOCR, and a conservative positive NT override. It does not detect plates, track vehicles, transcribe registration numbers, train models, run a web service, or integrate with the vehicle application.

## Package layout

```text
License plate plugin/
  license_plate_plugin/       Python package
  configuration/              Reproduction and portable templates
  assets/references/          Exact 12 reviewed reference crops + manifest
  assets/reference_index/     Frozen R2 embeddings + compatibility metadata
  assets/models/              DINOv3 checkpoint + Arabic PaddleOCR assets
  assets/vendor/dinov3/       Pinned official Meta DINOv3 repository
  assets/licenses/            DINOv3 license copy
  scripts/                    Reproduction helper
  tests/                      Focused unit checks
  verification/               Completed leave-one-out parity outputs
  pyproject.toml
  EXTRACTION_REPORT.md
```

The Python import name is `license_plate_plugin`.

## Required crop quality

Input must be a decoded colour image containing one complete plate. Preserve its entire border, category marking, Arabic dots, and registration area. The classifier does not tighten, deskew, detect, or repair a crop. Results from incomplete or vehicle-level images are outside the verified behavior.

## Model and reference assets

Included:

- all 12 exact reviewed reference PNGs;
- the frozen R2 reference index;
- `arabic_PP-OCRv5_mobile_rec` inference files;
- Meta's official DINOv3 repository at revision `6876159a11b4df116f30f667f8c9888617df0751`;
- `dinov3_vits16_pretrain_lvd1689m-08c60483.pth`, SHA-256 `08c60483bc63c04f533611e34bf70b120eedb7240f469bc16e9e20bf344b941d`;
- an exact copy of the DINOv3 license from the pinned official repository.

All runtime model and reference assets are inside this folder. The package never downloads weights. Paths are resolved relative to the configuration file, not the current working directory, so the complete folder can be moved as a unit.

Review [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) before redistribution.

## Installation

Use the working Python 3.11 environment or an isolated environment with compatible Torch, PaddleOCR, PaddlePaddle, OpenCV, Pillow, and NumPy versions.

```bash
/home/mohamed/miniconda3/envs/ml/bin/python -m pip install -e \
  "/mnt/d/temporary/test/plate_similarity_test/License plate plugin"
```

The verified environment used Python 3.11.15, Torch 2.12.1+cu126, PaddleOCR 3.7.0, PaddlePaddle 3.2.0, OpenCV 5.0.0.93, and NumPy 2.3.5. Copying the models does not copy Python dependencies; use this environment or install compatible packages separately.

Alternatively, set `PYTHONPATH` without installing:

```bash
export PYTHONPATH="/mnt/d/temporary/test/plate_similarity_test/License plate plugin"
```

## Configuration

`configuration/local_reproduction.json` and `configuration/template.json` both point to model assets inside this plugin. Normally only `device` and `ocr_device` need changing.

## Quick run

The wrapper resolves the plugin path automatically, even when invoked from another directory:

```bash
cd "/mnt/d/temporary/test/plate_similarity_test/License plate plugin"
PLUGIN_PYTHON=/home/mohamed/miniconda3/envs/ml/bin/python \
  ./run_plugin.sh classify "/path/to/complete_plate.png" \
  --output-dir "/tmp/plate_result"
```

For a folder:

```bash
PLUGIN_PYTHON=/home/mohamed/miniconda3/envs/ml/bin/python \
  "/mnt/d/temporary/test/plate_similarity_test/License plate plugin/run_plugin.sh" \
  classify-folder "/path/to/complete_plate_crops" \
  --output-dir "/tmp/plate_folder_results"
```

Changing the model, checkpoint, preprocessing policy, region definition, feature dimensions, or official repository revision invalidates the frozen reference index. Loading fails when its compatibility fingerprint differs; stale embeddings are never silently accepted.

## Build or reuse the reference index

The included index is ready to use:

```text
assets/reference_index/reference_r2.npz
assets/reference_index/reference_r2.json
```

To rebuild it, first move the existing two index files to a backup location or select a new `--output` path. The command refuses overwrite:

```bash
/home/mohamed/miniconda3/envs/ml/bin/python -m license_plate_plugin \
  --config "/mnt/d/temporary/test/plate_similarity_test/License plate plugin/configuration/local_reproduction.json" \
  build-index \
  --output "/tmp/reference_r2_rebuild.npz"
```

Index construction performs four DINO forwards per reference, saves 12 normalized 1,536-D vectors, reference IDs/categories/hashes, model and repository identity, preprocessing policy, and a canonical compatibility fingerprint.

## Classify one new crop

```bash
/home/mohamed/miniconda3/envs/ml/bin/python -m license_plate_plugin \
  --config "/mnt/d/temporary/test/plate_similarity_test/License plate plugin/configuration/local_reproduction.json" \
  classify "/path/to/complete_plate.png" \
  --output-dir "/tmp/plate_result"
```

This compares the query against the complete frozen 12-reference gallery. Do not use a reference crop itself as evidence of evaluation performance: if it remains in the gallery, it will self-match.

## Classify a folder

```bash
/home/mohamed/miniconda3/envs/ml/bin/python -m license_plate_plugin \
  --config "/mnt/d/temporary/test/plate_similarity_test/License plate plugin/configuration/local_reproduction.json" \
  classify-folder "/path/to/complete_plate_crops" \
  --output-dir "/tmp/plate_folder_results"
```

The folder command writes `results.json`, `results.csv`, per-image OCR submissions, and full structured records.

## Python API

```python
from pathlib import Path
from license_plate_plugin import PlateCategoryClassifier, load_config

config = load_config(
    "/mnt/d/temporary/test/plate_similarity_test/License plate plugin/"
    "configuration/local_reproduction.json"
)

# Models and the index load once and are reused.
classifier = PlateCategoryClassifier(config)
result = classifier.classify(
    "/path/to/complete_plate.png",
    Path("/tmp/plate_result"),
)

print(result["visual"]["visual_category"])
print(result["visual"]["nearest_reference_id"])
print(result["visual"]["nearest_similarity"])
print(result["visual"]["predicted_category_margin"])
print(result["ocr"]["raw_text"])
print(result["ocr"]["native_confidence"])
print(result["ocr"]["nt_evidence"])
print(result["decision"]["final_category"])
```

## Output fields

The JSON result contains:

- `input`: path, SHA-256 and decoded dimensions;
- `visual`: category, nearest reference, cosine similarity, predicted-category margin, tie flag, R2 dimension and per-view geometry/timing;
- `ocr`: exact raw text, native confidence, normalized text, parser boundary text, NT evidence, conflicts, inference status/failure, submitted dimensions and native payload;
- `decision`: visual and final categories, decision source, override, conflict, OCR failure and tie flags;
- `timing`: visual/R2, OCR and total measured durations;
- `index`: index path and compatibility fingerprint.

OCR digits are raw model output and are **not verified registration text**. Cosine similarity and Paddle's native confidence are not calibrated category probabilities. No rejection threshold is applied.

## Development reproduction

The valid reproduction excludes each reference image from its own gallery:

```bash
/home/mohamed/miniconda3/envs/ml/bin/python -m license_plate_plugin \
  --config "/mnt/d/temporary/test/plate_similarity_test/License plate plugin/configuration/local_reproduction.json" \
  evaluate \
  --output-dir "/tmp/plugin_leave_one_out" \
  --authoritative-csv \
  "/mnt/d/temporary/test/plate_similarity_test/License plate plugin/verification/authoritative_successful_results.csv"
```

Verified extraction result:

- R2 visual: 11/12;
- final fusion: 12/12;
- only image 5 overridden;
- all visual categories and nearest-reference IDs reproduced;
- similarities reproduced within `1e-6`;
- all raw OCR strings, parser decisions and final categories reproduced.

The original extraction evidence is under `verification/leave_one_out_final/`. The post-copy run using the vendored DINO repository and checkpoint is under `verification/vendored_leave_one_out/`; it also passed with no parity differences.

## Tests

From any working directory:

```bash
PYTHONPATH="/mnt/d/temporary/test/plate_similarity_test/License plate plugin" \
/home/mohamed/miniconda3/envs/ml/bin/python -m unittest discover \
  -s "/mnt/d/temporary/test/plate_similarity_test/License plate plugin/tests" -v
```

## Limitations

- The gallery contains only 12 repeatedly used development plates.
- Physical independence is unverified.
- Supported outputs are forced to four known categories; no unknown/rejection policy exists.
- Reference diversity is very limited.
- The method depends on complete reviewed plate crops.
- NT OCR confidence is not calibrated.
- Registration-digit recognition is not established.
- Automatic plate localization and production integration are out of scope.


## Shared repository import and in-memory interface

Django and standalone runtimes should install this **same source checkout** in
editable mode using their own Python environment. From any directory:

```bash
/path/to/runtime/python -m pip install --no-deps --no-build-isolation -e \
  /home/intern/part-time/mohamed/pipeline/services/license_plate_service/LicensePlatePlugin
```

This requires the existing runtime dependencies and setuptools build backend.
It installs the import package without copying model assets or changing their
configuration. Keep the entire plugin directory in place. Regular wheel asset
packaging is not provided by this change. No recognition runner imports or calls
the classifier yet.

Both runtimes can use the same package imports and package-relative configuration:

```python
import cv2
from license_plate_plugin import PlateCategoryClassifier, load_default_config

classifier = PlateCategoryClassifier(load_default_config())  # Once per owner/worker

# Existing file API: source file SHA-256 and saved unpadded OCR submission.
file_result = classifier.classify("/data/plate.png", "/tmp/file-diagnostics")

# New API: no input crop or diagnostic file is written by default.
plate_bgr = cv2.imread("/data/plate.png")
array_result = classifier.classify_bgr(plate_bgr)

# Optional diagnostics; use a distinct directory per retained call.
with_diagnostics = classifier.classify_bgr(plate_bgr, "/tmp/array-diagnostics")
```

`classify_bgr` requires a nonempty `numpy.uint8` array shaped `(H, W, 3)` in
BGR order. It copies caller storage before processing and accepts read-only or
non-contiguous arrays. Both APIs share feature extraction, reference matching,
OCR, NT parsing and final decisions. Both accept `exclude_reference_ids`,
`exclude_groups` and `expected_category` keyword arguments.

Array input metadata reports `kind="bgr_array"`, `dtype`, `color_order`, width
and height. It has no source `path` or file `sha256`. With no output directory,
Arabic OCR receives BGR pixels directly and `ocr.submitted_path` is null. With
diagnostics, it receives the saved lossless PNG, preserving the existing file
behavior. Native Paddle payloads can differ in input-path provenance; timing
also varies. Classification evidence and decision semantics are unchanged.

Focused tests use mocked models with real preprocessing. Optional checks require
the installed package, local assets and complete runtime dependencies:

```bash
/path/to/runtime/python -B -m unittest discover -s /absolute/plugin/tests -v
PLATE_PLUGIN_REAL_INFERENCE=1 /path/to/runtime/python -B -m unittest discover \
  -s /absolute/plugin/tests -p test_real_interface_parity.py -v
PLATE_PLUGIN_REAL_OCR=1 /path/to/runtime/python -B -m unittest discover \
  -s /absolute/plugin/tests -p test_real_interface_parity.py -v
PLATE_PLUGIN_IMPORT_AUDIT=1 /path/to/runtime/python -B -m unittest discover \
  -s /absolute/plugin/tests -p test_integration_imports.py -v
```

The import audit uses fresh processes in an unrelated working directory and
checks the Django and standalone recognition bindings without running inference.
