"""Leave-one-reference-out reproduction and authoritative parity checks."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .classifier import PlateCategoryClassifier
from .config import PluginConfig
from .errors import PluginError
from .manifest import load_reference_manifest
from .utils import json_ready


def classification_summary(rows: list[dict[str, Any]], prediction_key: str) -> dict[str, Any]:
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
        "correct": correct,
        "total": len(rows),
        "accuracy": correct / len(rows),
        "per_category": per_category,
        "confusion_matrix": confusion,
    }


def run_leave_one_out(
    config: PluginConfig,
    output_dir: Path,
    authoritative_csv: Path | None = None,
    similarity_tolerance: float = 1e-6,
) -> dict[str, Any]:
    if output_dir.exists():
        raise PluginError(f"Refusing to overwrite evaluation output: {output_dir}")
    output_dir.mkdir(parents=True)
    records, _manifest = load_reference_manifest(config.reference_manifest)
    classifier = PlateCategoryClassifier(config, load_ocr=True)
    rows: list[dict[str, Any]] = []
    full_results = []
    for record in records:
        image_id = str(record["id"])
        result = classifier.classify(
            record["path"],
            output_dir / "per_image" / image_id,
            exclude_reference_ids=(image_id,),
            exclude_groups={
                "physical_plate_id": record.get("physical_plate_id"),
                "group_id": record.get("group_id"),
            },
            expected_category=record["category"],
        )
        full_results.append(result)
        rows.append(
            {
                "image_id": image_id,
                "expected_category": record["category"],
                "visual_category": result["visual"]["visual_category"],
                "nearest_reference_id": result["visual"]["nearest_reference_id"],
                "nearest_similarity": result["visual"]["nearest_similarity"],
                "correct_category_margin": result["visual"].get("correct_category_margin"),
                "raw_ocr": result["ocr"]["raw_text"],
                "native_ocr_confidence": result["ocr"]["native_confidence"],
                "nt_evidence": result["ocr"]["nt_evidence"],
                "override_applied": result["decision"]["override_applied"],
                "final_category": result["decision"]["final_category"],
                "visual_correct": result["evaluation"]["visual_correct"],
                "final_correct": result["evaluation"]["final_correct"],
            }
        )
    fieldnames = list(rows[0])
    with (output_dir / "leave_one_out_results.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    (output_dir / "leave_one_out_results.json").write_text(
        json.dumps(json_ready(full_results), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = {
        "visual": classification_summary(rows, "visual_category"),
        "final": classification_summary(rows, "final_category"),
        "overrides": [row["image_id"] for row in rows if row["override_applied"]],
        "self_reference_excluded": True,
        "parity": None,
    }
    if authoritative_csv is not None:
        with authoritative_csv.open(encoding="utf-8", newline="") as handle:
            authority = {row["image_id"]: row for row in csv.DictReader(handle)}
        differences = []
        for row in rows:
            expected = authority.get(row["image_id"])
            if expected is None:
                differences.append({"image_id": row["image_id"], "field": "row", "reason": "missing authoritative row"})
                continue
            checks = {
                "visual_category": (row["visual_category"], expected["r2_prediction"]),
                "nearest_reference_id": (row["nearest_reference_id"], expected["nearest_reference_id"]),
                "raw_ocr": (row["raw_ocr"], expected["raw_ocr"]),
                "nt_evidence": (row["nt_evidence"], expected["nt_evidence"]),
                "final_category": (row["final_category"], expected["final_prediction"]),
            }
            for field, (actual, wanted) in checks.items():
                if str(actual) != str(wanted):
                    differences.append({"image_id": row["image_id"], "field": field, "actual": actual, "expected": wanted})
            delta = abs(float(row["nearest_similarity"]) - float(expected["nearest_similarity"]))
            if delta > similarity_tolerance:
                differences.append({"image_id": row["image_id"], "field": "nearest_similarity", "delta": delta})
            margin_delta = abs(
                float(row["correct_category_margin"])
                - float(expected["correct_category_margin"])
            )
            if margin_delta > similarity_tolerance:
                differences.append(
                    {
                        "image_id": row["image_id"],
                        "field": "correct_category_margin",
                        "delta": margin_delta,
                    }
                )
            confidence_delta = abs(
                float(row["native_ocr_confidence"])
                - float(expected["native_ocr_confidence"])
            )
            if confidence_delta > similarity_tolerance:
                differences.append(
                    {
                        "image_id": row["image_id"],
                        "field": "native_ocr_confidence",
                        "delta": confidence_delta,
                    }
                )
            expected_override = expected["fusion_override"].strip().lower() == "true"
            if bool(row["override_applied"]) != expected_override:
                differences.append(
                    {
                        "image_id": row["image_id"],
                        "field": "override_applied",
                        "actual": row["override_applied"],
                        "expected": expected_override,
                    }
                )
        summary["parity"] = {
            "authoritative_csv": str(authoritative_csv.resolve()),
            "similarity_tolerance": similarity_tolerance,
            "passed": not differences,
            "differences": differences,
        }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary

