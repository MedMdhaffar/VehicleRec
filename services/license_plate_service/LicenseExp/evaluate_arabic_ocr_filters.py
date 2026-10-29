#!/usr/bin/env python3
"""Compare fixed original, Otsu, Canny, and Otsu-to-Canny Arabic OCR inputs."""

from __future__ import annotations

import argparse
import csv
import json
import platform
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Sequence

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from arabic_marker_ocr import (
    ArabicOCRExperimentError,
    EXPECTED_MARKINGS,
    FILTER_CONDITIONS,
    FILTER_PARAMETERS,
    category_from_normalized_marker,
    extract_marker_crops,
    fixed_filter_variants,
    normalize_arabic_marker,
    sha256_file,
    unwrap_paddle_result,
)
from evaluate_arabic_marker_ocr import (
    LICENSE,
    MODEL_DOCUMENTATION,
    MODEL_NAME,
    MODULE_DOCUMENTATION,
    NORMALIZATION_POLICY,
    metrics,
    package_version,
    write_csv,
)
from plate_variants import validate_manifest


RESULT_COLUMNS = (
    "image_id", "condition", "expected_category", "expected_marking",
    "expected_normalized_marking", "source_path", "crop_bbox_xyxy_exclusive",
    "processed_path", "processed_sha256", "submitted_width", "submitted_height",
    "submitted_channels", "raw_text", "normalized_text", "native_confidence",
    "predicted_category", "raw_exact_marker_match", "normalized_exact_marker_match",
    "correct_category", "recognition_seconds", "inference_status", "failure_reason",
)
SUMMARY_COLUMNS = (
    "condition", "queries", "raw_exact_marker_matches",
    "normalized_exact_marker_matches", "category_correct", "category_accuracy",
    "nt_correct", "nt_total", "shorta_correct", "shorta_total", "tunis_correct",
    "tunis_total", "accepted_predictions", "acceptance_coverage",
    "accepted_correct", "accepted_accuracy", "mean_recognition_seconds",
    "median_recognition_seconds", "total_recognition_seconds",
    "improved_query_ids", "regressed_query_ids",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("controlled_plate_manifest.json"))
    parser.add_argument("--baseline-dir", type=Path, default=Path("arabic_ocr_ppocrv5_v4"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", default="cpu")
    return parser.parse_args()


def load_baseline(path: Path) -> dict[str, dict[str, str]]:
    csv_path = path / "ocr_results.csv"
    if not csv_path.exists():
        raise ArabicOCRExperimentError(f"Missing baseline CSV: {csv_path}")
    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 7 or any(row["inference_status"] != "completed" for row in rows):
        raise ArabicOCRExperimentError("Baseline must contain seven completed rows")
    indexed = {row["image_id"]: row for row in rows}
    if list(indexed) != ["4", "5", "6", "7", "8", "9", "10"]:
        raise ArabicOCRExperimentError("Baseline image IDs are not ordered 4 through 10")
    return indexed


def save_contact_sheet(
    path: Path,
    records: Sequence[dict[str, Any]],
    variants: dict[str, dict[str, Any]],
) -> None:
    figure, axes = plt.subplots(7, 4, figsize=(13, 20), squeeze=False)
    for row_index, record in enumerate(records):
        image_id = str(record["id"])
        for column_index, condition in enumerate(FILTER_CONDITIONS):
            axis = axes[row_index, column_index]
            image = variants[image_id][condition]
            axis.imshow(cv2.cvtColor(image, cv2.COLOR_BGR2RGB), interpolation="nearest")
            if row_index == 0:
                axis.set_title(condition.replace("_", " → "), fontsize=11)
            if column_index == 0:
                axis.set_ylabel(
                    f"{image_id}.png\n{record['category']}\n{image.shape[1]}×{image.shape[0]}",
                    fontsize=9,
                )
            axis.set_xticks([])
            axis.set_yticks([])
    figure.suptitle(
        "Arabic marker OCR inputs — display enlargement only; inference uses saved native-size PNGs",
        fontsize=14,
    )
    figure.tight_layout()
    figure.savefig(path, dpi=170, bbox_inches="tight")
    plt.close(figure)


def make_result_row(
    record: dict[str, Any],
    condition: str,
    processed_path: Path,
    raw_text: str,
    confidence: float | None,
    elapsed: float,
) -> dict[str, Any]:
    expected = EXPECTED_MARKINGS[str(record["id"])]
    expected_normalized = normalize_arabic_marker(expected)
    normalized = normalize_arabic_marker(raw_text)
    predicted = category_from_normalized_marker(normalized)
    image = cv2.imread(str(processed_path), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ArabicOCRExperimentError(f"Could not reload exact inference image: {processed_path}")
    return {
        "image_id": str(record["id"]), "condition": condition,
        "expected_category": record["category"], "expected_marking": expected,
        "expected_normalized_marking": expected_normalized,
        "source_path": str(record["path"]),
        "crop_bbox_xyxy_exclusive": json.dumps(record["marker_bbox"]),
        "processed_path": str(processed_path), "processed_sha256": sha256_file(processed_path),
        "submitted_width": image.shape[1], "submitted_height": image.shape[0],
        "submitted_channels": image.shape[2], "raw_text": raw_text,
        "normalized_text": normalized,
        "native_confidence": "" if confidence is None else confidence,
        "predicted_category": predicted, "raw_exact_marker_match": raw_text == expected,
        "normalized_exact_marker_match": normalized == expected_normalized,
        "correct_category": predicted == record["category"],
        "recognition_seconds": elapsed, "inference_status": "completed", "failure_reason": "",
    }


def summarize(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    original = {row["image_id"]: row for row in rows if row["condition"] == "original"}
    summaries = []
    for condition in FILTER_CONDITIONS:
        selected = [row for row in rows if row["condition"] == condition]
        result = metrics(selected)
        if result is None:
            raise ArabicOCRExperimentError(f"{condition}: incomplete inference rows")
        accepted = [row for row in selected if row["predicted_category"] != "unknown"]
        times = [float(row["recognition_seconds"]) for row in selected]
        improved = [
            row["image_id"] for row in selected
            if not original[row["image_id"]]["correct_category"] and row["correct_category"]
        ]
        regressed = [
            row["image_id"] for row in selected
            if original[row["image_id"]]["correct_category"] and not row["correct_category"]
        ]
        summaries.append({
            "condition": condition, "queries": 7,
            "raw_exact_marker_matches": result["raw_exact_marker_matches"],
            "normalized_exact_marker_matches": result["normalized_exact_marker_matches"],
            "category_correct": result["category_correct"],
            "category_accuracy": result["category_accuracy"],
            "nt_correct": result["confusion_matrix"]["nt"]["nt"], "nt_total": 2,
            "shorta_correct": result["confusion_matrix"]["shorta"]["shorta"], "shorta_total": 2,
            "tunis_correct": result["confusion_matrix"]["tunis"]["tunis"], "tunis_total": 3,
            "accepted_predictions": len(accepted), "acceptance_coverage": len(accepted) / 7,
            "accepted_correct": sum(bool(row["correct_category"]) for row in accepted),
            "accepted_accuracy": (
                sum(bool(row["correct_category"]) for row in accepted) / len(accepted)
                if accepted else None
            ),
            "mean_recognition_seconds": statistics.fmean(times),
            "median_recognition_seconds": statistics.median(times),
            "total_recognition_seconds": sum(times),
            "improved_query_ids": ";".join(improved), "regressed_query_ids": ";".join(regressed),
            "metrics": result,
        })
    return summaries


def baseline_comparison(
    rows: Sequence[dict[str, Any]], baseline: dict[str, dict[str, str]]
) -> dict[str, Any]:
    original = [row for row in rows if row["condition"] == "original"]
    comparisons = []
    for row in original:
        prior = baseline[row["image_id"]]
        comparisons.append({
            "image_id": row["image_id"],
            "raw_text_match": row["raw_text"] == prior["raw_text"],
            "normalized_text_match": row["normalized_text"] == prior["normalized_text"],
            "predicted_category_match": row["predicted_category"] == prior["predicted_category"],
            "correctness_match": str(row["correct_category"]) == prior["correct_category"],
            "prior_raw_text": prior["raw_text"], "reproduced_raw_text": row["raw_text"],
            "prior_confidence": float(prior["native_confidence"]),
            "reproduced_confidence": float(row["native_confidence"]),
        })
    return {
        "all_raw_text_match": all(item["raw_text_match"] for item in comparisons),
        "all_normalized_text_match": all(item["normalized_text_match"] for item in comparisons),
        "all_predictions_match": all(item["predicted_category_match"] for item in comparisons),
        "all_correctness_match": all(item["correctness_match"] for item in comparisons),
        "per_image": comparisons,
    }


def write_report(
    path: Path,
    rows: Sequence[dict[str, Any]],
    summaries: Sequence[dict[str, Any]],
    reproduction: dict[str, Any],
    timing: dict[str, Any],
) -> None:
    lines = [
        "# Arabic OCR Fixed-Filter Experiment", "",
        "The same seven manual marker crops, recognizer, decoder, normalization, and exact category mapping are used in every condition. Display enlargement is not used for inference.", "",
        "## Baseline reproduction", "",
        f"All raw text matched saved v4: **{reproduction['all_raw_text_match']}**. All normalized text, category predictions, and correctness also matched: **{reproduction['all_normalized_text_match'] and reproduction['all_predictions_match'] and reproduction['all_correctness_match']}**.", "",
        "## Summary", "",
        "| Condition | Exact | Category correct | NT | Shorta | Tunis | Accepted | Accepted correct | Accepted accuracy |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in summaries:
        accepted_accuracy = "—" if item["accepted_accuracy"] is None else f"{item['accepted_accuracy']:.3f}"
        lines.append(
            f"| {item['condition']} | {item['normalized_exact_marker_matches']}/7 | "
            f"{item['category_correct']}/7 | {item['nt_correct']}/2 | "
            f"{item['shorta_correct']}/2 | {item['tunis_correct']}/3 | "
            f"{item['accepted_predictions']}/7 | {item['accepted_correct']}/{item['accepted_predictions']} | {accepted_accuracy} |"
        )
    lines.extend(["", "## Changes relative to reproduced original", ""])
    for item in summaries:
        lines.append(
            f"- {item['condition']}: improved={item['improved_query_ids'] or 'none'}; "
            f"regressed={item['regressed_query_ids'] or 'none'}."
        )
    lines.extend([
        "", "## Per-image OCR output", "",
        "Each cell is `raw text → category; confidence; correct`.", "",
        "| Image | Expected | Original | Otsu | Canny | Otsu → Canny |",
        "|---|---|---|---|---|---|",
    ])
    indexed = {(row["image_id"], row["condition"]): row for row in rows}
    for image_id in ["4", "5", "6", "7", "8", "9", "10"]:
        cells = []
        for condition in FILTER_CONDITIONS:
            row = indexed[(image_id, condition)]
            cells.append(
                f"`{row['raw_text']}` → {row['predicted_category']}; "
                f"{float(row['native_confidence']):.3f}; {row['correct_category']}"
            )
        lines.append(
            f"| {image_id} | {EXPECTED_MARKINGS[image_id]} | " + " | ".join(cells) + " |"
        )
    lines.extend([
        "", "## Timing", "",
        f"- Model load: {timing['model_load_seconds']:.6f} s",
        f"- Warm-up: {timing['warmup_seconds']:.6f} s",
    ])
    for item in summaries:
        lines.append(
            f"- {item['condition']}: total={item['total_recognition_seconds']:.6f} s; "
            f"mean={item['mean_recognition_seconds']:.6f} s/image; "
            f"median={item['median_recognition_seconds']:.6f} s/image."
        )
    lines.extend([
        "", "## Interpretation limits", "",
        "Canny inputs are exploratory edge maps and may discard appearance information needed by the recognizer. No condition or per-image choice was tuned using labels. These seven manually localized images are development data and do not establish generalization or automatic-localization performance.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    if args.output_dir.exists():
        raise ArabicOCRExperimentError(f"Output directory already exists: {args.output_dir}")
    baseline = load_baseline(args.baseline_dir)
    records, validation = validate_manifest(args.manifest)
    if not validation["valid"]:
        raise ArabicOCRExperimentError("Manifest validation failed: " + "; ".join(validation["errors"]))
    marker_records, crops = extract_marker_crops(records)
    args.output_dir.mkdir(parents=True)
    processed_root = args.output_dir / "processed_crops"
    processed_root.mkdir()

    variants_by_id: dict[str, dict[str, Any]] = {}
    processed_paths: dict[tuple[str, str], Path] = {}
    for record, crop in zip(marker_records, crops):
        image_id = str(record["id"])
        variants = fixed_filter_variants(crop)
        variants_by_id[image_id] = variants
        for condition, image in variants.items():
            directory = processed_root / condition
            directory.mkdir(exist_ok=True)
            path = directory / f"{image_id}_marker.png"
            if not cv2.imwrite(str(path), image):
                raise ArabicOCRExperimentError(f"Failed to save {path}")
            processed_paths[(image_id, condition)] = path
    save_contact_sheet(args.output_dir / "filter_contact_sheet.png", marker_records, variants_by_id)

    initial_configuration = {
        "schema_version": 1, "status": "running", "manifest": str(args.manifest),
        "manifest_sha256": sha256_file(args.manifest), "baseline_dir": str(args.baseline_dir),
        "baseline_csv_sha256": sha256_file(args.baseline_dir / "ocr_results.csv"),
        "model": MODEL_NAME, "device": args.device,
        "conditions_in_fixed_order": list(FILTER_CONDITIONS),
        "filter_parameters_fixed_before_inference": FILTER_PARAMETERS,
        "normalization_policy_unchanged": NORMALIZATION_POLICY,
        "recognizer_internal_preprocessing": "official TextRecognition defaults; unchanged across conditions",
    }
    config_path = args.output_dir / "experiment_configuration.json"
    config_path.write_text(json.dumps(initial_configuration, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    try:
        from paddleocr import TextRecognition
    except Exception as exc:
        raise ArabicOCRExperimentError(f"PaddleOCR unavailable: {type(exc).__name__}: {exc}") from exc
    load_started = time.perf_counter()
    model = TextRecognition(model_name=MODEL_NAME, device=args.device)
    load_seconds = time.perf_counter() - load_started
    warm_started = time.perf_counter()
    warm = list(model.predict(input=str(processed_paths[("4", "original")]), batch_size=1))
    warm_seconds = time.perf_counter() - warm_started
    if len(warm) != 1:
        raise ArabicOCRExperimentError("Warm-up did not return exactly one result")

    rows, native_outputs = [], []
    for condition in FILTER_CONDITIONS:
        for record in marker_records:
            image_id = str(record["id"])
            path = processed_paths[(image_id, condition)]
            started = time.perf_counter()
            outputs = list(model.predict(input=str(path), batch_size=1))
            elapsed = time.perf_counter() - started
            if len(outputs) != 1:
                raise ArabicOCRExperimentError(
                    f"{condition} image {image_id}: expected one OCR result, got {len(outputs)}"
                )
            raw_text, confidence, native = unwrap_paddle_result(outputs[0])
            rows.append(make_result_row(record, condition, path, raw_text, confidence, elapsed))
            native_outputs.append({"image_id": image_id, "condition": condition, "payload": native})

    summaries = summarize(rows)
    reproduction = baseline_comparison(rows, baseline)
    timing = {
        "model_load_seconds": load_seconds, "warmup_seconds": warm_seconds,
        "recognition_seconds_excludes_loading_and_warmup": True,
    }
    write_csv(args.output_dir / "ocr_filter_results.csv", rows, RESULT_COLUMNS)
    write_csv(args.output_dir / "summary_metrics.csv", summaries, SUMMARY_COLUMNS)
    (args.output_dir / "native_outputs.json").write_text(json.dumps(native_outputs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    complete_configuration = initial_configuration | {
        "status": "completed", "model_metadata": {
            "identifier": MODEL_NAME, "license": LICENSE,
            "documentation": MODEL_DOCUMENTATION, "module_documentation": MODULE_DOCUMENTATION,
        },
        "runtime_versions": {
            "python": platform.python_version(), "paddleocr": package_version("paddleocr"),
            "paddlepaddle": package_version("paddlepaddle"),
            "opencv-python": package_version("opencv-python"), "numpy": package_version("numpy"),
        },
        "timing": timing, "baseline_reproduction": reproduction,
        "summary_by_condition": {item["condition"]: item["metrics"] for item in summaries},
    }
    config_path.write_text(json.dumps(complete_configuration, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(args.output_dir / "ARABIC_OCR_FILTER_REPORT.md", rows, summaries, reproduction, timing)
    print(f"Filter experiment written to: {args.output_dir.resolve()}")


def main() -> int:
    try:
        run(parse_args())
        return 0
    except ArabicOCRExperimentError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

