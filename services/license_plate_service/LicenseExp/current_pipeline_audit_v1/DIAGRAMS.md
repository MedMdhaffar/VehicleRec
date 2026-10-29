# Pipeline Diagrams

## Processing and decision flow

```mermaid
flowchart TD
    A[Reviewed complete plate crop\nBGR uint8] --> V0[Whole view]
    A --> VL[Left 0–50%]
    A --> VC[Centre 25–75%]
    A --> VR[Right 50–100%]
    V0 & VL & VC & VR --> B[Per-view budget resize\nfull aspect ratio retained]
    B --> P[Minimal centre padding\nto 16×16 patch grid]
    P --> N[RGB, /255, ImageNet normalization]
    N --> D[DINOv3 ViT-S/16\neval + no_grad]
    D --> C[CLS per view\nfloat32 + L2 normalize]
    C --> R2[R2: concatenate 0.5 × each\nwhole, left, centre, right\n1536-D + L2 normalize]
    R2 --> NN[Cosine 1-nearest neighbour\nknown reference category]
    A --> O1[Same budget proportional resize]
    O1 --> O2[Use unpadded resized BGR PNG]
    O2 --> O3[Paddle TextRecognition\ninternal 3×48×320 policy]
    O3 --> RAW[Preserve rec_text + rec_score]
    RAW --> PARSE[Conservative explicit NT parser]
    PARSE -->|present, no conflict| NT[Override category to NT]
    PARSE -->|absent / conflict / failure| NN
    NN --> OUT[Final known category]
    NT --> OUT
```

The visual evaluator and OCR evaluator are two separate scripts. In the recorded experiment, OCR fusion reads saved leave-one-out R2 predictions; the diagram expresses the logical method, not an implemented online service.

## Component dependencies

```mermaid
flowchart LR
    M[all_plate_manifest.json] --> PV[plate_variants.py]
    M --> EV[evaluate_plate_representations.py]
    PV --> EV
    PP[plate_preprocessing.py] --> EV
    PR[plate_representations.py] --> EV
    DA[dinov3_official_adapter.py] --> EV
    REPO[official DINOv3 repository] --> DA
    CKPT[DINOv3 checkpoint] --> DA
    EV --> VA[visual artifacts\nfinalcrop_v1]
    VA --> EO[evaluate_complete_plate_nt_ocr.py]
    M --> EO
    PP --> EO
    AM[arabic_marker_ocr.py] --> EO
    PM[PaddleOCR model cache] --> EO
    EO --> OA[OCR/fusion artifacts\nfinalcrop_v1]
    CR[crop_car_plates.py + best.pt] -. provenance only .-> M
    REC[reviewed crop records] -. provenance only .-> M
```
