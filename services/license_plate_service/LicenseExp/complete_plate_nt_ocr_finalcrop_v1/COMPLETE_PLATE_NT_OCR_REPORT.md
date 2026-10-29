# Complete-Plate Arabic OCR and NT Fusion

All 12 complete plate crops were proportionally budget-resized using the saved DINOv3-S policy. OCR receives the unpadded resized crop; no marker crop, detector, thresholding, edge filter, or extra variant is used.

## Results

- NT detection: TP=4, FP=0, FN=0, TN=8.
- NT precision: 1.0; recall: 1.0.
- Visual baseline: 11/12 (0.917).
- Fixed fusion: 12/12 (1.000).
- Corrections: ['5']; regressions: none; false NT overrides: none.
- Conflicts: none; inference failures: none.

## Per-image outcomes

| Image | Expected | R2 prediction | Raw Arabic OCR | NT evidence | Fused prediction | Correct |
|---:|---|---|---|---|---|---:|
| 1 | idara | idara | `17 - 138957` | absent | idara | True |
| 2 | idara | idara | `17-148885` | absent | idara | True |
| 3 | idara | idara | `17 - 138956` | absent | idara | True |
| 4 | nt | nt | `ن ت ١١٦٤` | present | nt | True |
| 5 | nt | tunis | `نت١0379` | present | nt | True |
| 6 | nt | nt | `نت68` | present | nt | True |
| 7 | nt | nt | `نت٦2٦75` | present | nt | True |
| 8 | shorta | shorta | `300` | absent | shorta | True |
| 9 | shorta | shorta | `a27211` | absent | shorta | True |
| 10 | tunis | tunis | `توشن` | absent | tunis | True |
| 11 | tunis | tunis | `937 ن 2` | absent | tunis | True |
| 12 | tunis | tunis | `ؤن` | absent | tunis | True |

## Timing

- Model load: 2.315 s.
- Warm-up: 0.227 s.
- Recognition total: 1.429 s; mean completed image: 0.1191 s.

## Interpretation

OCR confidence is a native line-recognition score, not a calibrated plate-category probability. NT evidence absent does not mean definitely not NT. The fusion policy was fixed before examining these outputs and is not integrated into production.

These 12 repeatedly used development images cannot establish generalization. Any apparent gain requires physically independent validation plates.
