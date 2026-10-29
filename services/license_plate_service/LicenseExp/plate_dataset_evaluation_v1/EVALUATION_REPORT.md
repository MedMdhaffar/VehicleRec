# Plate Dataset Similarity Evaluation

This is a descriptive frozen-embedding comparison, not a trained classifier or a held-out accuracy study.

## Dataset

- Images: 10
- Categories: 4
- Unique pairs: 45
- Same-category pairs: 8
- Different-category pairs: 37
- Counts: idara=3, nt=2, shorta=2, tunis=3

## Summary

| Method | Same mean | Different mean | Mean gap | ROC-AUC | Nearest-neighbor label match |
|---|---:|---:|---:|---:|---:|
| original | 0.697939 | 0.501092 | +0.196847 | 0.915541 | 0.800 |
| native | 0.471468 | 0.385363 | +0.086105 | 0.658784 | 0.300 |
| budget | 0.503194 | 0.431536 | +0.071658 | 0.638514 | 0.500 |

Higher similarity is not accuracy. ROC-AUC here only measures how the 8 same-category pair scores rank against the 37 different-category scores in this small, reused dataset. Nearest-neighbor label match is in-sample retrieval, not generalization.

## Highest-scoring pairs by method

### original

| Pair | Categories | Same category | Similarity |
|---|---|---:|---:|
| idara:1 / idara:2 | idara / idara | True | 0.846753 |
| tunis:2 / tunis:3 | tunis / tunis | True | 0.824038 |
| shorta:1 / shorta:2 | shorta / shorta | True | 0.765720 |
| idara:2 / nt:1 | idara / nt | False | 0.698465 |
| nt:2 / shorta:1 | nt / shorta | False | 0.698451 |
| tunis:1 / tunis:2 | tunis / tunis | True | 0.675615 |
| tunis:1 / tunis:3 | tunis / tunis | True | 0.674307 |
| idara:1 / nt:1 | idara / nt | False | 0.652222 |
| idara:2 / idara:3 | idara / idara | True | 0.652042 |
| nt:2 / shorta:2 | nt / shorta | False | 0.645518 |

### native

| Pair | Categories | Same category | Similarity |
|---|---|---:|---:|
| idara:1 / idara:2 | idara / idara | True | 0.847612 |
| idara:2 / nt:1 | idara / nt | False | 0.769763 |
| idara:1 / nt:1 | idara / nt | False | 0.692941 |
| idara:3 / tunis:2 | idara / tunis | False | 0.558323 |
| idara:3 / nt:1 | idara / nt | False | 0.543999 |
| idara:3 / tunis:1 | idara / tunis | False | 0.542866 |
| nt:2 / tunis:3 | nt / tunis | False | 0.539645 |
| nt:1 / tunis:1 | nt / tunis | False | 0.525598 |
| idara:2 / tunis:3 | idara / tunis | False | 0.524381 |
| shorta:1 / tunis:3 | shorta / tunis | False | 0.510581 |

### budget

| Pair | Categories | Same category | Similarity |
|---|---|---:|---:|
| idara:1 / idara:2 | idara / idara | True | 0.760976 |
| idara:2 / nt:1 | idara / nt | False | 0.728040 |
| idara:1 / nt:1 | idara / nt | False | 0.695607 |
| idara:3 / tunis:1 | idara / tunis | False | 0.655781 |
| tunis:1 / tunis:2 | tunis / tunis | True | 0.618589 |
| shorta:1 / shorta:2 | shorta / shorta | True | 0.587168 |
| nt:2 / tunis:3 | nt / tunis | False | 0.586578 |
| tunis:2 / tunis:3 | tunis / tunis | True | 0.566763 |
| idara:2 / shorta:1 | idara / shorta | False | 0.560504 |
| nt:2 / shorta:1 | nt / shorta | False | 0.557108 |

## Limitations

The dataset has only 10 images, categories contain two or three examples, and crops may share capture conditions or vehicles. Results can be dominated by plate color, layout, resolution, framing, or padding. Positional interpolation preserves rectangular grids but does not make embeddings invariant to resolution or crop context. Use more independent images and a held-out protocol before drawing category-level conclusions.
