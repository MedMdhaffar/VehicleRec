# Files Used by the Last Successful Implementation

Only active runtime/evaluation dependencies and authoritative successful artifacts are listed.

| Path | Purpose | Important verified locations | Role |
|---|---|---|---|
| `all_plate_manifest.json` | Defines the 12 current image paths, categories, dimensions and hashes | Lines 1–20 | Required input |
| `plates/idara/1.png`–`3.png` | Current complete idara crops | Paths fixed by manifest | Required data |
| `plates/nt/4.png`–`7.png` | Current complete NT crops | Paths fixed by manifest | Required data |
| `plates/shorta/8.png`–`9.png` | Current complete Shorta crops | Paths fixed by manifest | Required data |
| `plates/tunis/10.png`–`12.png` | Current complete Tunis crops | Paths fixed by manifest | Required data |
| `evaluate_plate_representations.py` | Loads DINOv3, extracts R2 features and performs leave-one-out evaluation | CLI 109–140; loading 549–630; extraction 653–709; outputs 711–784 | Visual entry point |
| `dinov3_official_adapter.py` | Geometry-free RGB processor and official local-checkpoint loader | Processor 22–43; feature wrapper 52–80; loader 83–106 | Visual runtime |
| `plate_preprocessing.py` | Budget resize, patch alignment, padding and tensor reconstruction | Policy 24–32; geometry 191–262; resize 265–294; tensors 327–379 | Shared preprocessing |
| `plate_representations.py` | Region coordinates, CLS extraction and fixed R2 score | Regions 21–39; CLS 170–183; R2 191–225 | Visual representation |
| `plate_variants.py` | Manifest validation, self/group exclusion, nearest-neighbour prediction and metrics | Validation 113–213; prediction 376–432; metrics 435–463 | Evaluation |
| `compare_plate_embeddings.py` | Shared exception, seeds, PNG writer and processor metadata | Seeds 110–115; PNG 274–276; metadata 283–296 | Imported utility |
| `evaluate_plate_dataset.py` | Supplies the summary helper imported by the visual entry point | Imported at `evaluate_plate_representations.py:34` | Imported utility |
| `third_party/third_party/dinov3/` | Pinned official Meta DINOv3 implementation | `dinov3/hub/backbones.py:199–235`; `dinov3/models/vision_transformer.py:222–267` | Required model code |
| `checkpoints/dinov3/dinov3_vits16_pretrain_lvd1689m-08c60483.pth` | DINOv3 ViT-S/16 weights | SHA-256 in configuration | Required model asset |
| `evaluate_complete_plate_nt_ocr.py` | Creates unpadded OCR inputs, runs Arabic recognition, parses evidence and applies fusion | Baseline validation 85–118; preparation 204–252; recognition/fusion 254–344 | OCR/fusion entry point |
| `arabic_marker_ocr.py` | Unicode normalization, conservative NT parser and Paddle result extraction | Normalization 64–76; parser 84–121; result extraction 186–204 | OCR/fusion runtime |
| `evaluate_arabic_marker_ocr.py` | Supplies Paddle model identity, license/documentation constants, version and CSV helpers | CLI/constants 31–63; CSV helper 66–70 | Imported utility |
| `/home/mohamed/.paddlex/official_models/arabic_PP-OCRv5_mobile_rec/inference.pdiparams` | Paddle recognition weights | SHA-256 in configuration | Required model asset |
| `/home/mohamed/.paddlex/official_models/arabic_PP-OCRv5_mobile_rec/inference.json` | Paddle static inference graph/configuration | SHA-256 in configuration | Required model asset |
| `/home/mohamed/.paddlex/official_models/arabic_PP-OCRv5_mobile_rec/inference.yml` | Recognition preprocessing and character dictionary | Lines 22–43 define preprocessing/decoder | Required model asset |
| `dinov3_s_all12_budget_finalcrop_v1/leave_one_out_predictions.csv` | Saved R2 visual predictions consumed by fusion | Full-12 R2 rows | Authoritative visual input to fusion |
| `dinov3_s_all12_budget_finalcrop_v1/representation_results.json` | Model, policy, tensor, timing and metric record | Structured JSON | Authoritative visual metadata |
| `dinov3_s_all12_budget_finalcrop_v1/all_pairs_full.csv` | 66 unordered R0/R1/R2 similarity rows | Structured CSV | Authoritative visual scores |
| `complete_plate_nt_ocr_finalcrop_v1/complete_plate_ocr_fusion.csv` | Complete per-image visual/OCR/parser/fusion record | 12 rows | Authoritative final predictions |
| `complete_plate_nt_ocr_finalcrop_v1/native_ocr_outputs.json` | Unmodified Paddle result payloads | 12 payload records | Authoritative OCR evidence |
| `complete_plate_nt_ocr_finalcrop_v1/summary_metrics.json` | NT detection and final category metrics | Structured JSON | Authoritative final metrics |
| `complete_plate_nt_ocr_finalcrop_v1/experiment_configuration.json` | Runtime versions, policy, hashes and timings | Structured JSON | Authoritative run configuration |

