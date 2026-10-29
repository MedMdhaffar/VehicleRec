#!/usr/bin/env python3
"""Evaluate complete budget-resized plates with Arabic OCR and fixed NT fusion."""

from __future__ import annotations

import argparse
import csv
import importlib.metadata
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
    normalize_arabic_marker,
    parse_nt_evidence,
    sha256_file,
    unwrap_paddle_result,
)
from evaluate_arabic_marker_ocr import (
    LICENSE,
    MODEL_DOCUMENTATION,
    MODEL_NAME,
    MODULE_DOCUMENTATION,
    package_version,
    write_csv,
)
from plate_preprocessing import ResolutionPolicy, calculate_geometry, resize_and_pad
from plate_variants import validate_manifest


RESULT_COLUMNS = (
    "image_id", "expected_category", "source_path", "source_sha256",
    "submitted_path", "submitted_sha256", "submitted_width", "submitted_height",
    "budget_requested_scale", "budget_effective_scale_x", "budget_effective_scale_y",
    "budget_padding_ltrb_not_submitted", "raw_text", "normalized_text",
    "parser_boundary_text", "native_confidence", "nt_evidence", "parser_reason",
    "conflicting_categories", "inference_status", "failure_reason",
    "recognition_seconds", "r2_prediction", "r2_nearest_reference_id",
    "r2_nearest_similarity", "baseline_correct", "fusion_override",
    "fused_prediction", "fused_correct", "fusion_outcome",
)

