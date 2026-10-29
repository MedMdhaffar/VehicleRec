# Complete-Plate Arabic OCR and NT Fusion

All 12 complete plate crops were proportionally budget-resized using the saved DINOv3-S policy. OCR receives the unpadded resized crop; no marker crop, detector, thresholding, edge filter, or extra variant is used.

## Results

- NT detection: TP=2, FP=0, FN=2, TN=8.
- NT precision: 1.0; recall: 0.5.
- Visual baseline: 8/12 (0.667).
- Fixed fusion: 10/12 (0.833).
- Corrections: ['4', '5']; regressions: none; false NT overrides: none.
- Conflicts: none; inference failures: none.

## Per-image outcomes

| Image | Expected | R2 prediction | Raw Arabic OCR | NT evidence | Fused prediction | Correct |
|---:|---|---|---|---|---|---:|
| 1 | idara | idara | `17 - 138957` | absent | idara | True |
| 2 | idara | nt | `17-18885` | absent | nt | False |
| 3 | idara | idara | `17 - 138956` | absent | idara | True |
| 4 | nt | idara | `ن ت ١١٦٤` | present | nt | True |
| 5 | nt | tunis | `نت١0379` | present | nt | True |
| 6 | nt | tunis | `16828 00` | absent | tunis | False |
| 7 | nt | nt | `727750` | absent | nt | True |
| 8 | shorta | shorta | `300` | absent | shorta | True |
| 9 | shorta | shorta | `a27211` | absent | shorta | True |
| 10 | tunis | tunis | `توشن` | absent | tunis | True |
| 11 | tunis | tunis | `937 ن 2` | absent | tunis | True |
| 12 | tunis | tunis | `ؤن` | absent | tunis | True |

## Timing

- Model load: 2.376 s.
- Warm-up: 0.232 s.
- Recognition total: 0.995 s; mean completed image: 0.0829 s.

## Interpretation

OCR confidence is a native line-recognition score, not a calibrated plate-category probability. NT evidence absent does not mean definitely not NT. The fusion policy was fixed before examining these outputs and is not integrated into production.

These 12 repeatedly used development images cannot establish generalization. Any apparent gain requires physically independent validation plates.
