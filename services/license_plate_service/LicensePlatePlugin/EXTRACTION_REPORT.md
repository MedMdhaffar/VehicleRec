# Extraction Report

## Outcome

The last successful category method was extracted into a standalone Python package. It imports no module from the original project and resolves all data/model paths through an explicit JSON configuration. The extraction adds reusable new-image inference, folder inference, frozen reference-index persistence, compatibility fingerprints, and a separate leave-one-out reproduction path without changing the algorithm.

## Source-to-package mapping

| Original source | Extracted component |
|---|---|
| `plate_preprocessing.py` budget functions | `license_plate_plugin/preprocessing.py` |
| `dinov3_official_adapter.py` and official output usage | `license_plate_plugin/dinov3.py` |
| `plate_representations.py` R2 regions/CLS/combination | `license_plate_plugin/representations.py` |
| `plate_variants.py` validation, group exclusion, tie logic | `manifest.py`, `reference_index.py`, `evaluation.py` |
| `arabic_marker_ocr.py` normalization/parser/result preservation | `parser.py`, `ocr.py` |
| `evaluate_complete_plate_nt_ocr.py` OCR preparation and fusion | `classifier.py`, `evaluation.py` |
| `all_plate_manifest.json` and current plate files | `assets/references/manifest.json` and `images/` |

Excluded code includes detector integration, unused representation branches, marker-only processing, filters, colour/shape features, other encoders, training and application services.

## New files and roles

- `config.py`: immutable policy/configuration and config-relative path resolution.
- `preprocessing.py`: exact target-area scaling, long-edge/patch budgets, round-half-up dimensions, bicubic resize, centre padding and ImageNet tensor normalization.
- `dinov3.py`: exact checkpoint/hash validation and official local `torch.hub` loading.
- `representations.py`: original-space R2 regions and fixed 1,536-D equal-weight feature.
- `manifest.py`: reference image/dimension/hash validation.
- `reference_index.py`: normalized embedding persistence, canonical compatibility fingerprint, matching, ties, exclusions and margins.
- `ocr.py`: copied-model loading, lossless submitted PNG and native result preservation.
- `parser.py`: exact NFKC/diacritic/tatweel/whitespace and NT boundary policy.
- `classifier.py`: load-once Python API and exact positive-NT/fallback decision.
- `evaluation.py`: self-excluding leave-one-out reproduction and parity comparison.
- `cli.py`: build-index, classify, classify-folder and evaluate commands.
- `tests/test_core.py`: focused preprocessing, region, parser, index compatibility and exclusion checks.

## Structural adaptations

The experiment previously retained R2 vectors only in memory and fused OCR with saved leave-one-out predictions. The extraction serializes the exact normalized R2 vectors and metadata so unseen images can be compared with a frozen gallery. This changes packaging and execution structure, not feature or decision behavior.

The index compatibility fingerprint covers checkpoint hash, official repository revision, patch/feature dimensions, R2 view order/weights, region definitions, resolution policy and normalization. An incompatible index fails before classification.

For an unseen image, `predicted_category_margin` is the winning category's maximum reference score minus the best other category score. During labelled reproduction, `correct_category_margin` retains the original best-same minus best-different definition.

## Assets

Copied into the plugin:

- 12 reviewed reference PNGs, byte-for-byte SHA-256 matches;
- reference manifest with source paths, labels, dimensions and hashes;
- three PaddleOCR model files, byte-for-byte SHA-256 matches;
- the 83 MB DINOv3 ViT-S/16 checkpoint, byte-for-byte SHA-256 match;
- the complete pinned 33 MB official DINOv3 repository including its Git revision metadata;
- generated frozen R2 index and metadata;
- DINOv3 license notice.

No runtime model or reference asset remains external. Configuration now points to `assets/vendor/dinov3` and `assets/models/dinov3/` using config-relative paths. No weight download, signed URL, credential, token, or virtual environment was copied.

## Verification results

Unit and interface checks:

- package modules compiled successfully;
- 8 focused unit tests passed;
- import and CLI help succeeded from `/tmp`, proving independence from the project working directory;
- preprocessing dimensions match authoritative examples;
- content preservation, exact region rounding, parser digit boundaries/conflicts, self-exclusion and index incompatibility were checked.

The original full parity reproduction used the same pinned DINO bytes before they were copied. After vendoring, model loading, index compatibility, and classification were rechecked from `/tmp` using only paths within the plugin.

The `classify` command was also executed from `/tmp` against a non-index image file (`idara_2_recrop_v1/original/2.png`) as an interface smoke check. It loaded both models and the frozen index, wrote the submitted OCR crop and structured JSON, and returned `idara` without an OCR override. This is an API/CLI check, not an independent accuracy claim.

| Check | Result |
|---|---|
| Visual R2 accuracy | 11/12 |
| Final fusion accuracy | 12/12 |
| Overrides | image 5 only |
| Per-image visual category | exact match |
| Nearest reference ID | exact match |
| Nearest similarity and correct-category margin | all within `1e-6` |
| Raw OCR output | exact match for all 12 |
| Native OCR confidence | all within `1e-6` |
| NT parser decision | exact match for all 12 |
| Final category | exact match for all 12 |
| Self-reference in reproduction | excluded |

Machine-readable evidence:

- `verification/leave_one_out_final/summary.json`
- `verification/leave_one_out_final/leave_one_out_results.csv`
- `verification/leave_one_out_final/leave_one_out_results.json`
- `verification/vendored_leave_one_out/summary.json` (post-copy, plugin-local DINO assets)

## Dependencies

The verified environment used Python 3.11.15, Torch 2.12.1+cu126, PaddleOCR 3.7.0, PaddlePaddle 3.2.0, OpenCV 5.0.0.93, NumPy 2.3.5 and Pillow. No dependency installation or upgrade was performed.

## Remaining limitations

The extracted component is reusable but not production-validated. It has only 12 development references, four forced categories, no rejection policy, unverified physical independence, reviewed-crop dependence and no registration transcription guarantee. The package deliberately contains no detector, tracker, server or application integration.

## Original-project integrity

Git status was captured before extraction and was clean. At completion, `git diff --name-only` and the staged diff were empty, while `git status --short` contained only the new untracked `License plate plugin/` directory. No pre-existing tracked source, configuration, data or output file was modified, moved, renamed or deleted. The machine-readable check is `verification/original_project_integrity.json`.


