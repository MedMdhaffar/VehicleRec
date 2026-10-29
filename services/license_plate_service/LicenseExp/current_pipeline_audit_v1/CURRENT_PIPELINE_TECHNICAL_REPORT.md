# Current Tunisian Plate-Category Pipeline Audit

Audit date: 2026-10-28  
Scope: documentation and artifact verification only; no model inference or pipeline behavior was changed.

## Plain-language overview

The current experiment assigns one of four known Tunisian plate categories—`idara`, `nt`, `shorta`, or `tunis`—to an already extracted, reviewed plate crop. It first compares DINOv3 visual features from the whole plate and three overlapping horizontal regions against the other labelled development images. A PaddleOCR recognizer then reads the complete resized plate. If a conservative parser finds an explicit `ن ت`/`نت` marking without conflicting category text, the category is forced to `nt`; otherwise the visual prediction is kept.

The saved artifacts verify the reported scores: R0 is 8/12, R1 is 8/12, R2 is 11/12, and R2 plus the NT rule is 12/12. OCR NT detection is TP 4, FP 0, FN 0, TN 8. Image 5 is the only visual error and the only fusion correction.

This is not a deployed recognition system. It is a two-stage evaluation assembled from two scripts, and the OCR stage reads saved leave-one-out visual predictions. The 12 images are repeatedly used development data, each query is matched against the other 11 labelled images, crop corrections were reviewed manually, no unknown/rejection outcome exists, and no frozen-gallery interface for a genuinely new plate is implemented. The result establishes category decisions on these crops only; it does not establish correct registration transcription or end-to-end vehicle-to-category performance.

## Audit status and material discrepancies

The headline result claims all match the authoritative CSV/JSON files. Three implementation/documentation discrepancies matter:

1. The official ViT-S architecture has four storage/register tokens (`third_party/third_party/dinov3/dinov3/hub/backbones.py:199–235`). The adapter excludes them correctly by returning only explicit CLS and spatial patch tensors (`dinov3_official_adapter.py:65–80`), but its diagnostic checks `x_norm_regtokens`; the local official implementation names the field `x_storage_tokens` (`third_party/third_party/dinov3/dinov3/models/vision_transformer.py:252–259`). Consequently, saved per-forward metadata says zero nonspatial tokens although the architecture contains four. This does not put registers into R1.
2. The saved record calls rectangular support “positional interpolation.” This DINOv3 implementation instead constructs two-dimensional rotary positional embeddings for each observed `(H,W)` patch grid (`vision_transformer.py:222–234,263–279`). Rectangular inference succeeded, but the mechanism is RoPE, not interpolation of a learned square positional table.
3. “Pipeline” is logical shorthand. `evaluate_complete_plate_nt_ocr.py` loads prior R2 predictions from disk and validates hashes (`85–118`); it does not invoke DINOv3. There is no single reusable online component that accepts a new crop and runs both branches.

The visual entry point’s module docstring still says “DINOv2” (`evaluate_plate_representations.py:2`) even though its CLI and loader support official DINOv3 (`109–140,587–602`).

## 1. Actual system boundary

### Accepted input

The authoritative evaluator accepts `all_plate_manifest.json`, whose 12 records point to decoded BGR PNG plate crops and supply category labels, registration metadata, expected dimensions, hashes, optional marker boxes, and null physical/group IDs (`all_plate_manifest.json:1–20`). Manifest validation enforces numerical order, exact paths, parent-directory category, dimensions, marker bounds, hashes, and duplicate detection (`plate_variants.py:113–213`). Registration strings are metadata; neither visual matching nor NT fusion uses them.

The operational visual input is therefore a complete plate crop, not a vehicle image. The final OCR branch also accepts that crop, not a marker crop and not a detected text polygon. All final OCR calls bypass text detection, orientation classification, and unwarping.

### Automatic versus reviewed steps

Given a manifest crop, resize/pad, DINO feature extraction, similarity evaluation, OCR, parsing, and fusion are automatic and fixed. Upstream crop production is not uniformly automatic:

