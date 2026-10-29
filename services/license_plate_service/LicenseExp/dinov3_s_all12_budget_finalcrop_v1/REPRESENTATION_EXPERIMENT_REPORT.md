# Frozen-Encoder Plate Representation Experiment

Encoder: `dinov3_vits16`. Rectangular preprocessing: `budget`. Colour input, float32 L2 normalization, and cosine comparison remain fixed.

R0 and R1 share each whole-plate tensor and forward output. R2 uses the whole view plus three independently processed regional views and a fixed corresponding-region score. R3 uses supplied manual marker boxes and does not demonstrate automatic localization.

## R0 reproducibility

- Equivalent to prior P0 budget: False
- Maximum absolute similarity difference: unavailable
- Nearest-neighbor predictions match: unavailable

## A. Full 12-image evaluation

| Representation | Correct | Macro-F1 | Balanced accuracy | Same mean | Different mean |
|---|---:|---:|---:|---:|---:|
| R0 whole-plate CLS | 8/12 | 0.687 | 0.729 | 0.640788 | 0.521378 |
| R1 mean selected patches | 8/12 | 0.664 | 0.667 | 0.836650 | 0.778839 |
| R2 whole + corresponding regions | 11/12 | 0.929 | 0.938 | 0.681744 | 0.531661 |

- R0 whole-plate CLS per-category recall: idara=3/3 (1.000), nt=1/4 (0.250), shorta=2/2 (1.000), tunis=2/3 (0.667)
- R1 mean selected patches per-category recall: idara=3/3 (1.000), nt=2/4 (0.500), shorta=1/2 (0.500), tunis=2/3 (0.667)
- R2 whole + corresponding regions per-category recall: idara=3/3 (1.000), nt=3/4 (0.750), shorta=2/2 (1.000), tunis=3/3 (1.000)

## B. Matched 9-image marker subset

| Representation | Correct | Macro-F1 | Balanced accuracy | Same mean | Different mean |
|---|---:|---:|---:|---:|---:|
| R0 whole-plate CLS | 7/9 | 0.806 | 0.806 | 0.609669 | 0.507164 |
| R1 mean selected patches | 7/9 | 0.806 | 0.806 | 0.817396 | 0.763540 |
| R2 whole + corresponding regions | 8/9 | 0.905 | 0.917 | 0.643251 | 0.511653 |
| R3 manual marker CLS | 8/9 | 0.905 | 0.917 | 0.682713 | 0.472893 |

- R0 whole-plate CLS per-category recall: nt=3/4 (0.750), shorta=2/2 (1.000), tunis=2/3 (0.667)
- R1 mean selected patches per-category recall: nt=3/4 (0.750), shorta=2/2 (1.000), tunis=2/3 (0.667)
- R2 whole + corresponding regions per-category recall: nt=3/4 (0.750), shorta=2/2 (1.000), tunis=3/3 (1.000)
- R3 manual marker CLS per-category recall: nt=3/4 (0.750), shorta=2/2 (1.000), tunis=3/3 (1.000)

## Manual marker visual review

- Image 4: Supplied in-bounds marker crop; no coordinates were adjusted. Review the saved crop gallery before interpretation.
- Image 5: Supplied in-bounds marker crop; no coordinates were adjusted. Review the saved crop gallery before interpretation.
- Image 6: Supplied in-bounds marker crop; no coordinates were adjusted. Review the saved crop gallery before interpretation.
- Image 7: Supplied in-bounds marker crop; no coordinates were adjusted. Review the saved crop gallery before interpretation.
- Image 8: Supplied in-bounds marker crop; no coordinates were adjusted. Review the saved crop gallery before interpretation.
- Image 9: Supplied in-bounds marker crop; no coordinates were adjusted. Review the saved crop gallery before interpretation.
- Image 10: Supplied in-bounds marker crop; no coordinates were adjusted. Review the saved crop gallery before interpretation.
- Image 11: Supplied in-bounds marker crop; no coordinates were adjusted. Review the saved crop gallery before interpretation.
- Image 12: Supplied in-bounds marker crop; no coordinates were adjusted. Review the saved crop gallery before interpretation.

