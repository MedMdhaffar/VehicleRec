# DINOv2 Plate Representation Experiment

This experiment changes what is pooled or compared while keeping DINOv2-small, colour input, budgeted model-input preprocessing, float32 L2 normalization, and cosine comparison fixed.

R0 and R1 share each whole-plate tensor and forward output. R2 uses the whole view plus three independently processed regional views and a fixed corresponding-region score. R3 uses supplied manual marker boxes and does not demonstrate automatic localization.

## R0 reproducibility

- Equivalent to prior P0 budget: False
- Maximum absolute similarity difference: unavailable
- Nearest-neighbor predictions match: unavailable

## A. Full ten-image/four-category evaluation

| Representation | Correct | Macro-F1 | Balanced accuracy | Same mean | Different mean |
|---|---:|---:|---:|---:|---:|
| R0 whole-plate CLS | 3/10 | 0.292 | 0.292 | 0.471468 | 0.385363 |
| R1 mean selected patches | 4/10 | 0.393 | 0.417 | 0.637784 | 0.567020 |
| R2 whole + corresponding regions | 4/10 | 0.410 | 0.375 | 0.521651 | 0.406645 |

- R0 whole-plate CLS per-category recall: idara=2/3 (0.667), nt=0/2 (0.000), shorta=1/2 (0.500), tunis=0/3 (0.000)
- R1 mean selected patches per-category recall: idara=2/3 (0.667), nt=0/2 (0.000), shorta=2/2 (1.000), tunis=0/3 (0.000)
- R2 whole + corresponding regions per-category recall: idara=2/3 (0.667), nt=0/2 (0.000), shorta=1/2 (0.500), tunis=1/3 (0.333)

## B. Matched seven-image/three-category marker subset

| Representation | Correct | Macro-F1 | Balanced accuracy | Same mean | Different mean |
|---|---:|---:|---:|---:|---:|
| R0 whole-plate CLS | 2/7 | 0.317 | 0.278 | 0.422842 | 0.383659 |
| R1 mean selected patches | 2/7 | 0.267 | 0.333 | 0.580916 | 0.568654 |
| R2 whole + corresponding regions | 2/7 | 0.333 | 0.278 | 0.455710 | 0.413036 |
| R3 manual marker CLS | 2/7 | 0.267 | 0.333 | 0.544253 | 0.528749 |

- R0 whole-plate CLS per-category recall: nt=0/2 (0.000), shorta=1/2 (0.500), tunis=1/3 (0.333)
- R1 mean selected patches per-category recall: nt=0/2 (0.000), shorta=2/2 (1.000), tunis=0/3 (0.000)
- R2 whole + corresponding regions per-category recall: nt=0/2 (0.000), shorta=1/2 (0.500), tunis=1/3 (0.333)
- R3 manual marker CLS per-category recall: nt=0/2 (0.000), shorta=2/2 (1.000), tunis=0/3 (0.000)

## Manual marker visual review

- Image 4: The supplied box visibly contains both NT letters; no coordinate clipping is visible.
- Image 5: The corrected supplied box is in bounds and visibly contains both NT marker characters; no coordinate clipping is visible.
- Image 6: The supplied box covers the small left-side Shorta marking, but the 23x17 crop is very low resolution and tightly framed; visual completeness is uncertain.
- Image 7: The supplied box covers the small left-side Shorta marking, but the 27x20 crop is very low resolution and tightly framed; visual completeness is uncertain.
- Image 8: The supplied box visibly contains the complete Tunis text marker.
- Image 9: The supplied box visibly contains the complete Tunis text marker.
- Image 10: The supplied box visibly contains the complete Tunis text marker.

## Prediction changes relative to R0

- full_10, R1 mean selected patches: improved=['6']; worsened=none; changed=['2: tunis -> nt', '6: tunis -> shorta', '8: idara -> nt']; NT={'4': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'idara', 'new_prediction': 'idara'}, '5': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'tunis', 'new_prediction': 'tunis'}}.
- full_10, R2 whole + corresponding regions: improved=['9']; worsened=none; changed=['2: tunis -> nt', '6: tunis -> nt', '9: idara -> tunis']; NT={'4': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'idara', 'new_prediction': 'idara'}, '5': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'tunis', 'new_prediction': 'tunis'}}.
- marker_7, R1 mean selected patches: improved=['6']; worsened=['9']; changed=['6: tunis -> shorta', '9: tunis -> shorta']; NT={'4': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'tunis', 'new_prediction': 'tunis'}, '5': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'tunis', 'new_prediction': 'tunis'}}.
- marker_7, R2 whole + corresponding regions: improved=none; worsened=none; changed=['6: tunis -> nt']; NT={'4': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'tunis', 'new_prediction': 'tunis'}, '5': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'tunis', 'new_prediction': 'tunis'}}.
- marker_7, R3 manual marker CLS: improved=['6']; worsened=['9']; changed=['5: tunis -> shorta', '6: tunis -> shorta', '9: tunis -> nt']; NT={'4': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'tunis', 'new_prediction': 'tunis'}, '5': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'tunis', 'new_prediction': 'shorta'}}.

## Workload and limitations

- R0 and R1 share 10 whole-plate forward passes. R1 adds pooling only.
- R2 reuses each whole pass and adds 30 regional forward passes: four views per plate in total.
- R3 adds seven marker-crop forward passes. All embeddings are cached before pair/query evaluation.
- Patch overlays use green for full-content patches, orange for partially padded selected patches, red for padding-only excluded patches, and magenta for the unpadded content boundary.
- No latency was measured. Different views and patch counts imply different computation.
- Corresponding fixed regions may misalign category markings when their positions differ.
- R3 is evaluated only on seven eligible images. Its result must not be compared with ten-image R0 as evidence of improvement.
- These are ten development images; pair scores are correlated and physical independence is unverified where group identifiers are null. No generalization or calibrated confidence is claimed.