- `best.pt` and `crop_car_plates.py` can select the highest-confidence detection and add proportional crop padding (`crop_car_plates.py:86–113`; `compare_plate_embeddings.py:159–205`).
- Image 2 used a reviewed exclusive crop `[120,80,770,283]` after `best.pt` returned no detection down to 0.001. The recoverable 770×286 original and hash are recorded in `idara_2_recrop_v1/recrop_record.json:2–10`; the current crop is 650×203.
- Images 6 and 7 also failed strict detection. Reviewed fallback boxes plus a fixed 2% crop margin produced 516×136 and 562×141 crops. Originals, errors, boxes, notes, and previews are retained in `nt_6_7_recrop_v1/manifest.json:13–71` and `nt_6_7_recrop_v1/originals/`.

Thus the final result is conditional on reviewed, sufficiently complete plate crops. The detector is provenance tooling, not a validated end-to-end stage.

### Outputs and missing objectives

The visual evaluator writes pairwise scores, leave-one-out predictions, confusion matrices, similarity figures, exact-tensor display reconstructions, per-image geometry, timing, and a JSON record (`evaluate_plate_representations.py:703–784`). It keeps embeddings in memory for the run but does not persist a reusable embedding gallery.

The OCR evaluator writes submitted crops, raw native payloads, parser/fusion rows, metrics, confusion figures, and configuration (`evaluate_complete_plate_nt_ocr.py:224–344`). It outputs a known category on every row because the visual branch always selects a neighbour. There is no unknown/rejection threshold, calibrated probability, automatic category-marker localization in this final configuration, registration-digit correctness evaluation, production API, vehicle application integration, or independent validation set.

## 2. Components and authoritative evidence

The detailed inventory is in [COMPONENT_INVENTORY.md](COMPONENT_INVENTORY.md). The authoritative result sources are:

- `dinov3_s_all12_budget_finalcrop_v1/REPRESENTATION_EXPERIMENT_REPORT.md`
- `dinov3_s_all12_budget_finalcrop_v1/leave_one_out_predictions.csv`
- `dinov3_s_all12_budget_finalcrop_v1/representation_results.json`
- `complete_plate_nt_ocr_finalcrop_v1/COMPLETE_PLATE_NT_OCR_REPORT.md`
- `complete_plate_nt_ocr_finalcrop_v1/complete_plate_ocr_fusion.csv`
- `complete_plate_nt_ocr_finalcrop_v1/native_ocr_outputs.json`
- `complete_plate_nt_ocr_finalcrop_v1/summary_metrics.json`

Directories from earlier crop revisions or without the `finalcrop_v1` suffix are historical evidence, not the source of the final headline figures. The visual output contains similarity matrices and metadata but no `.pt`/`.npy` embedding cache.

## 3. Crop provenance and image preprocessing

### Visual sequence

For each whole or regional view:

1. OpenCV decodes the PNG as BGR `uint8` (`plate_variants.py:161–188`).
2. For budget mode, requested scale is

   `s_requested = sqrt(50176 / (height × width))`.

   It is capped by `1008 / max(height,width)`, then reduced by binary search if the padded patch count would exceed 512 (`plate_preprocessing.py:164–219`). Each dimension uses `floor(value + 0.5)`, at least one pixel (`54–58,147–151`). Upscaling and downscaling are allowed.
3. OpenCV bicubic (`INTER_CUBIC`) resizes once while preserving aspect ratio within integer rounding (`265–279`). No content is cropped.
4. Each dimension is rounded upward to a multiple of the model’s 16-pixel patch size. Minimal padding is centred; an odd extra pixel goes right or bottom (`226–257`). Padding is RGB `(124,116,104)`, supplied as BGR `(104,116,124)` to OpenCV (`280–290`).
5. The adapter converts BGR to PIL RGB (`plate_preprocessing.py:327–340`), then `MetaDinoV3Processor` converts to float32, rescales by `1/255`, and normalizes by ImageNet RGB mean `(0.485,0.456,0.406)` and standard deviation `(0.229,0.224,0.225)` (`dinov3_official_adapter.py:18–43`). Automatic resize and centre crop are disabled.
6. The verified tensor is `[1,3,H,W]`, float32, patch-aligned, and sent individually to the saved run’s CPU device (`plate_preprocessing.py:343–379`). Final whole-view tensors vary from `[1,3,112,448]` to `[1,3,128,448]` and `[1,3,112,512]`; exact values are in [VERIFIED_PER_IMAGE_RESULTS.csv](VERIFIED_PER_IMAGE_RESULTS.csv).

