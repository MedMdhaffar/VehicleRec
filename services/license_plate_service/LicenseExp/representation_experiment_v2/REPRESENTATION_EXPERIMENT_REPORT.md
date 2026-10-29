# DINOv2 Plate Representation Experiment

This experiment changes what is pooled or compared while keeping DINOv2-small, colour input, budgeted model-input preprocessing, float32 L2 normalization, and cosine comparison fixed.

R0 and R1 share each whole-plate tensor and forward output. R2 uses the whole view plus three independently processed regional views and a fixed corresponding-region score. R3 uses supplied manual marker boxes and does not demonstrate automatic localization.

## R0 reproducibility

- Equivalent to prior P0 budget: True
- Maximum absolute similarity difference: 3.5762786865234375e-07
- Nearest-neighbor predictions match: True

## A. Full ten-image/four-category evaluation

| Representation | Correct | Macro-F1 | Balanced accuracy | Same mean | Different mean |
|---|---:|---:|---:|---:|---:|
| R0 whole-plate CLS | 5/10 | 0.476 | 0.500 | 0.503194 | 0.431536 |
| R1 mean selected patches | 5/10 | 0.476 | 0.500 | 0.675293 | 0.634594 |
| R2 whole + corresponding regions | 7/10 | 0.604 | 0.667 | 0.602964 | 0.515260 |

- R0 whole-plate CLS per-category recall: idara=2/3 (0.667), nt=0/2 (0.000), shorta=2/2 (1.000), tunis=1/3 (0.333)
- R1 mean selected patches per-category recall: idara=2/3 (0.667), nt=0/2 (0.000), shorta=2/2 (1.000), tunis=1/3 (0.333)
- R2 whole + corresponding regions per-category recall: idara=2/3 (0.667), nt=0/2 (0.000), shorta=2/2 (1.000), tunis=3/3 (1.000)

## B. Matched seven-image/three-category marker subset

| Representation | Correct | Macro-F1 | Balanced accuracy | Same mean | Different mean |
|---|---:|---:|---:|---:|---:|
| R0 whole-plate CLS | 4/7 | 0.524 | 0.556 | 0.523662 | 0.453264 |
| R1 mean selected patches | 4/7 | 0.524 | 0.556 | 0.683206 | 0.647236 |
| R2 whole + corresponding regions | 5/7 | 0.583 | 0.667 | 0.618104 | 0.530237 |
| R3 manual marker CLS | 4/7 | 0.524 | 0.556 | 0.676940 | 0.509856 |

- R0 whole-plate CLS per-category recall: nt=0/2 (0.000), shorta=2/2 (1.000), tunis=2/3 (0.667)
- R1 mean selected patches per-category recall: nt=0/2 (0.000), shorta=2/2 (1.000), tunis=2/3 (0.667)
- R2 whole + corresponding regions per-category recall: nt=0/2 (0.000), shorta=2/2 (1.000), tunis=3/3 (1.000)
- R3 manual marker CLS per-category recall: nt=0/2 (0.000), shorta=2/2 (1.000), tunis=2/3 (0.667)

## Manual marker visual review

- Image 4: The supplied box visibly contains both NT letters; no coordinate clipping is visible.
- Image 5: The supplied box is in bounds but visibly contains only the rightmost Arabic marker character; another marker character lies outside the box. The R3 crop is therefore incomplete relative to image 4.
- Image 6: The supplied box covers the small left-side Shorta marking, but the 23x17 crop is very low resolution and tightly framed; visual completeness is uncertain.
- Image 7: The supplied box covers the small left-side Shorta marking, but the 27x20 crop is very low resolution and tightly framed; visual completeness is uncertain.
- Image 8: The supplied box visibly contains the complete Tunis text marker.
- Image 9: The supplied box visibly contains the complete Tunis text marker.
- Image 10: The supplied box visibly contains the complete Tunis text marker.

## Prediction changes relative to R0

- full_10, R1 mean selected patches: improved=none; worsened=none; changed=none; NT={'4': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'idara', 'new_prediction': 'idara'}, '5': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'tunis', 'new_prediction': 'tunis'}}.
- full_10, R2 whole + corresponding regions: improved=['8', '10']; worsened=none; changed=['8: idara -> tunis', '10: nt -> tunis']; NT={'4': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'idara', 'new_prediction': 'idara'}, '5': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'tunis', 'new_prediction': 'tunis'}}.
- marker_7, R1 mean selected patches: improved=none; worsened=none; changed=none; NT={'4': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'tunis', 'new_prediction': 'tunis'}, '5': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'tunis', 'new_prediction': 'tunis'}}.
- marker_7, R2 whole + corresponding regions: improved=['10']; worsened=none; changed=['10: nt -> tunis']; NT={'4': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'tunis', 'new_prediction': 'tunis'}, '5': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'tunis', 'new_prediction': 'tunis'}}.
- marker_7, R3 manual marker CLS: improved=['10']; worsened=['8']; changed=['8: tunis -> nt', '10: nt -> tunis']; NT={'4': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'tunis', 'new_prediction': 'tunis'}, '5': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'tunis', 'new_prediction': 'tunis'}}.

## Workload and limitations

- R0 and R1 share 10 whole-plate forward passes. R1 adds pooling only.
- R2 reuses each whole pass and adds 30 regional forward passes: four views per plate in total.
- R3 adds seven marker-crop forward passes. All embeddings are cached before pair/query evaluation.
- Patch overlays use green for full-content patches, orange for partially padded selected patches, red for padding-only excluded patches, and magenta for the unpadded content boundary.
- No latency was measured. Different views and patch counts imply different computation.
- Corresponding fixed regions may misalign category markings when their positions differ.
- R3 is evaluated only on seven eligible images. Its result must not be compared with ten-image R0 as evidence of improvement.
- These are ten development images; pair scores are correlated and physical independence is unverified where group identifiers are null. No generalization or calibrated confidence is claimed.