## Prediction changes relative to R0

- full_12, R1 mean selected patches: improved=['6']; worsened=['8']; changed=['6: idara -> nt', '8: shorta -> idara']; NT={'4': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'idara', 'new_prediction': 'idara'}, '5': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'tunis', 'new_prediction': 'tunis'}, '6': {'r0_correct': False, 'new_correct': True, 'r0_prediction': 'idara', 'new_prediction': 'nt'}, '7': {'r0_correct': True, 'new_correct': True, 'r0_prediction': 'nt', 'new_prediction': 'nt'}}.
- full_12, R2 whole + corresponding regions: improved=['4', '6', '12']; worsened=none; changed=['4: idara -> nt', '6: idara -> nt', '12: nt -> tunis']; NT={'4': {'r0_correct': False, 'new_correct': True, 'r0_prediction': 'idara', 'new_prediction': 'nt'}, '5': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'tunis', 'new_prediction': 'tunis'}, '6': {'r0_correct': False, 'new_correct': True, 'r0_prediction': 'idara', 'new_prediction': 'nt'}, '7': {'r0_correct': True, 'new_correct': True, 'r0_prediction': 'nt', 'new_prediction': 'nt'}}.
- marker_9, R1 mean selected patches: improved=none; worsened=none; changed=none; NT={'4': {'r0_correct': True, 'new_correct': True, 'r0_prediction': 'nt', 'new_prediction': 'nt'}, '5': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'tunis', 'new_prediction': 'tunis'}, '6': {'r0_correct': True, 'new_correct': True, 'r0_prediction': 'nt', 'new_prediction': 'nt'}, '7': {'r0_correct': True, 'new_correct': True, 'r0_prediction': 'nt', 'new_prediction': 'nt'}}.
- marker_9, R2 whole + corresponding regions: improved=['12']; worsened=none; changed=['12: nt -> tunis']; NT={'4': {'r0_correct': True, 'new_correct': True, 'r0_prediction': 'nt', 'new_prediction': 'nt'}, '5': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'tunis', 'new_prediction': 'tunis'}, '6': {'r0_correct': True, 'new_correct': True, 'r0_prediction': 'nt', 'new_prediction': 'nt'}, '7': {'r0_correct': True, 'new_correct': True, 'r0_prediction': 'nt', 'new_prediction': 'nt'}}.
- marker_9, R3 manual marker CLS: improved=['12']; worsened=none; changed=['12: nt -> tunis']; NT={'4': {'r0_correct': True, 'new_correct': True, 'r0_prediction': 'nt', 'new_prediction': 'nt'}, '5': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'tunis', 'new_prediction': 'tunis'}, '6': {'r0_correct': True, 'new_correct': True, 'r0_prediction': 'nt', 'new_prediction': 'nt'}, '7': {'r0_correct': True, 'new_correct': True, 'r0_prediction': 'nt', 'new_prediction': 'nt'}}.

## Workload and limitations

- R0 and R1 share 12 whole-plate forward passes. R1 adds pooling only.
- R2 reuses each whole pass and adds 36 regional forward passes: four views per plate in total.
- R3 adds 9 marker-crop forward passes. All embeddings are cached before pair/query evaluation.
- Patch overlays use green for full-content patches, orange for partially padded selected patches, red for padding-only excluded patches, and magenta for the unpadded content boundary.
- Latency is recorded in representation_results.json after two warm-up forwards. Different views and patch counts imply different computation.
- Corresponding fixed regions may misalign category markings when their positions differ.
- R3 is evaluated only on 9 eligible images. Its result must not be compared directly with the 12-image R0 result as evidence of improvement.
- These are 12 development images; pair scores are correlated and physical independence is unverified where group identifiers are null. No generalization or calibrated confidence is claimed.