The reconstruction images reverse normalization/rescaling from the exact tensor, then clip to `[0,255]` and round to `uint8` for display (`plate_preprocessing.py:297–324`). They are display reconstructions, not the normalized tensor itself.

### OCR sequence and branch difference

The OCR script reads the visual run’s exact budget policy and patch size, calls the same `calculate_geometry`, and generates the same bicubic resized content (`evaluate_complete_plate_nt_ocr.py:213–234`). It deliberately submits the unpadded BGR resized PNG; patch-alignment padding is recorded but not submitted. PaddleOCR then applies its own recognition preprocessing. Therefore OCR and DINOv3 share the same externally resized whole-plate content, but not the same pixels at model input: DINO receives RGB constant padding plus ImageNet normalization; OCR receives an unpadded BGR PNG followed by its recognizer-specific resize/pad.

## 4. DINOv3 and representations

### Model identity and runtime

The encoder is Meta DINOv3 ViT-S/16 pretrained/distilled on web-image LVD-1689M. It is loaded from the official local repository with:

```python
torch.hub.load(local_repo, "dinov3_vits16", source="local", weights=local_checkpoint)
```

This matches Meta’s [official local-checkpoint interface](https://github.com/facebookresearch/dinov3#pretrained-backbones-via-pytorch-hub). The official [model card](https://github.com/facebookresearch/dinov3/blob/main/MODEL_CARD.md) describes ViT-S/16 as patch size 16, dimension 384, four register tokens, six heads, and RoPE. Locally, `backbones.py:199–235` fixes patch 16, dimension 384, depth 12, six heads, four storage tokens and fp32 RoPE coordinates.

The pinned source and weights are governed by the DINOv3 License Agreement in `third_party/third_party/dinov3/LICENSE.md`; no signed download URL is stored in this audit.

Checkpoint: `checkpoints/dinov3/dinov3_vits16_pretrain_lvd1689m-08c60483.pth`  
SHA-256: `08c60483bc63c04f533611e34bf70b120eedb7240f469bc16e9e20bf344b941d`  
Official repository revision: `6876159a11b4df116f30f667f8c9888617df0751`

The saved final run used CPU, `model.eval()`, `torch.no_grad()`, float32 token pooling and L2-normalized representations (`evaluate_plate_representations.py:603–630,645–698`). It was not the GPU run. Runtime packages are captured in `FROZEN_CONFIGURATION.json`.

### R2 exactly

Regions are cut from the original complete crop before any model resize:

- whole: complete image;
- left: `x=[0,floor/ceil(0.5W))` effectively lower floor, upper ceil;
- centre: `x=[floor(0.25W),ceil(0.75W))`;
- right: `x=[floor(0.5W),W)`;
- every region uses full height.

The exact floor-lower/ceil-upper/clamp implementation is `plate_representations.py:21–39`. Each of the four views is independently budget-resized, minimally padded, normalized and forwarded (`evaluate_plate_representations.py:653–677`). From each final output, the adapter takes the explicit normalized CLS token; the evaluator casts it to float32 and L2-normalizes it (`plate_representations.py:170–183`).

For image `x`, let `u_w(x),u_l(x),u_c(x),u_r(x)` be those unit 384-D CLS vectors. The stored equivalent feature is:

`z(x) = normalize(concat(0.5u_w, 0.5u_l, 0.5u_c, 0.5u_r))`, a 1536-D vector.

Since the four parts are unit vectors, its norm before the final normalization is already one apart from floating-point error. Pair scoring is verified against the direct expression:

`score(A,B) = 0.25 cos(u_wA,u_wB) + 0.25 cos(u_lA,u_lB) + 0.25 cos(u_cA,u_cB) + 0.25 cos(u_rA,u_rB)`.

The code fixes order and weights, checks concatenation/direct equivalence within `1e-6`, and computes cosine by normalized dot product (`plate_representations.py:186–225`). R2 costs four forwards per plate.

### R0 and R1

R0 is the L2-normalized float32 whole-view CLS token. R1 reuses the exact same whole-view forward, selects spatial patch tokens whose 16×16 footprint intersects the resized unpadded content, excludes padding-only patches, includes partial-content patches with uniform weight, averages, then L2-normalizes (`plate_representations.py:42–167`). Registers/storage tokens cannot enter R1 because `OfficialDinoV3Wrapper` returns only CLS plus `x_norm_patchtokens`.

## 5. Reference matching and evaluation

The current reference collection is the same 12-image manifest: three idara, four NT, two Shorta, and three Tunis images. All group IDs are null, so physical independence is unverified. Exact duplicates were not detected.

For each representation, all embeddings are generated once and cached in memory. Cosine matrices are constructed, then each query excludes itself and any reference sharing a non-null physical/group ID (`plate_variants.py:376–395`). The reference with maximum similarity supplies its category. Similarity ties within `1e-12` choose the smallest numeric ID while setting `tie_flag`; category-level ties are also flagged (`395–428`). No final R2 row was tied.

`correct_category_margin = best same-category similarity − best different-category similarity`. `predicted_category_margin = winning category's best-reference score − the best other category score` (`plate_variants.py:405–426`). These are relative cosine gaps, not calibrated confidence or probability.

Changing any reference crop changes its whole and regional features. It can therefore change both that image’s own prediction and another query’s nearest reference, competing-category maximum, and margin. The final image 2/6/7 crop revisions changed the reference gallery as well as their queries.

This evaluation must be distinguished from new-image inference. Leave-one-out has labels and rebuilds a gallery from 11 development images per query. A real interface would need a versioned frozen gallery containing feature vectors, IDs/categories/hashes and preprocessing/model metadata; a query extractor; reference search; an explicit unsupported/unknown policy; and monitoring. None is implemented here.

## 6. Arabic OCR and conservative NT evidence

The recognizer is `arabic_PP-OCRv5_mobile_rec`, which PaddleOCR [documents as an Arabic letters-and-numbers recognizer](https://www.paddleocr.ai/main/en/version3.x/module_usage/text_recognition.html). The code directly instantiates `paddleocr.TextRecognition(model_name=..., device="cpu")` and calls `predict(..., batch_size=1)` (`evaluate_complete_plate_nt_ocr.py:254–275`). No detector, orientation classifier, or unwarper is involved.

The completed experiment records the recognizer license as Apache-2.0 (`evaluate_arabic_marker_ocr.py:225–237`).

Cached assets are under `/home/mohamed/.paddlex/official_models/arabic_PP-OCRv5_mobile_rec/`; their hashes are frozen separately. The local `inference.yml:22–43` specifies BGR decoding, `RecResizeImg` shape `[3,48,320]`, `valid_ratio`, and `CTCLabelDecode`. The installed PaddleX `OCRReisizeNormImg` implementation resizes proportionally to height 48 using `ceil(48 × width/height)`, right-pads to the batch width, converts CHW float32, rescales by `1/255`, then applies `(x−0.5)/0.5` (`.../site-packages/paddlex/inference/models/text_recognition/processors.py:50–97`). With batch size one and all submitted aspect ratios below `320/48`, the configured inference arrays are `[3,48,320]`; these tensor shapes were established from configuration and implementation, not recorded by the public result object. The public result is `{"res": {"input_path", "page_index", "rec_text", "rec_score"}}`; PaddleOCR defines `rec_score` as predicted-text confidence in its [Text Recognition API documentation](https://www.paddleocr.ai/main/en/version3.x/module_usage/text_recognition.html). This experiment preserves it but does not calibrate or threshold it.

`normalize_arabic_marker` applies Unicode NFKC, removes U+0640 tatweel, all whitespace, and Unicode `Mn` marks; it does not substitute base letters or digits (`arabic_marker_ocr.py:64–76`). Arabic-Indic digits are preserved, not converted.

For boundary matching, the parser separately applies NFKC and removes tatweel/`Mn` while retaining whitespace. It searches for `ن\s*ت`, requiring the immediately preceding/following characters not to be Arabic base letters. String ends, punctuation, separators, Latin digits, and Arabic-Indic digits are allowed. Hence adjacent registration digits are permitted, while `بنت` and `نتيجة` are rejected. Explicit boundary-valid `شرطة` or `تونس` makes the evidence `conflicting`; otherwise an NT match is `present`, and all other completed outputs are `absent` (`arabic_marker_ocr.py:84–121`). Failed inference is stored as unavailable.

Image 5 demonstrates the category/transcription distinction. Raw output `نت١0379` has boundary-valid contiguous NT letters followed by digits, so the parser supplies NT evidence. The expected registration metadata is `103679`; the mixed-script OCR digits are not an exact transcription. The category correction is valid under the fixed rule, but it is not evidence of registration recognition.

## 7. Final decision rule and saved schema

Exact behavior (`evaluate_complete_plate_nt_ocr.py:265–316`) is:

```text
visual = saved full_12 / r2_global_regional leave-one-out prediction
try:
    raw_text, rec_score = paddle_recognize(unpadded_budget_crop)
    evidence = parse_nt_evidence(raw_text)
except inference_error:
    evidence = unavailable

if inference_completed and evidence.decision == "present":
    final_category = "nt"
else:
    final_category = visual.predicted_category
```

Thus conflict and failure both retain the visual result. Absence is not treated as proof of non-NT. There is no `unknown`; the visual branch always chooses one manifest category. The CSV preserves source/submitted hashes and dimensions, resize scales, unsubmitted padding, raw/normalized/boundary text, native score, parser reason/conflicts, inference status/time, visual neighbour/score, override, final category and correctness (`evaluate_complete_plate_nt_ocr.py:42–52,295–316`). A real row is image 5: expected `nt`; R2 `tunis`; nearest reference 12 at `0.6737486720`; raw `نت١0379`; confidence `0.7348999381`; evidence `present`; override true; final `nt`; correction.

## 8. Independently verified results

The audit reread the authoritative CSV/JSON files and recomputed counts from their rows. It did not rerun model inference.

| Method | Correct | Accuracy | Balanced accuracy | Macro-F1 | Idara recall | NT recall | Shorta recall | Tunis recall |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| R0 whole CLS | 8/12 | 0.667 | 0.729 | 0.687 | 3/3 | 1/4 | 2/2 | 2/3 |
| R1 mean patches | 8/12 | 0.667 | 0.667 | 0.664 | 3/3 | 2/4 | 1/2 | 2/3 |
| R2 global + regions | 11/12 | 0.917 | 0.938 | 0.929 | 3/3 | 3/4 | 2/2 | 3/3 |
| R2 + NT evidence | 12/12 | 1.000 | 1.000 | not saved by OCR summary | 3/3 | 4/4 | 2/2 | 3/3 |

R2 confusion matrix, rows expected and columns `[idara, nt, shorta, tunis]`:

```text
idara  [3,0,0,0]
nt     [0,3,0,1]
shorta [0,0,2,0]
tunis  [0,0,0,3]
```

For completeness, R0 is `[[3,0,0,0],[2,1,0,1],[0,0,2,0],[0,1,0,2]]`; R1 is `[[3,0,0,0],[1,2,0,1],[1,0,1,0],[0,1,0,2]]`, with the same row/column ordering.

Fused confusion is diagonal `[3,4,2,3]`. NT OCR counts are TP `4/4`, FP `0/8`, FN `0/4`, TN `8/8`, precision `4/4`, recall `4/4`; all 12 inferences completed. There were no conflicts, false NT overrides, regressions, or failed OCR rows. The full per-image evidence—including exact tensor sizes, nearest references, similarities, correct-category margins, raw OCR and final decisions—is [VERIFIED_PER_IMAGE_RESULTS.csv](VERIFIED_PER_IMAGE_RESULTS.csv).

The narrowest correct R2 margin is image 4 (`0.0038507`). Image 5 has margin `−0.0338511`, nearest image 12, and changes from `tunis` to `nt`. All other final categories equal R2.

### Timing and memory boundaries

The saved visual run used CPU. Model load was `2.239 s`. After two fixed warm-up forwards, 12 shared whole R0/R1 forwards totalled `1.043 s`; 36 R2 regional forwards totalled `3.002 s`; therefore recorded R2 feature time was `4.045 s` for all 12 images. These are wall-clock model-forward measurements only; image I/O, preprocessing, figures and matching are excluded. Peak GPU memory is unavailable because no GPU was used.

OCR CPU model load was `2.315 s`, one warm-up was `0.227 s`, and 12 recognition calls totalled `1.429 s` (mean `0.1191 s`, median `0.09585 s`). External crop preparation, loading and warm-up are excluded from per-image recognition values. These measurements are environment-specific and are not end-to-end service latency.

## 9. Reproduction, verification and freeze checklist

Run from the project root with fresh output directories because both entry points refuse overwrite:

```bash
MPLCONFIGDIR=/tmp/matplotlib-dinov3 \
/home/mohamed/miniconda3/envs/ml/bin/python evaluate_plate_representations.py \
  --manifest all_plate_manifest.json \
  --encoder dinov3_vits16 \
  --dinov3-repo third_party/third_party/dinov3 \
  --dinov3-checkpoint checkpoints/dinov3/dinov3_vits16_pretrain_lvd1689m-08c60483.pth \
  --input-mode budget --device cpu \
  --output-dir dinov3_s_all12_budget_reproduction

MPLCONFIGDIR=/tmp/matplotlib-ocr \
/home/mohamed/miniconda3/envs/ml/bin/python evaluate_complete_plate_nt_ocr.py \
  --manifest all_plate_manifest.json \
  --visual-baseline-dir dinov3_s_all12_budget_reproduction \
  --output-dir complete_plate_nt_ocr_reproduction \
  --device cpu
```

Tests were run during this audit with:

```bash
MPLCONFIGDIR=/tmp/matplotlib-tests \
/home/mohamed/miniconda3/envs/ml/bin/python -m unittest discover -s tests -v
```

All 51 tests passed. Relevant checks cover resize/padding/divisibility/reconstruction, DINOv3 processor geometry and register exclusion, region rounding, patch masking, R2 score equivalence, self/group exclusion, metrics, OCR crop bounds, conservative NT parsing, and raw-output preservation. The test fixture confirms register exclusion but does not detect the real-model output-key metadata mismatch described above.

Before any validation run, freeze and verify: manifest and every image hash; crop provenance; checkpoint hash; official repository revision; processor mean/std; area/edge/patch budgets; padding/interpolation/rounding; R2 regions/order/weights; reference gallery membership; tie/rejection policy; Paddle model asset hashes and internal YAML; parser regex/conflict/failure policy; package versions; device/precision; and output directory immutability. The machine-readable snapshot is [FROZEN_CONFIGURATION.json](FROZEN_CONFIGURATION.json).

## Limitations and conclusion

- Twelve repeatedly examined development images cannot support a generalization or deployment claim; pairwise observations are correlated.
- Physical independence is unverified because all group identifiers are null.
- Crop quality is assisted: final images 2, 6, and 7 depend on reviewed boxes after detector failure. Upstream detection performance is not part of the 12/12 score.
- The gallery is tiny and category coverage is limited to four labels. Unsupported plates are forced to the nearest known category because no rejection exists.
- Leave-one-out is not the same as inference against a frozen production reference set, and that interface is absent.
- `rec_score` is a native line-recognition score, not calibrated NT probability. The parser uses exact evidence only, which is conservative but may miss other renderings.
- NT evidence and category accuracy do not establish exact digit transcription; image 5 visibly demonstrates this gap.
- CPU timings exclude several end-to-end costs; GPU latency and memory are not measured for the final crop run.
- No production integration or independent prospective validation has been completed.

The evidence supports the narrow statement that the fixed R2 + explicit NT override correctly classifies these 12 reviewed development crops. It does not yet support autonomous vehicle-image classification, unknown-category handling, or full license-plate recognition.

## Evidence map

- [Component inventory](COMPONENT_INVENTORY.md)
- [Frozen configuration and hashes](FROZEN_CONFIGURATION.json)
- [Verified per-image results](VERIFIED_PER_IMAGE_RESULTS.csv)
- [Mermaid processing/dependency diagrams](DIAGRAMS.md)
- [Authoritative visual report](../dinov3_s_all12_budget_finalcrop_v1/REPRESENTATION_EXPERIMENT_REPORT.md)
- [Authoritative OCR/fusion report](../complete_plate_nt_ocr_finalcrop_v1/COMPLETE_PLATE_NT_OCR_REPORT.md)
