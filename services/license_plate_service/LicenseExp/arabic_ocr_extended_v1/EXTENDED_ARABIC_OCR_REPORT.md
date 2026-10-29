# Extended Arabic OCR Filter Experiment

Nine manually localized category markers are evaluated: NT=4 images, Shorta=2, Tunis=3. Idara is excluded. The approved new NT boxes are used without adjustment.

## Legacy reproduction

All 7 renamed legacy images reproduce their v4 raw text, normalized text, and prediction: **True**.

## Summary

| Condition | Exact | Correct | NT | Shorta | Tunis | Accepted | Accepted correct | Accepted accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| original | 4/9 | 4/9 | 4/4 | 0/2 | 0/3 | 4/9 | 4/4 | 1.000 |
| otsu | 4/9 | 4/9 | 4/4 | 0/2 | 0/3 | 4/9 | 4/4 | 1.000 |
| canny | 2/9 | 2/9 | 2/4 | 0/2 | 0/3 | 2/9 | 2/2 | 1.000 |
| otsu_canny | 2/9 | 2/9 | 2/4 | 0/2 | 0/3 | 2/9 | 2/2 | 1.000 |

## Changes from original

- original: improved=none; regressed=none.
- otsu: improved=none; regressed=none.
- canny: improved=none; regressed=5;6.
- otsu_canny: improved=none; regressed=4;5.

## Per-image OCR

Each cell is `raw text → category; confidence; correct`.

| Image | Expected | Original | Otsu | Canny | Otsu → Canny |
|---|---|---|---|---|---|
| 4 | ن ت | `ن ت` → nt; 0.860; True | `ن ت` → nt; 0.894; True | `ن ت` → nt; 0.416; True | `ق ت` → unknown; 0.421; False |
| 5 | ن ت | `ن ت` → nt; 0.862; True | `ن ت` → nt; 0.754; True | `نلت` → unknown; 0.473; False | `L` → unknown; 0.178; False |
| 6 | ن ت | `ن ت` → nt; 0.772; True | `نت` → nt; 0.911; True | `تت` → unknown; 0.576; False | `نت` → nt; 0.793; True |
| 7 | ن ت | `ن ت` → nt; 0.824; True | `نت` → nt; 0.927; True | `نت` → nt; 0.830; True | `نت` → nt; 0.874; True |
| 8 | شرطة | `نسرية` → unknown; 0.561; False | `يَ` → unknown; 0.366; False | `49` → unknown; 0.344; False | `` → unknown; 0.000; False |
| 9 | شرطة | `شرطلة` → unknown; 0.728; False | `نرة` → unknown; 0.503; False | `=` → unknown; 0.333; False | `2` → unknown; 0.241; False |
| 10 | تونس | `تونن` → unknown; 0.934; False | `تونسن` → unknown; 0.791; False | `تونيت` → unknown; 0.507; False | `تونين` → unknown; 0.746; False |
| 11 | تونس | `تون` → unknown; 0.862; False | `توشن` → unknown; 0.644; False | `قَ` → unknown; 0.331; False | `ن` → unknown; 0.195; False |
| 12 | تونس | `تون` → unknown; 0.902; False | `تقوينن` → unknown; 0.742; False | `قوقشن` → unknown; 0.507; False | `قونن` → unknown; 0.569; False |

## Timing

- Model load: 3.530060 s
- Warm-up: 0.235230 s
- original: total=0.515203 s; mean=0.057245 s/image; median=0.054793 s/image.
- otsu: total=0.668039 s; mean=0.074227 s/image; median=0.070270 s/image.
- canny: total=0.643040 s; mean=0.071449 s/image; median=0.062792 s/image.
- otsu_canny: total=0.831620 s; mean=0.092402 s/image; median=0.048765 s/image.

## Limits

The two new NT marker boxes were manually approved, but the nine images remain development examples with unverified physical independence. No filter was selected per image, no fuzzy text repair was used, and these results do not establish generalization or automatic localization performance.
