# Controlled NT OCR Views

A reproduction discrepancies: none.
D blocked: PP-OCRv5_mobile_det is not cached; PaddleX reported no reachable official model host, so no regions were fabricated.

| Condition | TP | FP | FN | TN | Precision | Recall | Fused | Corrections | Regressions |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| A | 4 | 0 | 0 | 8 | 1.0 | 1.0 | 11/12 | ['5'] | [] |
| B | 4 | 0 | 0 | 8 | 1.0 | 1.0 | 11/12 | ['5'] | [] |
| C | 4 | 0 | 0 | 8 | 1.0 | 1.0 | 11/12 | ['5'] | [] |
| D | 0 | 0 | 0 | 0 | None | None | 10/12 | [] | [] |
| E | 4 | 0 | 0 | 8 | 1.0 | 1.0 | 11/12 | ['5'] | [] |
| ABC_any | 4 | 0 | 0 | 8 | 1.0 | 1.0 | 11/12 | ['5'] | [] |

## Per image

| ID | Expected | A | B | C | E | Multi |
|---:|---|---|---|---|---|---|
| 1 | idara | `17 - 138957` / absent | `17 - 138957` / absent | `957` / absent | `17 -138957` / absent | idara |
| 2 | idara | `17-18885` / absent | `17-18885` / absent | `8885` / absent | `17-148885` / absent | tunis |
| 3 | idara | `17 - 138956` / absent | `17 - 138956` / absent | `956` / absent | `17 - 138956]` / absent | idara |
| 4 | nt | `ن ت ١١٦٤` / present | `ن ت ١١٦٤` / present | `نت` / present | `نت214172` / present | nt |
| 5 | nt | `نت١0379` / present | `نت١0379` / present | `نت` / present | `نت03679` / present | nt |
| 6 | nt | `نت68` / present | `نت68` / present | `نت` / present | `نت176828` / present | nt |
| 7 | nt | `نت٦2٦75` / present | `نت٦2٦75` / present | `ن ت` / present | `نت72775` / present | nt |
| 8 | shorta | `300` / absent | `300` / absent | `330` / absent | `14330` / absent | shorta |
| 9 | shorta | `a27211` / absent | `a27211` / absent | `211` / absent | `27211ة` / absent | shorta |
| 10 | tunis | `توشن` / absent | `توشن` / absent | `603` / absent | `9603تونن150` / absent | tunis |
| 11 | tunis | `937 ن 2` / absent | `937 ن 2` / absent | `937` / absent | `937 3ن 122` / absent | tunis |
| 12 | tunis | `ؤن` / absent | `ؤن` / absent | `395` / absent | `395تنن105` / absent | tunis |

D has no fabricated output. These repeatedly used development images do not establish generalization.
