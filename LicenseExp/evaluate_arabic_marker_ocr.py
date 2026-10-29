#!/usr/bin/env python3
"""Evaluate one fixed Arabic recognizer on the seven manual marker crops."""

from __future__ import annotations

import argparse
import csv
import importlib.metadata
import json
import platform
import sys
import time
from pathlib import Path
from typing import Any, Sequence

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from arabic_marker_ocr import (
    ArabicOCRExperimentError,
    EXPECTED_MARKINGS,
    category_from_normalized_marker,
    extract_marker_crops,
    normalize_arabic_marker,
    sha256_file,
    unwrap_paddle_result,
)
from plate_variants import validate_manifest


MODEL_NAME = "arabic_PP-OCRv5_mobile_rec"
MODEL_DOCUMENTATION = "https://www.paddleocr.ai/main/en/version3.x/algorithm/PP-OCRv5/PP-OCRv5_multi_languages.html"
MODULE_DOCUMENTATION = "https://www.paddleocr.ai/main/en/version3.x/module_usage/text_recognition.html"
LICENSE = "Apache-2.0"
NORMALIZATION_POLICY = {
    "unicode": "NFKC (converts compatibility/presentation forms)",
    "removed": ["Unicode category Mn diacritics/marks", "U+0640 tatweel", "all Unicode whitespace"],
    "preserved": "base-letter identity, including dot distinctions; no substitutions, fuzzy matching, or label-conditioned repair",
    "exact_mapping": {"نت": "nt", "شرطة": "shorta", "تونس": "tunis"},
    "fallback": "unknown",
}
RESULT_COLUMNS = [
    "image_id", "expected_category", "expected_marking", "expected_normalized_marking",
    "source_path", "crop_path", "crop_x1", "crop_y1", "crop_x2", "crop_y2",
    "submitted_width", "submitted_height", "raw_text", "raw_text_available",
    "native_confidence", "confidence_semantics", "normalized_text", "predicted_category",
    "raw_exact_marker_match", "normalized_exact_marker_match", "correct_category",
    "recognition_seconds", "inference_status", "failure_reason",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("controlled_plate_manifest.json"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--previous-results", type=Path, default=Path("encoder_colour_shape_online_v4"))
    parser.add_argument("--prepare-only", action="store_true", help="Validate/save crops and record missing OCR runtime without loading a model.")
    parser.add_argument("--device", default="cpu", help="PaddleOCR local device string; fixed for the complete run record.")
    return parser.parse_args()


def write_csv(path: Path, rows: Sequence[dict[str, Any]], columns: Sequence[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def load_previous_failures(path: Path) -> list[dict[str, Any]]:
    csv_path = path / "leave_one_out_predictions.csv"
    if not csv_path.exists():
        raise ArabicOCRExperimentError(f"Missing authoritative previous predictions: {csv_path}")
    failures = []
    with csv_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if (
                row["method"] == "e2_siglip2_naflex_base"
                and row["input_scope"] == "manual_marker"
                and row["evaluation_subset"] == "marker_7"
                and row["valid"] == "True"
                and row["correct"] == "False"
            ):
                failures.append({
                    "query_id": row["query_id"],
                    "query_category": row["query_category"],
                    "nearest_reference_id": row["nearest_reference_id"],
                    "predicted_category": row["predicted_category"],
                    "nearest_similarity": float(row["nearest_raw_score"]),
                    "correct_category_margin": float(row["correct_category_margin"]),
                })
    if [row["query_id"] for row in failures] != ["4", "5"]:
        raise ArabicOCRExperimentError(f"Expected SigLIP failures 4 and 5, got {failures}")
    return failures


def save_contact_sheet(path: Path, records: Sequence[dict[str, Any]], crops: Sequence[np.ndarray]) -> None:
    figure, axes = plt.subplots(4, 2, figsize=(11, 13))
    for axis in axes.flat:
        axis.axis("off")
    for axis, record, crop in zip(axes.flat, records, crops):
        axis.imshow(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB), interpolation="nearest")
        width, height = crop.shape[1], crop.shape[0]
        axis.set_title(
            f"{record['id']}.png | {record['category']} | expected {EXPECTED_MARKINGS[str(record['id'])]}\n"
            f"bbox={record['marker_bbox']} exclusive | {width}×{height}px",
            fontsize=9,
        )
        axis.axis("on")
        axis.set_xticks([])
        axis.set_yticks([])
    figure.suptitle("Manual category-marker crops — unchanged pixels, nearest-neighbour display scaling")
    figure.tight_layout()
    figure.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(figure)


def blocked_rows(records: Sequence[dict[str, Any]], crop_paths: Sequence[Path], reason: str) -> list[dict[str, Any]]:
    rows = []
    for record, crop_path in zip(records, crop_paths):
        x1, y1, x2, y2 = record["marker_bbox"]
        rows.append(base_row(record, crop_path) | {
            "raw_text": "", "raw_text_available": False, "native_confidence": "",
            "normalized_text": "", "predicted_category": "unknown",
            "raw_exact_marker_match": "", "normalized_exact_marker_match": "",
            "correct_category": "", "recognition_seconds": "", "inference_status": "blocked",
            "failure_reason": reason,
        })
    return rows


def base_row(record: dict[str, Any], crop_path: Path) -> dict[str, Any]:
    x1, y1, x2, y2 = record["marker_bbox"]
    expected = EXPECTED_MARKINGS[str(record["id"])]
    return {
        "image_id": str(record["id"]), "expected_category": record["category"],
        "expected_marking": expected, "expected_normalized_marking": normalize_arabic_marker(expected),
        "source_path": str(record["path"]), "crop_path": str(crop_path),
        "crop_x1": x1, "crop_y1": y1, "crop_x2": x2, "crop_y2": y2,
        "submitted_width": x2 - x1, "submitted_height": y2 - y1,
        "confidence_semantics": "PaddleOCR rec_score: native confidence score for predicted text-line text; not calibrated here",
    }


def result_payload(result: Any) -> Any:
    payload = getattr(result, "json", result)
    return payload() if callable(payload) else payload


def run_inference(records: Sequence[dict[str, Any]], crop_paths: Sequence[Path], device: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        from paddleocr import TextRecognition
    except Exception as exc:
        raise ArabicOCRExperimentError(f"PaddleOCR runtime unavailable: {type(exc).__name__}: {exc}") from exc
    load_start = time.perf_counter()
    model = TextRecognition(model_name=MODEL_NAME, device=device)
    load_seconds = time.perf_counter() - load_start

    warm_start = time.perf_counter()
    warm_outputs = list(model.predict(input=str(crop_paths[0]), batch_size=1))
    warm_seconds = time.perf_counter() - warm_start
    if len(warm_outputs) != 1:
        raise ArabicOCRExperimentError("Warm-up did not return exactly one recognition result")

    rows, native_payloads = [], []
    for record, crop_path in zip(records, crop_paths):
        started = time.perf_counter()
        outputs = list(model.predict(input=str(crop_path), batch_size=1))
        elapsed = time.perf_counter() - started
        if len(outputs) != 1:
            raise ArabicOCRExperimentError(f"Image {record['id']}: expected one OCR result, got {len(outputs)}")
        raw_text, confidence, native_payload = unwrap_paddle_result(outputs[0])
        native_payloads.append({"image_id": str(record["id"]), "payload": native_payload})
        normalized = normalize_arabic_marker(raw_text)
        predicted = category_from_normalized_marker(normalized)
        expected = EXPECTED_MARKINGS[str(record["id"])]
        rows.append(base_row(record, crop_path) | {
            "raw_text": raw_text, "raw_text_available": True,
            "native_confidence": "" if confidence is None else confidence,
            "normalized_text": normalized, "predicted_category": predicted,
            "raw_exact_marker_match": raw_text == expected,
            "normalized_exact_marker_match": normalized == normalize_arabic_marker(expected),
            "correct_category": predicted == record["category"],
            "recognition_seconds": elapsed, "inference_status": "completed", "failure_reason": "",
        })
    return rows, {"model_load_seconds": load_seconds, "warmup_seconds": warm_seconds, "native_payloads": native_payloads}


def metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows or any(row["inference_status"] != "completed" for row in rows):
        return None
    categories = ["nt", "shorta", "tunis"]
    confusion = {actual: {predicted: 0 for predicted in categories + ["unknown"]} for actual in categories}
    for row in rows:
        confusion[row["expected_category"]][row["predicted_category"]] += 1
    accepted = [row for row in rows if row["predicted_category"] != "unknown"]
    return {
        "queries": len(rows),
        "raw_exact_marker_matches": sum(bool(row["raw_exact_marker_match"]) for row in rows),
        "normalized_exact_marker_matches": sum(bool(row["normalized_exact_marker_match"]) for row in rows),
        "category_correct": sum(bool(row["correct_category"]) for row in rows),
        "category_accuracy": sum(bool(row["correct_category"]) for row in rows) / len(rows),
        "per_category_recall": {
            category: sum(row["correct_category"] for row in rows if row["expected_category"] == category)
            / sum(row["expected_category"] == category for row in rows)
            for category in categories
        },
        "confusion_matrix": confusion,
        "accepted_predictions": len(accepted),
        "acceptance_coverage": len(accepted) / len(rows),
        "accepted_accuracy": (sum(row["correct_category"] for row in accepted) / len(accepted)) if accepted else None,
    }


def write_report(path: Path, status: str, blocker: str | None, failures: Sequence[dict[str, Any]], rows: Sequence[dict[str, Any]], summary: dict[str, Any] | None) -> None:
    lines = [
        "# Arabic Marker OCR Experiment", "",
        "This standalone development experiment uses the seven supplied manual category-marker boxes and does not perform text detection or registration-digit OCR.", "",
        "## Previous SigLIP marker failures", "",
        "| Query | Expected | Nearest reference | Prediction | Similarity | Correct-category margin |",
        "|---|---|---|---|---:|---:|",
    ]
    for item in failures:
        lines.append(f"| {item['query_id']} | {item['query_category']} | {item['nearest_reference_id']} | {item['predicted_category']} | {item['nearest_similarity']:.6f} | {item['correct_category_margin']:.6f} |")
    lines.extend([
        "", "## Recognizer", "",
        f"Selected model: `{MODEL_NAME}` (Apache-2.0). It explicitly supports Arabic and accepts cropped text-line images through PaddleOCR's standalone `TextRecognition` API. `rec_score` is preserved as the model's native predicted-text confidence; it is not calibrated or thresholded in this experiment.", "",
        "The primary condition uses the recognizer's documented preprocessing without image variants or tuned settings. Submitted crop dimensions are recorded; PaddleOCR's public result does not expose a distinct post-preprocessing tensor shape, so that internal dimension is recorded as unavailable rather than inferred.", "",
        "## Crop review", "",
        "Coordinates are original-image `xyxy` pixels with exclusive `x2/y2`. The crops are saved unchanged. Visual acceptance or ambiguity must be recorded in `crop_review.json`; no coordinates are adjusted by this tool.", "",
        "## Inference status", "",
        f"Status: **{status}**.",
    ])
    if blocker:
        lines.append(f"Blocker: {blocker}")
    if summary is None:
        lines.extend(["", "No OCR accuracy, confidence, or timing result is claimed because recognition did not run. Empty CSV text fields mean unavailable due to the blocker, not a model prediction of an empty string."])
    else:
        lines.extend([
            "", "## Results", "",
            f"- Raw exact-marker matches: {summary['raw_exact_marker_matches']}/{summary['queries']}",
            f"- Normalized exact-marker matches: {summary['normalized_exact_marker_matches']}/{summary['queries']}",
            f"- Category accuracy: {summary['category_correct']}/{summary['queries']} ({summary['category_accuracy']:.3f})",
            f"- Acceptance coverage: {summary['accepted_predictions']}/{summary['queries']} ({summary['acceptance_coverage']:.3f})",
            f"- Accuracy among accepted predictions: {summary['accepted_accuracy'] if summary['accepted_accuracy'] is not None else 'unavailable'}",
            "", "## Per-image output", "",
            "| ID | Expected | Raw text | Normalized | Prediction | Confidence | Correct |",
            "|---|---|---|---|---|---:|---|",
        ])
        for row in rows:
            lines.append(f"| {row['image_id']} | {row['expected_marking']} | `{row['raw_text']}` | `{row['normalized_text']}` | {row['predicted_category']} | {row['native_confidence']} | {row['correct_category']} |")
    lines.extend([
        "", "## Limits", "",
        "These seven manually localized crops are development data with unverified physical independence. Results cannot establish generalization or automatic marker-localization performance. Expected labels are used only for evaluation and never for OCR normalization or category repair.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    if args.output_dir.exists():
        raise ArabicOCRExperimentError(f"Output directory already exists; refusing overwrite: {args.output_dir}")
    records, validation = validate_manifest(args.manifest)
    if not validation["valid"]:
        raise ArabicOCRExperimentError("Manifest validation failed: " + "; ".join(validation["errors"]))
    marker_records, crops = extract_marker_crops(records)
    previous_failures = load_previous_failures(args.previous_results)

    args.output_dir.mkdir(parents=True)
    crop_dir = args.output_dir / "marker_crops"
    crop_dir.mkdir()
    crop_paths = []
    crop_records = []
    for record, crop in zip(marker_records, crops):
        path = crop_dir / f"{record['id']}_marker.png"
        if not cv2.imwrite(str(path), crop):
            raise ArabicOCRExperimentError(f"Failed to save {path}")
        crop_paths.append(path)
        crop_records.append({
            "image_id": str(record["id"]), "source_path": str(record["path"]),
            "source_sha256": sha256_file(Path(record["path"])), "crop_path": str(path),
            "crop_sha256": sha256_file(path), "bbox_xyxy_exclusive": record["marker_bbox"],
            "crop_dimensions_wh": [crop.shape[1], crop.shape[0]],
            "expected_category": record["category"], "expected_marking": EXPECTED_MARKINGS[str(record["id"])],
        })
    save_contact_sheet(args.output_dir / "marker_contact_sheet.png", marker_records, crops)
    (args.output_dir / "normalization_policy.json").write_text(json.dumps(NORMALIZATION_POLICY, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "crop_manifest.json").write_text(json.dumps(crop_records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    crop_review = {
        "status": "pending_visual_review",
        "instruction": "Review marker_contact_sheet.png. Record complete/ambiguous per image without changing coordinates.",
        "images": {str(record["id"]): {"complete_marking": None, "ambiguous": None, "notes": ""} for record in marker_records},
    }
    (args.output_dir / "crop_review.json").write_text(json.dumps(crop_review, indent=2) + "\n", encoding="utf-8")

    blocker = None
    timing: dict[str, Any] = {"model_load_seconds": None, "warmup_seconds": None, "per_image_semantics": "wall time around model.predict for one crop; load and warm-up excluded"}
    try:
        if args.prepare_only:
            raise ArabicOCRExperimentError("Preparation-only run requested; inference not attempted")
        rows, measured = run_inference(marker_records, crop_paths, args.device)
        timing.update({key: value for key, value in measured.items() if key != "native_payloads"})
        (args.output_dir / "native_outputs.json").write_text(json.dumps(measured["native_payloads"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        status = "completed"
    except ArabicOCRExperimentError as exc:
        blocker = str(exc)
        status = "blocked"
        rows = blocked_rows(marker_records, crop_paths, blocker)
    write_csv(args.output_dir / "ocr_results.csv", rows, RESULT_COLUMNS)
    summary = metrics(rows)
    configuration = {
        "schema_version": 1, "status": status, "blocker": blocker,
        "manifest_path": str(args.manifest), "manifest_sha256": sha256_file(args.manifest),
        "coordinate_convention": "original decoded image xyxy; x2/y2 exclusive",
        "model": {"identifier": MODEL_NAME, "license": LICENSE, "documentation": MODEL_DOCUMENTATION, "module_documentation": MODULE_DOCUMENTATION, "device": args.device, "detection_bypassed": True, "preprocessing": "official TextRecognition defaults; no image variant", "internal_postprocessed_input_dimensions": "unavailable from public Result API"},
        "dependencies": {"python": platform.python_version(), "paddleocr": package_version("paddleocr"), "paddlepaddle": package_version("paddlepaddle"), "opencv-python": package_version("opencv-python"), "numpy": package_version("numpy")},
        "normalization_policy": NORMALIZATION_POLICY, "timing": timing,
        "previous_siglip_source": str(args.previous_results), "previous_siglip_failures": previous_failures,
        "crop_records": crop_records, "metrics": summary,
    }
    (args.output_dir / "experiment_configuration.json").write_text(json.dumps(configuration, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(args.output_dir / "ARABIC_OCR_REPORT.md", status, blocker, previous_failures, rows, summary)
    print(f"Arabic OCR experiment {status}: {args.output_dir.resolve()}")


def main() -> int:
    try:
        run(parse_args())
        return 0
    except ArabicOCRExperimentError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

