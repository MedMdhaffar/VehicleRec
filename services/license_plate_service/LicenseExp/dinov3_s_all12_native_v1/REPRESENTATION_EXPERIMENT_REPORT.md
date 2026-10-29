# Frozen-Encoder Plate Representation Experiment

Encoder: `dinov3_vits16`. Rectangular preprocessing: `native`. Colour input, float32 L2 normalization, and cosine comparison remain fixed.

R0 and R1 share each whole-plate tensor and forward output. R2 uses the whole view plus three independently processed regional views and a fixed corresponding-region score. R3 uses supplied manual marker boxes and does not demonstrate automatic localization.

## R0 reproducibility

- Equivalent to prior P0 budget: False
- Maximum absolute similarity difference: unavailable
- Nearest-neighbor predictions match: unavailable

## A. Full 12-image evaluation

| Representation | Correct | Macro-F1 | Balanced accuracy | Same mean | Different mean |
|---|---:|---:|---:|---:|---:|
| R0 whole-plate CLS | 6/12 | 0.493 | 0.521 | 0.534502 | 0.478387 |
| R1 mean selected patches | 5/12 | 0.408 | 0.437 | 0.783403 | 0.749896 |
| R2 whole + corresponding regions | 7/12 | 0.562 | 0.604 | 0.557786 | 0.474097 |

- R0 whole-plate CLS per-category recall: idara=2/3 (0.667), nt=1/4 (0.250), shorta=1/2 (0.500), tunis=2/3 (0.667)
- R1 mean selected patches per-category recall: idara=2/3 (0.667), nt=1/4 (0.250), shorta=1/2 (0.500), tunis=1/3 (0.333)
- R2 whole + corresponding regions per-category recall: idara=2/3 (0.667), nt=1/4 (0.250), shorta=1/2 (0.500), tunis=3/3 (1.000)

## B. Matched 9-image marker subset

| Representation | Correct | Macro-F1 | Balanced accuracy | Same mean | Different mean |
|---|---:|---:|---:|---:|---:|
| R0 whole-plate CLS | 5/9 | 0.546 | 0.556 | 0.511578 | 0.524214 |
| R1 mean selected patches | 5/9 | 0.546 | 0.556 | 0.761350 | 0.755898 |
| R2 whole + corresponding regions | 6/9 | 0.639 | 0.667 | 0.532050 | 0.499279 |
| R3 manual marker CLS | 6/9 | 0.689 | 0.750 | 0.540276 | 0.467633 |

- R0 whole-plate CLS per-category recall: nt=2/4 (0.500), shorta=1/2 (0.500), tunis=2/3 (0.667)
- R1 mean selected patches per-category recall: nt=2/4 (0.500), shorta=1/2 (0.500), tunis=2/3 (0.667)
- R2 whole + corresponding regions per-category recall: nt=2/4 (0.500), shorta=1/2 (0.500), tunis=3/3 (1.000)
- R3 manual marker CLS per-category recall: nt=1/4 (0.250), shorta=2/2 (1.000), tunis=3/3 (1.000)

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

- full_12, R1 mean selected patches: improved=none; worsened=['10']; changed=['10: tunis -> idara']; NT={'4': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'idara', 'new_prediction': 'idara'}, '5': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'shorta', 'new_prediction': 'shorta'}, '6': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'idara', 'new_prediction': 'idara'}, '7': {'r0_correct': True, 'new_correct': True, 'r0_prediction': 'nt', 'new_prediction': 'nt'}}.
- full_12, R2 whole + corresponding regions: improved=['12']; worsened=none; changed=['6: idara -> tunis', '12: shorta -> tunis']; NT={'4': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'idara', 'new_prediction': 'idara'}, '5': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'shorta', 'new_prediction': 'shorta'}, '6': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'idara', 'new_prediction': 'tunis'}, '7': {'r0_correct': True, 'new_correct': True, 'r0_prediction': 'nt', 'new_prediction': 'nt'}}.
- marker_9, R1 mean selected patches: improved=none; worsened=none; changed=none; NT={'4': {'r0_correct': True, 'new_correct': True, 'r0_prediction': 'nt', 'new_prediction': 'nt'}, '5': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'shorta', 'new_prediction': 'shorta'}, '6': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'tunis', 'new_prediction': 'tunis'}, '7': {'r0_correct': True, 'new_correct': True, 'r0_prediction': 'nt', 'new_prediction': 'nt'}}.
- marker_9, R2 whole + corresponding regions: improved=['12']; worsened=none; changed=['12: shorta -> tunis']; NT={'4': {'r0_correct': True, 'new_correct': True, 'r0_prediction': 'nt', 'new_prediction': 'nt'}, '5': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'shorta', 'new_prediction': 'shorta'}, '6': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'tunis', 'new_prediction': 'tunis'}, '7': {'r0_correct': True, 'new_correct': True, 'r0_prediction': 'nt', 'new_prediction': 'nt'}}.
- marker_9, R3 manual marker CLS: improved=['8', '12']; worsened=['4']; changed=['4: nt -> tunis', '5: shorta -> tunis', '8: tunis -> shorta', '12: shorta -> tunis']; NT={'4': {'r0_correct': True, 'new_correct': False, 'r0_prediction': 'nt', 'new_prediction': 'tunis'}, '5': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'shorta', 'new_prediction': 'tunis'}, '6': {'r0_correct': False, 'new_correct': False, 'r0_prediction': 'tunis', 'new_prediction': 'tunis'}, '7': {'r0_correct': True, 'new_correct': True, 'r0_prediction': 'nt', 'new_prediction': 'nt'}}.

## Workload and limitations

- R0 and R1 share 12 whole-plate forward passes. R1 adds pooling only.
- R2 reuses each whole pass and adds 36 regional forward passes: four views per plate in total.
- R3 adds 9 marker-crop forward passes. All embeddings are cached before pair/query evaluation.
- Patch overlays use green for full-content patches, orange for partially padded selected patches, red for padding-only excluded patches, and magenta for the unpadded content boundary.
- Latency is recorded in representation_results.json after two warm-up forwards. Different views and patch counts imply different computation.
- Corresponding fixed regions may misalign category markings when their positions differ.
- R3 is evaluated only on 9 eligible images. Its result must not be compared directly with the 12-image R0 result as evidence of improvement.
- These are 12 development images; pair scores are correlated and physical independence is unverified where group identifiers are null. No generalization or calibrated confidence is claimed.
