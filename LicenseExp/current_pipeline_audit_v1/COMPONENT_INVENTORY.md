# Component and File Inventory

Line references were verified against the working tree on 2026-10-28. Roles describe the current final-crop experiment, not every historical use of each module.

| Project-relative path | Role | Verified implementation points | Inputs | Outputs | Status |
|---|---|---|---|---|---|
| `all_plate_manifest.json` | Authoritative 12-image data manifest | Coordinate convention and independence caveat at lines 3–5; records at 7–18 | Plate paths, labels, registration metadata, optional marker boxes | Validated in-memory records | Required for evaluation; labels are unavailable at real inference |
| `evaluate_plate_representations.py` | Visual experiment entry point | CLI 109–140; loading 549–630; extraction 653–709; evaluation/output 711–784 | Manifest, DINO repository/checkpoint, mode/policy | Pairwise CSVs, leave-one-out predictions, figures, JSON report | Required visual evaluator; not a reusable production inference API |
| `dinov3_official_adapter.py` | Official DINOv3 loader and processor adapter | RGB/normalization 22–43; explicit feature selection 52–80; local `torch.hub` load 83–106 | BGR-converted PIL image, official repository, checkpoint | Normalized tensor; CLS + spatial-token wrapper | Required for DINOv3 |
| `plate_preprocessing.py` | Rectangular resize/pad policy | Defaults 24–32; geometry 191–262; resize/pad 265–294; exact tensor creation 327–379 | Complete BGR plate/view, patch size, policy | Resized image, padded image, `[1,3,H,W]` tensor, metadata | Required |
| `plate_representations.py` | R0–R2 feature definitions | Regions 21–39; patch mask 42–112; R0/R1 115–167; CLS 170–183; R2 191–225 | Model tensors and spatial metadata | Normalized vectors and cosine matrices | Required |
| `plate_variants.py` | Manifest validation and evaluation | Validation 113–213; group exclusion/LOO 376–432; metrics 435–463 | Manifest, similarity matrix | Validated records, predictions, metrics | Required |
| `compare_plate_embeddings.py` | Shared utilities imported by evaluator | Seeds 110–115; PNG writer 274–276; processor record 283–296 | Runtime objects/images | Deterministic seeds and metadata | Required transitively; its three-image CLI is historical |
| `evaluate_plate_dataset.py` | Shared reporting helper | Imported by visual evaluator at `evaluate_plate_representations.py:34` | Evaluation values | Summary formatting | Required transitively; its own dataset CLI is development/historical |
| `evaluate_complete_plate_nt_ocr.py` | OCR/fusion experiment entry point | Schema/policy 42–73; visual-artifact validation 85–118; budget inputs 204–252; OCR/fusion 254–344 | Manifest and saved visual result directory | OCR/fusion CSV/JSON, metrics, report, figures | Required evaluator; consumes saved LOO predictions rather than running visual inference |
| `arabic_marker_ocr.py` | OCR normalization/parser/result adapter | Normalization 64–76; NT parser 84–121; Paddle result extraction 186–204 | Raw recognizer result | Raw text, native score, parser decision | Required |
| `evaluate_arabic_marker_ocr.py` | OCR constants and CSV/version helpers | Model constants near lines 31–38; CSV/version helpers 66–78 | Model name and rows | Metadata/CSV support | Required transitively; marker-only CLI is historical |
| `checkpoints/dinov3/dinov3_vits16_pretrain_lvd1689m-08c60483.pth` | DINOv3 ViT-S/16 LVD-1689M weights | SHA-256 in `FROZEN_CONFIGURATION.json` | Local official loader | Frozen backbone weights | Required asset |
| `third_party/third_party/dinov3/` | Meta official implementation | Hub architecture at `dinov3/hub/backbones.py:199–235`; token/RoPE behavior at `dinov3/models/vision_transformer.py:210–267` | Checkpoint and tensors | DINOv3 backbone | Required asset; pinned Git revision in snapshot |
| `/home/mohamed/.paddlex/official_models/arabic_PP-OCRv5_mobile_rec/` | PaddleOCR cached model assets | `inference.yml:22–43` defines BGR decode, `3×48×320` recognition resize and CTC decode | Submitted text-line/plate PNG | `rec_text`, `rec_score` | Required external cache; absolute path is environment-specific |
| `dinov3_s_all12_budget_finalcrop_v1/representation_results.json` | Authoritative structured visual record | Model, packages, processor, policy, per-image geometry, workload, metrics | Completed visual run | Audit metadata | Authoritative output |
| `dinov3_s_all12_budget_finalcrop_v1/leave_one_out_predictions.csv` | Authoritative visual predictions | 12 full-set rows per R0/R1/R2 plus marker subset rows | Similarity matrices | Per-query prediction/reference/margins | Authoritative output |
| `dinov3_s_all12_budget_finalcrop_v1/all_pairs_full.csv` | Full-set pair scores | 66 unordered pairs for 12 images | Cached features | R0/R1/R2 pair similarities | Authoritative output; embeddings themselves were not persisted |
| `complete_plate_nt_ocr_finalcrop_v1/complete_plate_ocr_fusion.csv` | Authoritative per-image OCR/fusion record | Schema mirrors `RESULT_COLUMNS` | Submitted crops, Paddle result, saved R2 prediction | 12 OCR/parser/fusion rows | Authoritative output |
| `complete_plate_nt_ocr_finalcrop_v1/native_ocr_outputs.json` | Preserved native Paddle payloads | One payload per image | Paddle result objects | Raw JSON payloads | Authoritative output |
| `complete_plate_nt_ocr_finalcrop_v1/summary_metrics.json` | Authoritative OCR/fusion metrics | NT counts, baseline/fused confusion, correction lists | Fusion CSV | Structured metrics | Authoritative output |
| `idara_2_recrop_v1/recrop_record.json` | Image 2 reviewed crop provenance | Old/new hashes and exclusive bbox at lines 2–10 | Recoverable 770×286 backup | Current 650×203 crop record | Development evidence |
| `nt_6_7_recrop_v1/manifest.json` | Images 6/7 reviewed crop provenance | Detector failures and reviewed boxes at lines 13–71 | Recoverable 600×330 originals, `best.pt`, overrides | Current 516×136 and 562×141 crops | Development evidence |
| `crop_car_plates.py` | Batch detector/crop utility | Strict detection 86–113; reviewed fallback 114–164; audit manifest 175–190 | Vehicle/source images and `best.pt` | Crops, overlays, provenance | Development only; not invoked by final evaluator |
| `best.pt` | YOLO plate-localization weights | SHA-256 in snapshot | Vehicle/source image | Detection boxes | Upstream development asset; failed on final images 2/6/7 and is not validated end-to-end |

## Historical outputs

Directories without the `finalcrop_v1` suffix document earlier crops/configurations. They remain useful for provenance but are not authoritative for the reported final 12-image result. The marker-only R3, grayscale/CLAHE, filter, shape, SigLIP and earlier OCR directories are separate experiments and are not runtime dependencies of the current R2 + NT-evidence result.
