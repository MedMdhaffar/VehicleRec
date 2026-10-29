# Controlled Plate-Preparation Evaluation

Encoder, CLS extraction, float32 L2 normalization, cosine similarity, and model-input policy are fixed. Colour/grayscale/CLAHE describe image preparation; original/native/budget are historical model-input modes computed on unchanged colour P0.

## Classification summary

| Condition | Correct | Macro-F1 | Balanced accuracy | Same mean | Different mean |
|---|---:|---:|---:|---:|---:|
| original | 8/10 | 0.664 | 0.750 | 0.697939 | 0.501092 |
| native | 3/10 | 0.292 | 0.292 | 0.471468 | 0.385363 |
| budget | 5/10 | 0.476 | 0.500 | 0.503194 | 0.431536 |
| p0_budget | 5/10 | 0.476 | 0.500 | 0.503194 | 0.431536 |
| grayscale_budget | 4/10 | 0.375 | 0.375 | 0.519710 | 0.450716 |
| clahe_budget | 4/10 | 0.375 | 0.375 | 0.497034 | 0.447554 |

## Per-category recall

- original: idara=3/3 (1.000), nt=0/2 (0.000), shorta=2/2 (1.000), tunis=3/3 (1.000)
- native: idara=2/3 (0.667), nt=0/2 (0.000), shorta=1/2 (0.500), tunis=0/3 (0.000)
- budget: idara=2/3 (0.667), nt=0/2 (0.000), shorta=2/2 (1.000), tunis=1/3 (0.333)
- p0_budget: idara=2/3 (0.667), nt=0/2 (0.000), shorta=2/2 (1.000), tunis=1/3 (0.333)
- grayscale_budget: idara=2/3 (0.667), nt=0/2 (0.000), shorta=1/2 (0.500), tunis=1/3 (0.333)
- clahe_budget: idara=2/3 (0.667), nt=0/2 (0.000), shorta=1/2 (0.500), tunis=1/3 (0.333)

## Confusion matrices

### original

Rows are true categories; columns are predicted categories.

| true / predicted | idara | nt | shorta | tunis |
|---|---:|---:|---:|---:|
| idara | 3 | 0 | 0 | 0 |
| nt | 1 | 0 | 1 | 0 |
| shorta | 0 | 0 | 2 | 0 |
| tunis | 0 | 0 | 0 | 3 |

### native

Rows are true categories; columns are predicted categories.

| true / predicted | idara | nt | shorta | tunis |
|---|---:|---:|---:|---:|
| idara | 2 | 0 | 0 | 1 |
| nt | 1 | 0 | 0 | 1 |
| shorta | 0 | 0 | 1 | 1 |
| tunis | 2 | 1 | 0 | 0 |

### budget

Rows are true categories; columns are predicted categories.

| true / predicted | idara | nt | shorta | tunis |
|---|---:|---:|---:|---:|
| idara | 2 | 0 | 0 | 1 |
| nt | 1 | 0 | 0 | 1 |
| shorta | 0 | 0 | 2 | 0 |
| tunis | 1 | 1 | 0 | 1 |

### p0_budget

Rows are true categories; columns are predicted categories.

| true / predicted | idara | nt | shorta | tunis |
|---|---:|---:|---:|---:|
| idara | 2 | 0 | 0 | 1 |
| nt | 1 | 0 | 0 | 1 |
| shorta | 0 | 0 | 2 | 0 |
| tunis | 1 | 1 | 0 | 1 |

### grayscale_budget

Rows are true categories; columns are predicted categories.

| true / predicted | idara | nt | shorta | tunis |
|---|---:|---:|---:|---:|
| idara | 2 | 0 | 0 | 1 |
| nt | 1 | 0 | 0 | 1 |
| shorta | 1 | 0 | 1 | 0 |
| tunis | 1 | 1 | 0 | 1 |

### clahe_budget

Rows are true categories; columns are predicted categories.

| true / predicted | idara | nt | shorta | tunis |
|---|---:|---:|---:|---:|
| idara | 2 | 0 | 0 | 1 |
| nt | 1 | 0 | 0 | 1 |
| shorta | 1 | 0 | 1 | 0 |
| tunis | 1 | 1 | 0 | 1 |

## Per-query correct-category margins

- original: 1=0.148289, 2=0.030476, 3=0.194532, 4=-0.123761, 5=-0.123747, 6=0.067269, 7=0.120202, 8=0.120427, 9=0.359789, 10=0.314499
- native: 1=0.077848, 2=-0.102045, 3=0.154670, 4=-0.489837, 5=-0.259718, 6=-0.005217, 7=0.057952, 8=-0.082975, 9=-0.100189, 10=-0.079754
- budget: 1=0.032936, 2=-0.269940, 3=0.065369, 4=-0.405055, 5=-0.263593, 6=0.026664, 7=0.138349, 8=-0.037192, 9=0.076248, 10=-0.019816
- p0_budget: 1=0.032936, 2=-0.269940, 3=0.065369, 4=-0.405055, 5=-0.263593, 6=0.026664, 7=0.138349, 8=-0.037192, 9=0.076248, 10=-0.019816
- grayscale_budget: 1=0.049247, 2=-0.217297, 3=0.077251, 4=-0.369031, 5=-0.247055, 6=-0.083522, 7=0.082020, 8=-0.073863, 9=0.049723, 10=-0.048343
- clahe_budget: 1=0.073451, 2=-0.332704, 3=0.099146, 4=-0.367269, 5=-0.268149, 6=-0.051628, 7=0.069211, 8=-0.092624, 9=0.062359, 10=-0.032754

## Controlled comparisons

- grayscale_budget versus p0_budget: improved queries=none; worsened queries=['6']; changed predictions=['6: shorta -> idara'].
- clahe_budget versus p0_budget: improved queries=none; worsened queries=['6']; changed predictions=['6: shorta -> idara'].

## Interpretation limits

These ten images are exploratory development data, not a held-out test set. The 45 pair scores are correlated observations. Repeated configuration selection on these same images does not establish generalization. Higher average similarity does not select a winner; category predictions and margins must be inspected. No acceptance threshold or calibrated confidence is reported. Physical independence is unverified where group metadata is null. Perspective correction is postponed.
