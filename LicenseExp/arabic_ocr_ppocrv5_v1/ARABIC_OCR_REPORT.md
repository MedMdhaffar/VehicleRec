# Arabic Marker OCR Experiment

This standalone development experiment uses the seven supplied manual category-marker boxes and does not perform text detection or registration-digit OCR.

## Previous SigLIP marker failures

| Query | Expected | Nearest reference | Prediction | Similarity | Correct-category margin |
|---|---|---|---|---:|---:|
| 4 | nt | 8 | tunis | 0.869022 | -0.193885 |
| 5 | nt | 6 | shorta | 0.903340 | -0.228204 |

## Recognizer

Selected model: `arabic_PP-OCRv5_mobile_rec` (Apache-2.0). It explicitly supports Arabic and accepts cropped text-line images through PaddleOCR's standalone `TextRecognition` API. `rec_score` is preserved as the model's native predicted-text confidence; it is not calibrated or thresholded in this experiment.

The primary condition uses the recognizer's documented preprocessing without image variants or tuned settings. Submitted crop dimensions are recorded; PaddleOCR's public result does not expose a distinct post-preprocessing tensor shape, so that internal dimension is recorded as unavailable rather than inferred.

## Crop review

Coordinates are original-image `xyxy` pixels with exclusive `x2/y2`. The crops are saved unchanged. Visual acceptance or ambiguity must be recorded in `crop_review.json`; no coordinates are adjusted by this tool.

## Inference status

Status: **blocked**.
Blocker: Preparation-only run requested; inference not attempted

No OCR accuracy, confidence, or timing result is claimed because recognition did not run. Empty CSV text fields mean unavailable due to the blocker, not a model prediction of an empty string.

## Limits

These seven manually localized crops are development data with unverified physical independence. Results cannot establish generalization or automatic marker-localization performance. Expected labels are used only for evaluation and never for OCR normalization or category repair.