PARSER_POLICY = {
    "fixed_before_inference": True,
    "unicode": "NFKC; remove Unicode Mn marks and U+0640 tatweel",
    "saved_normalized_text": "Existing normalize_arabic_marker policy also removes all whitespace",
    "nt_match": "Arabic letter nun, optional whitespace, Arabic letter ta: ن\\s*ت",
    "external_boundary": (
        "The character immediately before nun and after ta must not be an Arabic base "
        "letter. String boundaries, digits, punctuation, and separators are allowed."
    ),
    "embedded_word_policy": "Do not accept NT inside a longer Arabic word.",
    "conflict_policy": (
        "Explicit boundary-valid شرطة or تونس evidence yields conflicting, even if NT "
        "also appears; fusion retains the visual baseline."
    ),
    "absent_semantics": "NT evidence absent does not mean definitely not NT.",
    "forbidden": [
        "fuzzy matching", "expected-label repair", "filename cues",
        "registration-ground-truth repair", "confidence threshold tuning",
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("all_plate_manifest.json"))
    parser.add_argument("--visual-baseline-dir", type=Path, default=Path("dinov3_s_all12_budget_v1"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", default="cpu")
    return parser.parse_args()


def load_visual_baseline(path: Path, records: Sequence[dict[str, Any]]) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
    result_path = path / "representation_results.json"
    prediction_path = path / "leave_one_out_predictions.csv"
    if not result_path.is_file() or not prediction_path.is_file():
        raise ArabicOCRExperimentError(f"Incomplete visual baseline directory: {path}")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    model = result.get("model", {})
    if model.get("encoder") != "dinov3_vits16" or result.get("input_mode") != "budget":
        raise ArabicOCRExperimentError("Visual baseline is not DINOv3 ViT-S/16 budget mode")
    manifest_images = result.get("manifest", {}).get("images", [])
    baseline_hashes = {str(item["id"]): item.get("sha256") for item in manifest_images}
    current_hashes = {str(item["id"]): item.get("sha256") for item in records}
    if baseline_hashes != current_hashes:
        raise ArabicOCRExperimentError("Visual baseline source hashes do not match the current manifest")
    with prediction_path.open(newline="", encoding="utf-8") as handle:
        selected = [
            row for row in csv.DictReader(handle)
            if row["evaluation_subset"] == f"full_{len(records)}"
            and row["representation"] == "r2_global_regional"
        ]
    if len(selected) != len(records):
        raise ArabicOCRExperimentError("Visual baseline lacks one R2 prediction per current image")
    indexed = {row["query_id"]: row for row in selected}
    correct = sum(row["correct"].lower() == "true" for row in selected)
    per_category = {
        category: [
            sum(row["correct"].lower() == "true" for row in selected if row["query_category"] == category),
            sum(row["query_category"] == category for row in selected),
        ]
        for category in sorted({row["query_category"] for row in selected})
    }
    if sum(total for _correct, total in per_category.values()) != len(records):
        raise ArabicOCRExperimentError("Saved R2 baseline category totals are inconsistent")
    return indexed, result


def save_contact_sheet(path: Path, records: Sequence[dict[str, Any]]) -> None:
    figure, axes = plt.subplots(6, 2, figsize=(14, 18), squeeze=False, constrained_layout=True)
    for axis, record in zip(axes.flat, records):
        image = record["submitted"]
        axis.imshow(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        axis.set_title(
            f"Image {record['id']} | {record['category']} | "
            f"submitted {image.shape[1]}×{image.shape[0]}", fontsize=10
        )
        axis.axis("off")
    figure.suptitle(
        "Complete unpadded budget-resized crops submitted to Arabic recognition\n"
        "Display scaling only; inference uses the saved PNG dimensions",
        fontsize=14,
    )
    figure.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(figure)


def classification_summary(rows: Sequence[dict[str, Any]], prediction_key: str) -> dict[str, Any]:
    categories = sorted({row["expected_category"] for row in rows})
    confusion = {actual: {predicted: 0 for predicted in categories} for actual in categories}
    for row in rows:
        confusion[row["expected_category"]][row[prediction_key]] += 1
    per_category = {}
    for category in categories:
        total = sum(row["expected_category"] == category for row in rows)
        correct = sum(
            row["expected_category"] == category and row[prediction_key] == category
            for row in rows
        )
        per_category[category] = {"correct": correct, "total": total, "recall": correct / total}
    correct = sum(row[prediction_key] == row["expected_category"] for row in rows)
    return {
        "correct": correct, "total": len(rows), "accuracy": correct / len(rows),
        "per_category": per_category, "confusion_matrix": confusion,
    }


def save_confusion_matrices(path: Path, summary: dict[str, Any]) -> None:
    categories = ["idara", "nt", "shorta", "tunis"]
    figure, axes = plt.subplots(1, 2, figsize=(10, 4.5), constrained_layout=True)
    for axis, key, title in zip(axes, ("baseline", "fused"), ("Saved DINOv3 R2", "Fixed OCR fusion")):
        mapping = summary[key]["confusion_matrix"]
        matrix = [[mapping[actual][predicted] for predicted in categories] for actual in categories]
        axis.imshow(matrix, vmin=0, vmax=3, cmap="Blues")
        axis.set_xticks(range(4), categories, rotation=35, ha="right")
        axis.set_yticks(range(4), categories)
        axis.set_xlabel("predicted"); axis.set_ylabel("expected"); axis.set_title(title)
        for row in range(4):
            for column in range(4):
                axis.text(column, row, str(matrix[row][column]), ha="center", va="center")
    figure.suptitle("Complete-plate experiment confusion matrices")
    figure.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(figure)


def write_report(path: Path, rows: Sequence[dict[str, Any]], summary: dict[str, Any], timing: dict[str, Any]) -> None:
    ocr = summary["nt_detection"]
    baseline, fused = summary["baseline"], summary["fused"]
    lines = [
        "# Complete-Plate Arabic OCR and NT Fusion", "",
        "All 12 complete plate crops were proportionally budget-resized using the saved DINOv3-S policy. OCR receives the unpadded resized crop; no marker crop, detector, thresholding, edge filter, or extra variant is used.", "",
        "## Results", "",
        f"- NT detection: TP={ocr['true_positive']}, FP={ocr['false_positive']}, FN={ocr['false_negative']}, TN={ocr['true_negative']}.",
        f"- NT precision: {ocr['precision'] if ocr['precision'] is not None else 'undefined'}; recall: {ocr['recall'] if ocr['recall'] is not None else 'undefined'}.",
        f"- Visual baseline: {baseline['correct']}/{baseline['total']} ({baseline['accuracy']:.3f}).",
        f"- Fixed fusion: {fused['correct']}/{fused['total']} ({fused['accuracy']:.3f}).",
        f"- Corrections: {summary['corrections'] or 'none'}; regressions: {summary['regressions'] or 'none'}; false NT overrides: {summary['false_nt_overrides'] or 'none'}.",
        f"- Conflicts: {summary['conflicts'] or 'none'}; inference failures: {summary['inference_failures'] or 'none'}.", "",
        "## Per-image outcomes", "",
        "| Image | Expected | R2 prediction | Raw Arabic OCR | NT evidence | Fused prediction | Correct |", "|---:|---|---|---|---|---|---:|",
    ]
    for row in rows:
        raw = str(row["raw_text"]).replace("|", "\\|")
        lines.append(
            f"| {row['image_id']} | {row['expected_category']} | {row['r2_prediction']} | "
            f"`{raw}` | {row['nt_evidence']} | {row['fused_prediction']} | {row['fused_correct']} |"
        )
    lines += ["", "## Timing", "", f"- Model load: {timing['model_load_seconds']:.3f} s.", f"- Warm-up: {timing['warmup_seconds']:.3f} s.", f"- Recognition total: {timing['recognition_total_seconds']:.3f} s; mean completed image: {timing['mean_completed_seconds']:.4f} s.", "", "## Interpretation", "", "OCR confidence is a native line-recognition score, not a calibrated plate-category probability. NT evidence absent does not mean definitely not NT. The fusion policy was fixed before examining these outputs and is not integrated into production.", "", "These 12 repeatedly used development images cannot establish generalization. Any apparent gain requires physically independent validation plates.", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    if args.output_dir.exists():
        raise ArabicOCRExperimentError(f"Output directory already exists; refusing overwrite: {args.output_dir}")
    records, manifest_report = validate_manifest(args.manifest)
    if not manifest_report["valid"] or len(records) != 12 or not all(record["available"] for record in records):
        raise ArabicOCRExperimentError("The all-plate manifest must contain 12 valid available images: " + "; ".join(manifest_report["errors"]))
    expected_categories = {str(i): "idara" for i in range(1, 4)} | {str(i): "nt" for i in range(4, 8)} | {str(i): "shorta" for i in range(8, 10)} | {str(i): "tunis" for i in range(10, 13)}
    if {str(record["id"]): record["category"] for record in records} != expected_categories:
        raise ArabicOCRExperimentError("Current manifest IDs/categories do not match the authoritative 1–12 mapping")
    baseline, baseline_json = load_visual_baseline(args.visual_baseline_dir, records)
    policy_data = baseline_json["budget_policy"]
    policy = ResolutionPolicy(
        int(policy_data["target_area"]), int(policy_data["max_long_edge"]),
        int(policy_data["max_patch_count"]), tuple(policy_data["padding_rgb"]),
        str(policy_data["interpolation"]),
    )
    patch_size = tuple(int(value) for value in baseline_json["model"]["patch_size_hw"])
    if patch_size != (16, 16):
        raise ArabicOCRExperimentError(f"Expected DINOv3-S patch size 16, got {patch_size}")

    args.output_dir.mkdir(parents=True)
    crop_dir = args.output_dir / "submitted_budget_crops"
    crop_dir.mkdir()
    prepared_records = []
    for record in records:
        geometry = calculate_geometry(record["image"].shape, "budget", patch_size, policy)
        resized, _padded = resize_and_pad(record["image"], geometry, policy)
        path = crop_dir / f"{record['id']}_budget_unpadded.png"
        if not cv2.imwrite(str(path), resized):
            raise ArabicOCRExperimentError(f"Could not save OCR input: {path}")
        prepared_records.append(record | {"submitted": resized, "submitted_path": path, "budget_geometry": geometry})
    save_contact_sheet(args.output_dir / "submitted_budget_crops_contact_sheet.png", prepared_records)
    (args.output_dir / "nt_parser_policy.json").write_text(json.dumps(PARSER_POLICY, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    initial_config = {
        "schema_version": 1, "status": "running", "manifest": str(args.manifest),
        "manifest_sha256": sha256_file(args.manifest), "manifest_validation": manifest_report,
        "visual_baseline_dir": str(args.visual_baseline_dir),
        "visual_baseline_results_sha256": sha256_file(args.visual_baseline_dir / "representation_results.json"),
        "external_budget_resize": policy_data,
        "external_resize_patch_alignment_reference": list(patch_size),
        "ocr_submitted_stage": "resized unpadded complete plate; padding is not submitted",
        "ocr_model": MODEL_NAME, "ocr_device": args.device,
        "ocr_internal_preprocessing": "Official TextRecognition defaults; internal postprocessed tensor dimensions unavailable from public Result API",
        "detection_bypassed": True, "manual_marker_crops": False,
        "parser_policy": PARSER_POLICY,
    }
    config_path = args.output_dir / "experiment_configuration.json"
    config_path.write_text(json.dumps(initial_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    try:
        from paddleocr import TextRecognition
    except Exception as exc:
        raise ArabicOCRExperimentError(f"PaddleOCR unavailable: {type(exc).__name__}: {exc}") from exc
    started = time.perf_counter(); model = TextRecognition(model_name=MODEL_NAME, device=args.device)
    load_seconds = time.perf_counter() - started
    started = time.perf_counter(); warm = list(model.predict(input=str(prepared_records[0]["submitted_path"]), batch_size=1))
    warm_seconds = time.perf_counter() - started
    if len(warm) != 1:
        raise ArabicOCRExperimentError("Warm-up did not return exactly one result")

    rows, native_outputs = [], []
    for record in prepared_records:
        image_id = str(record["id"]); visual = baseline[image_id]
        raw_text, confidence, parser, payload = "", None, None, None
        failure, elapsed = "", 0.0
        try:
            started = time.perf_counter(); outputs = list(model.predict(input=str(record["submitted_path"]), batch_size=1)); elapsed = time.perf_counter() - started
            if len(outputs) != 1:
                raise ArabicOCRExperimentError(f"Expected one result, got {len(outputs)}")
            raw_text, confidence, payload = unwrap_paddle_result(outputs[0])
            parser = parse_nt_evidence(raw_text)
            status = "completed"
        except Exception as exc:
            status = "failed"; failure = f"{type(exc).__name__}: {exc}"
        baseline_prediction = visual["predicted_category"]
        if status == "completed" and parser["decision"] == "present":
            fused_prediction, override = "nt", baseline_prediction != "nt"
        else:
            fused_prediction, override = baseline_prediction, False
        baseline_correct = baseline_prediction == record["category"]
        fused_correct = fused_prediction == record["category"]
        if not baseline_correct and fused_correct:
            outcome = "correction"
        elif baseline_correct and not fused_correct:
            outcome = "regression"
        elif override:
            outcome = "changed_without_correctness_change"
        else:
            outcome = "unchanged"
        geometry = record["budget_geometry"]
        row = {
            "image_id": image_id, "expected_category": record["category"],
            "source_path": str(record["path"]), "source_sha256": record["sha256"],
            "submitted_path": str(record["submitted_path"]), "submitted_sha256": sha256_file(record["submitted_path"]),
            "submitted_width": record["submitted"].shape[1], "submitted_height": record["submitted"].shape[0],
            "budget_requested_scale": geometry["requested_scale"],
            "budget_effective_scale_x": geometry["effective_scale_xy"][0], "budget_effective_scale_y": geometry["effective_scale_xy"][1],
            "budget_padding_ltrb_not_submitted": json.dumps(geometry["padding_ltrb"]),
            "raw_text": raw_text, "normalized_text": "" if parser is None else parser["normalized_text"],
            "parser_boundary_text": "" if parser is None else parser["boundary_text"],
            "native_confidence": "" if confidence is None else confidence,
            "nt_evidence": "unavailable" if parser is None else parser["decision"],
            "parser_reason": "inference_failed" if parser is None else parser["reason"],
            "conflicting_categories": "" if parser is None else ";".join(parser["conflicting_categories"]),
            "inference_status": status, "failure_reason": failure, "recognition_seconds": elapsed,
            "r2_prediction": baseline_prediction, "r2_nearest_reference_id": visual["nearest_reference_id"],
            "r2_nearest_similarity": visual["nearest_similarity"], "baseline_correct": baseline_correct,
            "fusion_override": override, "fused_prediction": fused_prediction,
            "fused_correct": fused_correct, "fusion_outcome": outcome,
        }
        rows.append(row)
        native_outputs.append({"image_id": image_id, "status": status, "payload": payload, "failure_reason": failure})

    completed = [row for row in rows if row["inference_status"] == "completed"]
    tp = sum(row["expected_category"] == "nt" and row["nt_evidence"] == "present" for row in completed)
    fp = sum(row["expected_category"] != "nt" and row["nt_evidence"] == "present" for row in completed)
    fn = sum(row["expected_category"] == "nt" and row["nt_evidence"] != "present" for row in completed)
    tn = sum(row["expected_category"] != "nt" and row["nt_evidence"] != "present" for row in completed)
    summary = {
        "nt_detection": {"completed": len(completed), "failed": len(rows) - len(completed), "true_positive": tp, "false_positive": fp, "false_negative": fn, "true_negative": tn, "precision": None if tp + fp == 0 else tp / (tp + fp), "recall": None if tp + fn == 0 else tp / (tp + fn)},
        "baseline": classification_summary(rows, "r2_prediction"),
        "fused": classification_summary(rows, "fused_prediction"),
        "corrections": [row["image_id"] for row in rows if row["fusion_outcome"] == "correction"],
        "regressions": [row["image_id"] for row in rows if row["fusion_outcome"] == "regression"],
        "false_nt_overrides": [row["image_id"] for row in rows if row["fusion_override"] and row["expected_category"] != "nt"],
        "conflicts": [row["image_id"] for row in rows if row["nt_evidence"] == "conflicting"],
        "inference_failures": [row["image_id"] for row in rows if row["inference_status"] != "completed"],
    }
    completed_times = [float(row["recognition_seconds"]) for row in completed]
    timing = {"model_load_seconds": load_seconds, "warmup_seconds": warm_seconds, "recognition_total_seconds": sum(completed_times), "mean_completed_seconds": statistics.mean(completed_times) if completed_times else None, "median_completed_seconds": statistics.median(completed_times) if completed_times else None, "load_and_warmup_excluded_from_per_image": True}
    write_csv(args.output_dir / "complete_plate_ocr_fusion.csv", rows, RESULT_COLUMNS)
    (args.output_dir / "complete_plate_ocr_fusion.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "native_ocr_outputs.json").write_text(json.dumps(native_outputs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "summary_metrics.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    save_confusion_matrices(args.output_dir / "confusion_matrices.png", summary)
    flat_summary = [{"metric": key, "value": json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value} for key, value in summary.items()]
    write_csv(args.output_dir / "summary_metrics.csv", flat_summary, ("metric", "value"))
    final_config = initial_config | {"status": "completed", "model_metadata": {"identifier": MODEL_NAME, "license": LICENSE, "documentation": MODEL_DOCUMENTATION, "module_documentation": MODULE_DOCUMENTATION}, "runtime_versions": {"python": platform.python_version(), "paddleocr": package_version("paddleocr"), "paddlepaddle": package_version("paddlepaddle"), "opencv-python": package_version("opencv-python"), "numpy": package_version("numpy")}, "timing": timing, "summary": summary}
    config_path.write_text(json.dumps(final_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(args.output_dir / "COMPLETE_PLATE_NT_OCR_REPORT.md", rows, summary, timing)
    print(f"Complete-plate NT OCR experiment written to: {args.output_dir.resolve()}")


def main() -> int:
    try:
        run(parse_args()); return 0
    except ArabicOCRExperimentError as exc:
        print(f"Error: {exc}", file=sys.stderr); return 1
    except KeyboardInterrupt:
        print("Interrupted by user.", file=sys.stderr); return 130


if __name__ == "__main__":
    raise SystemExit(main())
