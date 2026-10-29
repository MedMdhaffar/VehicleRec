#!/usr/bin/env python3
"""Run the fixed four-condition Arabic OCR comparison on the extended marker set."""

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
    FILTER_CONDITIONS,
    FILTER_PARAMETERS,
    category_from_normalized_marker,
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
    package_version,
    write_csv,
)


CATEGORY_MARKINGS = {"nt": "ن ت", "shorta": "شرطة", "tunis": "تونس"}
RESULT_COLUMNS = (
    "image_id", "condition", "expected_category", "expected_marking",
    "expected_normalized_marking", "source_path", "source_sha256",
    "crop_bbox_xyxy_exclusive", "crop_width", "crop_height", "processed_path",
    "processed_sha256", "raw_text", "normalized_text", "native_confidence",
    "predicted_category", "raw_exact_marker_match", "normalized_exact_marker_match",
    "correct_category", "recognition_seconds", "inference_status", "failure_reason",
)
SUMMARY_COLUMNS = (
    "condition", "queries", "exact_matches", "category_correct", "category_accuracy",
    "nt_correct", "nt_total", "shorta_correct", "shorta_total", "tunis_correct",
    "tunis_total", "accepted_count", "accepted_coverage", "accepted_correct",
    "accepted_accuracy", "improved_ids", "regressed_ids", "mean_seconds",
    "median_seconds", "total_seconds",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("arabic_ocr_extended_manifest.json"))
    parser.add_argument("--baseline-dir", type=Path, default=Path("arabic_ocr_ppocrv5_v4"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", default="cpu")
    return parser.parse_args()


def load_manifest(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in data.get("images", []):
        image_id = str(item["id"])
        if image_id in seen:
            raise ArabicOCRExperimentError(f"Duplicate extended image ID: {image_id}")
        seen.add(image_id)
        if item["category"] not in CATEGORY_MARKINGS:
            raise ArabicOCRExperimentError(f"Image {image_id}: unsupported category")
        if item["expected_marking"] != CATEGORY_MARKINGS[item["category"]]:
            raise ArabicOCRExperimentError(f"Image {image_id}: expected marking/category mismatch")
        source = Path(item["path"])
        image = cv2.imread(str(source), cv2.IMREAD_COLOR)
        if image is None:
            raise ArabicOCRExperimentError(f"Image {image_id}: cannot decode {source}")
        height, width = image.shape[:2]
        if [width, height] != [item["expected_width"], item["expected_height"]]:
            raise ArabicOCRExperimentError(
                f"Image {image_id}: decoded {width}x{height}, expected "
                f"{item['expected_width']}x{item['expected_height']}"
            )
        box = item["marker_bbox"]
        if len(box) != 4 or any(not isinstance(value, int) for value in box):
            raise ArabicOCRExperimentError(f"Image {image_id}: invalid marker box")
        x1, y1, x2, y2 = box
        if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            raise ArabicOCRExperimentError(f"Image {image_id}: out-of-bounds marker box")
        crop = image[y1:y2, x1:x2].copy()
        records.append(item | {
            "id": image_id, "source": source, "image": image, "crop": crop,
            "source_sha256": sha256_file(source),
        })
    if [record["id"] for record in records] != [str(value) for value in range(4, 13)]:
        raise ArabicOCRExperimentError("Extended manifest must contain ordered IDs 4 through 12")
    return records, data


def load_baseline(path: Path) -> dict[str, dict[str, str]]:
    with (path / "ocr_results.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 7 or any(row["inference_status"] != "completed" for row in rows):
        raise ArabicOCRExperimentError("The v4 baseline is not seven completed rows")
    return {row["image_id"]: row for row in rows}


def save_contact_sheet(
    path: Path,
    records: Sequence[dict[str, Any]],
    variants: dict[str, dict[str, Any]],
) -> None:
    figure, axes = plt.subplots(len(records), 4, figsize=(13, 25), squeeze=False)
    for row_index, record in enumerate(records):
        for column_index, condition in enumerate(FILTER_CONDITIONS):
            axis = axes[row_index, column_index]
            image = variants[record["id"]][condition]
            axis.imshow(cv2.cvtColor(image, cv2.COLOR_BGR2RGB), interpolation="nearest")
            if row_index == 0:
                axis.set_title(condition.replace("_", " → "), fontsize=11)
            if column_index == 0:
                axis.set_ylabel(
                    f"{record['id']}.png\n{record['category']}\n"
                    f"{image.shape[1]}×{image.shape[0]}", fontsize=9,
                )
            axis.set_xticks([])
            axis.set_yticks([])
    figure.suptitle(
        "Extended Arabic marker inputs — native-size inference; nearest-neighbour display enlargement",
        fontsize=14,
    )
    figure.tight_layout()
    figure.savefig(path, dpi=170, bbox_inches="tight")
    plt.close(figure)


def summarize(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    original = {row["image_id"]: row for row in rows if row["condition"] == "original"}
    output = []
    for condition in FILTER_CONDITIONS:
        selected = [row for row in rows if row["condition"] == condition]
        accepted = [row for row in selected if row["predicted_category"] != "unknown"]
        times = [float(row["recognition_seconds"]) for row in selected]
        counts = {}
        for category in CATEGORY_MARKINGS:
            category_rows = [row for row in selected if row["expected_category"] == category]
            counts[category] = (
                sum(bool(row["correct_category"]) for row in category_rows), len(category_rows)
            )
        improved = [row["image_id"] for row in selected if not original[row["image_id"]]["correct_category"] and row["correct_category"]]
        regressed = [row["image_id"] for row in selected if original[row["image_id"]]["correct_category"] and not row["correct_category"]]
        correct = sum(bool(row["correct_category"]) for row in selected)
        output.append({
            "condition": condition, "queries": len(selected),
            "exact_matches": sum(bool(row["normalized_exact_marker_match"]) for row in selected),
            "category_correct": correct, "category_accuracy": correct / len(selected),
            "nt_correct": counts["nt"][0], "nt_total": counts["nt"][1],
            "shorta_correct": counts["shorta"][0], "shorta_total": counts["shorta"][1],
            "tunis_correct": counts["tunis"][0], "tunis_total": counts["tunis"][1],
            "accepted_count": len(accepted), "accepted_coverage": len(accepted) / len(selected),
            "accepted_correct": sum(bool(row["correct_category"]) for row in accepted),
            "accepted_accuracy": (
                sum(bool(row["correct_category"]) for row in accepted) / len(accepted)
                if accepted else None
            ),
            "improved_ids": ";".join(improved), "regressed_ids": ";".join(regressed),
            "mean_seconds": statistics.fmean(times), "median_seconds": statistics.median(times),
            "total_seconds": sum(times),
        })
    return output


def baseline_reproduction(
    rows: Sequence[dict[str, Any]], records: Sequence[dict[str, Any]], baseline: dict[str, dict[str, str]]
) -> dict[str, Any]:
    original = {row["image_id"]: row for row in rows if row["condition"] == "original"}
    checks = []
    for record in records:
        previous_id = record.get("baseline_v4_id")
        if previous_id is None:
            continue
        row, previous = original[record["id"]], baseline[previous_id]
        checks.append({
            "extended_id": record["id"], "baseline_v4_id": previous_id,
            "raw_text_match": row["raw_text"] == previous["raw_text"],
            "normalized_text_match": row["normalized_text"] == previous["normalized_text"],
            "prediction_match": row["predicted_category"] == previous["predicted_category"],
        })
    return {"legacy_images_checked": len(checks), "all_match": all(all(item[key] for key in ("raw_text_match", "normalized_text_match", "prediction_match")) for item in checks), "checks": checks}


def write_report(
    path: Path,
    rows: Sequence[dict[str, Any]],
    summaries: Sequence[dict[str, Any]],
    reproduction: dict[str, Any],
    timing: dict[str, Any],
) -> None:
    lines = [
        "# Extended Arabic OCR Filter Experiment", "",
        "Nine manually localized category markers are evaluated: NT=4 images, Shorta=2, Tunis=3. Idara is excluded. The approved new NT boxes are used without adjustment.", "",
        "## Legacy reproduction", "",
        f"All {reproduction['legacy_images_checked']} renamed legacy images reproduce their v4 raw text, normalized text, and prediction: **{reproduction['all_match']}**.", "",
        "## Summary", "",
        "| Condition | Exact | Correct | NT | Shorta | Tunis | Accepted | Accepted correct | Accepted accuracy |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in summaries:
        accepted_accuracy = "—" if item["accepted_accuracy"] is None else f"{item['accepted_accuracy']:.3f}"
        lines.append(
            f"| {item['condition']} | {item['exact_matches']}/9 | {item['category_correct']}/9 | "
            f"{item['nt_correct']}/4 | {item['shorta_correct']}/2 | {item['tunis_correct']}/3 | "
            f"{item['accepted_count']}/9 | {item['accepted_correct']}/{item['accepted_count']} | {accepted_accuracy} |"
        )
    lines.extend(["", "## Changes from original", ""])
    for item in summaries:
        lines.append(f"- {item['condition']}: improved={item['improved_ids'] or 'none'}; regressed={item['regressed_ids'] or 'none'}.")
    lines.extend([
        "", "## Per-image OCR", "",
        "Each cell is `raw text → category; confidence; correct`.", "",
        "| Image | Expected | Original | Otsu | Canny | Otsu → Canny |",
        "|---|---|---|---|---|---|",
    ])
    indexed = {(row["image_id"], row["condition"]): row for row in rows}
    for image_id in [str(value) for value in range(4, 13)]:
        cells = []
        for condition in FILTER_CONDITIONS:
            row = indexed[(image_id, condition)]
            cells.append(f"`{row['raw_text']}` → {row['predicted_category']}; {float(row['native_confidence']):.3f}; {row['correct_category']}")
        lines.append(f"| {image_id} | {indexed[(image_id, 'original')]['expected_marking']} | " + " | ".join(cells) + " |")
    lines.extend([
        "", "## Timing", "",
        f"- Model load: {timing['model_load_seconds']:.6f} s",
        f"- Warm-up: {timing['warmup_seconds']:.6f} s",
    ])
    for item in summaries:
        lines.append(f"- {item['condition']}: total={item['total_seconds']:.6f} s; mean={item['mean_seconds']:.6f} s/image; median={item['median_seconds']:.6f} s/image.")
    lines.extend([
        "", "## Limits", "",
        "The two new NT marker boxes were manually approved, but the nine images remain development examples with unverified physical independence. No filter was selected per image, no fuzzy text repair was used, and these results do not establish generalization or automatic localization performance.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    if args.output_dir.exists():
        raise ArabicOCRExperimentError(f"Output directory already exists: {args.output_dir}")
    records, manifest_data = load_manifest(args.manifest)
    baseline = load_baseline(args.baseline_dir)
    args.output_dir.mkdir(parents=True)
    processed_root = args.output_dir / "processed_crops"
    processed_root.mkdir()
    variants_by_id, paths = {}, {}
    for record in records:
        variants = fixed_filter_variants(record["crop"])
        variants_by_id[record["id"]] = variants
        for condition, image in variants.items():
            directory = processed_root / condition
            directory.mkdir(exist_ok=True)
            path = directory / f"{record['id']}_marker.png"
            if not cv2.imwrite(str(path), image):
                raise ArabicOCRExperimentError(f"Failed to save {path}")
            paths[(record["id"], condition)] = path
    save_contact_sheet(args.output_dir / "extended_filter_contact_sheet.png", records, variants_by_id)

    config_path = args.output_dir / "experiment_configuration.json"
    initial = {
        "schema_version": 1, "status": "running", "manifest": str(args.manifest),
        "manifest_sha256": sha256_file(args.manifest), "manifest_content": manifest_data,
        "baseline_dir": str(args.baseline_dir), "model": MODEL_NAME, "device": args.device,
        "filter_parameters_fixed_before_inference": FILTER_PARAMETERS,
        "normalization_policy_unchanged": NORMALIZATION_POLICY,
    }
    config_path.write_text(json.dumps(initial, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    try:
        from paddleocr import TextRecognition
    except Exception as exc:
        raise ArabicOCRExperimentError(f"PaddleOCR unavailable: {type(exc).__name__}: {exc}") from exc
    started = time.perf_counter()
    model = TextRecognition(model_name=MODEL_NAME, device=args.device)
    load_seconds = time.perf_counter() - started
    started = time.perf_counter()
    warm = list(model.predict(input=str(paths[("4", "original")]), batch_size=1))
    warm_seconds = time.perf_counter() - started
    if len(warm) != 1:
        raise ArabicOCRExperimentError("Warm-up did not return exactly one result")

    rows, native = [], []
    for condition in FILTER_CONDITIONS:
        for record in records:
            path = paths[(record["id"], condition)]
            started = time.perf_counter()
            outputs = list(model.predict(input=str(path), batch_size=1))
            elapsed = time.perf_counter() - started
            if len(outputs) != 1:
                raise ArabicOCRExperimentError(f"{record['id']} {condition}: expected one result")
            raw_text, confidence, payload = unwrap_paddle_result(outputs[0])
            normalized = normalize_arabic_marker(raw_text)
            predicted = category_from_normalized_marker(normalized)
            expected = record["expected_marking"]
            rows.append({
                "image_id": record["id"], "condition": condition,
                "expected_category": record["category"], "expected_marking": expected,
                "expected_normalized_marking": normalize_arabic_marker(expected),
                "source_path": str(record["source"]), "source_sha256": record["source_sha256"],
                "crop_bbox_xyxy_exclusive": json.dumps(record["marker_bbox"]),
                "crop_width": record["crop"].shape[1], "crop_height": record["crop"].shape[0],
                "processed_path": str(path), "processed_sha256": sha256_file(path),
                "raw_text": raw_text, "normalized_text": normalized,
                "native_confidence": "" if confidence is None else confidence,
                "predicted_category": predicted, "raw_exact_marker_match": raw_text == expected,
                "normalized_exact_marker_match": normalized == normalize_arabic_marker(expected),
                "correct_category": predicted == record["category"],
                "recognition_seconds": elapsed, "inference_status": "completed", "failure_reason": "",
            })
            native.append({"image_id": record["id"], "condition": condition, "payload": payload})

    summaries = summarize(rows)
    reproduction = baseline_reproduction(rows, records, baseline)
    timing = {"model_load_seconds": load_seconds, "warmup_seconds": warm_seconds, "recognition_excludes_load_and_warmup": True}
    write_csv(args.output_dir / "ocr_results.csv", rows, RESULT_COLUMNS)
    write_csv(args.output_dir / "summary_metrics.csv", summaries, SUMMARY_COLUMNS)
    (args.output_dir / "native_outputs.json").write_text(json.dumps(native, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    complete = initial | {
        "status": "completed", "model_metadata": {"identifier": MODEL_NAME, "license": LICENSE, "documentation": MODEL_DOCUMENTATION, "module_documentation": MODULE_DOCUMENTATION},
        "runtime_versions": {"python": platform.python_version(), "paddleocr": package_version("paddleocr"), "paddlepaddle": package_version("paddlepaddle"), "opencv-python": package_version("opencv-python"), "numpy": package_version("numpy")},
        "timing": timing, "legacy_baseline_reproduction": reproduction,
        "summaries": summaries,
    }
    config_path.write_text(json.dumps(complete, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(args.output_dir / "EXTENDED_ARABIC_OCR_REPORT.md", rows, summaries, reproduction, timing)
    print(f"Extended Arabic OCR experiment written to: {args.output_dir.resolve()}")


def main() -> int:
    try:
        run(parse_args())
        return 0
    except ArabicOCRExperimentError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
