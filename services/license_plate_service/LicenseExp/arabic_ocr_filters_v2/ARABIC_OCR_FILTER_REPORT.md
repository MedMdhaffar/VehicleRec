# Arabic OCR Fixed-Filter Experiment

The same seven manual marker crops, recognizer, decoder, normalization, and exact category mapping are used in every condition. Display enlargement is not used for inference.

## Baseline reproduction

All raw text matched saved v4: **True**. All normalized text, category predictions, and correctness also matched: **True**.

## Summary

| Condition | Exact | Category correct | NT | Shorta | Tunis | Accepted | Accepted correct | Accepted accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| original | 2/7 | 2/7 | 2/2 | 0/2 | 0/3 | 2/7 | 2/2 | 1.000 |
| otsu | 2/7 | 2/7 | 2/2 | 0/2 | 0/3 | 2/7 | 2/2 | 1.000 |
| canny | 1/7 | 1/7 | 1/2 | 0/2 | 0/3 | 1/7 | 1/1 | 1.000 |
| otsu_canny | 0/7 | 0/7 | 0/2 | 0/2 | 0/3 | 0/7 | 0/0 | — |

## Changes relative to reproduced original

- original: improved=none; regressed=none.
- otsu: improved=none; regressed=none.
- canny: improved=none; regressed=5.
- otsu_canny: improved=none; regressed=4;5.

## Per-image OCR output

Each cell is `raw text → category; confidence; correct`.

| Image | Expected | Original | Otsu | Canny | Otsu → Canny |
|---|---|---|---|---|---|
| 4 | ن ت | `ن ت` → nt; 0.860; True | `ن ت` → nt; 0.894; True | `ن ت` → nt; 0.416; True | `ق ت` → unknown; 0.421; False |
| 5 | ن ت | `ن ت` → nt; 0.862; True | `ن ت` → nt; 0.754; True | `نلت` → unknown; 0.473; False | `L` → unknown; 0.178; False |
| 6 | شرطة | `نسرية` → unknown; 0.561; False | `يَ` → unknown; 0.366; False | `49` → unknown; 0.344; False | `` → unknown; 0.000; False |
| 7 | شرطة | `شرطلة` → unknown; 0.728; False | `نرة` → unknown; 0.503; False | `=` → unknown; 0.333; False | `2` → unknown; 0.241; False |
| 8 | تونس | `تونن` → unknown; 0.934; False | `تونسن` → unknown; 0.791; False | `تونيت` → unknown; 0.507; False | `تونين` → unknown; 0.746; False |
| 9 | تونس | `تون` → unknown; 0.862; False | `توشن` → unknown; 0.644; False | `قَ` → unknown; 0.331; False | `ن` → unknown; 0.195; False |
| 10 | تونس | `تون` → unknown; 0.902; False | `تقوينن` → unknown; 0.742; False | `قوقشن` → unknown; 0.507; False | `قونن` → unknown; 0.569; False |

## Timing

- Model load: 0.937152 s
- Warm-up: 0.152467 s
- original: total=0.328235 s; mean=0.046891 s/image; median=0.042502 s/image.
- otsu: total=0.279075 s; mean=0.039868 s/image; median=0.034615 s/image.
- canny: total=0.304835 s; mean=0.043548 s/image; median=0.039430 s/image.
- otsu_canny: total=0.346389 s; mean=0.049484 s/image; median=0.041138 s/image.

## Interpretation limits

Canny inputs are exploratory edge maps and may discard appearance information needed by the recognizer. No condition or per-image choice was tuned using labels. These seven manually localized images are development data and do not establish generalization or automatic-localization performance.
