# Encoder, Colour, and Shape Plate Experiment

Branches remain separate; raw scores and accuracies across unequal subsets are not fused or treated as calibrated confidence.

## Model availability

- e0_dinov2_small: **completed** — loaded successfully
- e1_dinov3_small: **blocked** — OSError: Can't load image processor for 'facebook/dinov3-vits16-pretrain-lvd1689m'. If you were trying to load it from 'https://huggingface.co/models', make sure you don't have a local directory with the same name. Otherwise, make sure 'facebook/dinov3-vits16-pretrain-lvd1689m' is the correct path to a directory containing a preprocessor_config.json file
- e2_siglip2_naflex_base: **blocked** — OSError: We couldn't connect to 'https://huggingface.co' to load the files, and couldn't find them in the cached files.
Check your internet connection or see how to run the library in offline mode at 'https://huggingface.co/docs/transformers/installation#offline-mode'.

## Baseline reproduction

- Whole E0 maximum matrix difference: 3.57627869e-07
- Marker E0 maximum matrix difference: 2.38418579e-07
- Tolerance: 1e-06

## Full ten-image/four-category results

| Branch | Method | Scope | Coverage | Correct | Macro-F1 | Balanced accuracy |
|---|---|---|---:|---:|---:|---:|
| embedding | e0_dinov2_small | whole | 10/10 | 5/10 | 0.476 | 0.500 |
| embedding | e1_dinov3_small | whole | 0/10 | blocked | — | — |
| embedding | e2_siglip2_naflex_base | whole | 0/10 | blocked | — | — |
| colour | c0_hsv_joint | whole | 10/10 | 1/10 | 0.071 | 0.083 |
| colour | c1_bright_dark_hsv | whole | 10/10 | 1/10 | 0.071 | 0.083 |

## Matched seven-image/three-category results

| Branch | Method | Scope | Coverage | Correct | Macro-F1 | Balanced accuracy |
|---|---|---|---:|---:|---:|---:|
| embedding | e0_dinov2_small | whole | 7/7 | 4/7 | 0.524 | 0.556 |
| embedding | e0_dinov2_small | manual_marker | 7/7 | 4/7 | 0.524 | 0.556 |
| embedding | e1_dinov3_small | whole | 0/7 | blocked | — | — |
| embedding | e1_dinov3_small | manual_marker | 0/7 | blocked | — | — |
| embedding | e2_siglip2_naflex_base | whole | 0/7 | blocked | — | — |
| embedding | e2_siglip2_naflex_base | manual_marker | 0/7 | blocked | — | — |
| colour | c0_hsv_joint | whole | 7/7 | 1/7 | 0.083 | 0.111 |
| colour | c1_bright_dark_hsv | whole | 7/7 | 1/7 | 0.095 | 0.111 |
| shape | s0_template_correlation | manual_marker | 7/7 | 1/7 | 0.111 | 0.167 |
| shape | s1_symmetric_chamfer | manual_marker | 7/7 | 5/7 | 0.556 | 0.667 |

## Historical reference

Previous DINOv2 regional R2 (not rerun as a new encoder configuration): full=7/10, matched marker subset=5/7.

## Error comparison

- full_10 c0_hsv_joint: improved=['10'], worsened=['1', '3', '6', '7', '9'], NT predictions={'4': 'idara', '5': 'tunis'}.
- full_10 c1_bright_dark_hsv: improved=['10'], worsened=['1', '3', '6', '7', '9'], NT predictions={'4': 'idara', '5': 'tunis'}.
- marker_7 c0_hsv_joint: improved=['10'], worsened=['6', '7', '8', '9'], NT predictions={'4': 'tunis', '5': 'tunis'}.
- marker_7 c1_bright_dark_hsv: improved=['10'], worsened=['6', '7', '8', '9'], NT predictions={'4': 'shorta', '5': 'tunis'}.
- marker_7 s0_template_correlation: improved=['4'], worsened=['6', '7', '8', '9'], NT predictions={'4': 'nt', '5': 'tunis'}.
- marker_7 s1_symmetric_chamfer: improved=['10'], worsened=none, NT predictions={'4': 'shorta', '5': 'shorta'}.

## Runtime measurement

Warm feature-extraction latency was not measured because only E0 was available; a cross-encoder timing comparison would be incomplete. Model loading and the failed download attempt are not treated as inference latency.

## Interpretation limits

E0/E1/E2 would compare encoder-plus-preprocessing systems because their official preprocessors differ. E1 and E2 were not available, so no fabricated scores are reported. Colour groups are Otsu bright/dark intensity proxies, not guaranteed foreground/background. Shape methods assume ideal manual marker localization. These ten images are development data, pair observations are correlated, and physical independence is unverified where group IDs are null.
