#!/usr/bin/env python3
"""Evaluate fixed DINOv2 plate representations without changing the encoder."""

from __future__ import annotations

import argparse
import csv
import importlib.metadata
import json
import platform
import sys
from pathlib import Path
from typing import Any, Sequence

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import torch

from compare_plate_embeddings import (
    DEFAULT_EMBEDDING_MODEL,
    PlateExperimentError,
    load_embedding_model,
    processor_settings,
    save_png,
    set_reproducible_seeds,
)
from evaluate_plate_dataset import summary
from plate_preprocessing import (
    INTERPOLATIONS,
    PreprocessingError,
    ResolutionPolicy,
    derive_patch_size,
    policy_record,
    prepare_variable_input,
    validate_policy,
    verify_rectangular_model_support,
)
from plate_representations import (
    MARKER_REPRESENTATION,
    REGION_NAMES,
    REGION_WEIGHTS,
    REPRESENTATIONS,
    cosine_matrix,
    forward_whole_representations,
    global_regional_matrix,
    normalized_cls_from_tensor,
    region_coordinates,
)
from plate_variants import (
    TIE_TOLERANCE,
    VariantExperimentError,
    classification_metrics,
    leave_one_out_predictions,
    validate_manifest,
)


DISPLAY_NAMES = {
    "r0_cls": "R0 whole-plate CLS",
    "r1_mean_patch": "R1 mean selected patches",
    "r2_global_regional": "R2 whole + corresponding regions",
    "r3_marker_cls": "R3 manual marker CLS",
}
FULL_PAIR_COLUMNS = (
    "pair_index", "image_1_id", "image_2_id", "category_1", "category_2",
    "image_1_path", "image_2_path", "same_category", "r0_cls_similarity",
    "r1_mean_patch_similarity", "r2_global_regional_similarity",
    "r1_delta_from_r0", "r2_delta_from_r0",
)
MARKER_PAIR_COLUMNS = FULL_PAIR_COLUMNS + (
    "r3_marker_cls_similarity", "r3_delta_from_r0",
)
PREDICTION_COLUMNS = (
    "evaluation_subset", "representation", "query_id", "query_category",
    "nearest_reference_id", "predicted_category", "nearest_similarity",
    "best_same_category_reference_id", "best_same_category_similarity",
    "best_different_category_reference_id", "best_different_category_similarity",
    "correct_category_margin", "predicted_category_margin", "correct", "tie_flag",
    "eligible_reference_count",
)
R0_EQUIVALENCE_TOLERANCE = 1e-6
MARKER_VISUAL_REVIEW = {
    "4": "The supplied box visibly contains both NT letters; no coordinate clipping is visible.",
    "5": (
        "The corrected supplied box is in bounds and visibly contains both NT marker "
        "characters; no coordinate clipping is visible."
    ),
    "6": (
        "The supplied box covers the small left-side Shorta marking, but the 23x17 crop "
        "is very low resolution and tightly framed; visual completeness is uncertain."
    ),
    "7": (
        "The supplied box covers the small left-side Shorta marking, but the 27x20 crop "
        "is very low resolution and tightly framed; visual completeness is uncertain."
    ),
    "8": "The supplied box visibly contains the complete Tunis text marker.",
    "9": "The supplied box visibly contains the complete Tunis text marker.",
    "10": "The supplied box visibly contains the complete Tunis text marker.",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare R0-R3 representations with frozen DINOv2-small."
    )
    parser.add_argument("--manifest", type=Path, default=Path("controlled_plate_manifest.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("plate_representation_evaluation"))
    parser.add_argument(
        "--prior-p0-results-dir",
        type=Path,
        default=Path("controlled_colour_gray_clahe_v1"),
        help="Prior controlled run used only to verify R0/P0-budget equivalence.",
    )
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--revision")
    parser.add_argument("--allow-download", action="store_true")
    parser.add_argument("--target-area", type=int, default=50_176)
    parser.add_argument("--max-long-edge", type=int, default=1_008)
    parser.add_argument("--max-patch-count", type=int, default=512)
    parser.add_argument("--padding-rgb", nargs=3, type=int, default=(124, 116, 104), metavar=("R", "G", "B"))
    parser.add_argument("--resize-interpolation", choices=tuple(sorted(INTERPOLATIONS)), default="bicubic")
    return parser.parse_args()


def dependency_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {"python": platform.python_version()}
    for name in ("torch", "transformers", "opencv-python", "numpy", "matplotlib"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def write_csv(path: Path, rows: Sequence[dict[str, Any]], columns: Sequence[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def matrix_pair_statistics(matrix: np.ndarray, records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    same, different = [], []
    for first in range(len(records)):
        for second in range(first + 1, len(records)):
            target = same if records[first]["category"] == records[second]["category"] else different
            target.append(float(matrix[first, second]))
    return {"same_category": summary(same), "different_category": summary(different)}


def pair_rows(
    records: Sequence[dict[str, Any]], matrices: dict[str, np.ndarray], include_r3: bool
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for first in range(len(records)):
        for second in range(first + 1, len(records)):
            r0 = float(matrices["r0_cls"][first, second])
            r1 = float(matrices["r1_mean_patch"][first, second])
            r2 = float(matrices["r2_global_regional"][first, second])
            row = {
                "pair_index": len(rows) + 1,
                "image_1_id": str(records[first]["id"]),
                "image_2_id": str(records[second]["id"]),
                "category_1": records[first]["category"],
                "category_2": records[second]["category"],
                "image_1_path": str(records[first]["path"]),
                "image_2_path": str(records[second]["path"]),
                "same_category": records[first]["category"] == records[second]["category"],
                "r0_cls_similarity": r0,
                "r1_mean_patch_similarity": r1,
                "r2_global_regional_similarity": r2,
                "r1_delta_from_r0": r1 - r0,
                "r2_delta_from_r0": r2 - r0,
            }
            if include_r3:
                r3 = float(matrices["r3_marker_cls"][first, second])
                row.update({"r3_marker_cls_similarity": r3, "r3_delta_from_r0": r3 - r0})
            rows.append(row)
    expected = len(records) * (len(records) - 1) // 2
    keys = {(row["image_1_id"], row["image_2_id"]) for row in rows}
    if len(rows) != expected or len(keys) != expected or any(a == b for a, b in keys):
        raise VariantExperimentError("Unordered-pair construction failed integrity checks.")
    return rows


def prediction_rows(
    subset: str,
    records: Sequence[dict[str, Any]],
    matrices: dict[str, np.ndarray],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    result: dict[str, Any] = {}
    categories = sorted({record["category"] for record in records})
    for representation, matrix in matrices.items():
        raw = leave_one_out_predictions(representation, matrix, records, TIE_TOLERANCE)
        converted = []
        for item in raw:
            converted.append(
                {
                    "evaluation_subset": subset,
                    "representation": item.pop("condition"),
                    **item,
                }
            )
        rows.extend(converted)
        result[representation] = {
            "classification": classification_metrics(converted, categories),
            "pair_statistics": matrix_pair_statistics(matrix, records),
        }
    return rows, result


def save_patch_overlay(path: Path, image: np.ndarray, selection: dict[str, Any]) -> None:
    overlay = image.copy()
    for patch in selection["patches"]:
        x1, y1, x2, y2 = patch["xyxy"]
        if not patch["selected"]:
            colour = (0, 0, 255)  # red: padding only
        elif patch["partial"]:
            colour = (0, 165, 255)  # orange: partially padded
        else:
            colour = (0, 180, 0)  # green: full content
        cv2.rectangle(overlay, (x1, y1), (x2 - 1, y2 - 1), colour, 1)
    cv2.rectangle(
        overlay,
        tuple(selection["content_rectangle_xyxy_in_padded_input"][:2]),
        tuple(value - 1 for value in selection["content_rectangle_xyxy_in_padded_input"][2:]),
        (255, 0, 255),
        2,
    )
    save_png(path, overlay)


def save_region_figure(path: Path, record: dict[str, Any], coordinates: dict[str, list[int]]) -> None:
    images = [record["image"]]
    titles = [f"whole {record['image'].shape[1]}×{record['image'].shape[0]}"]
    for name in REGION_NAMES:
        x1, y1, x2, y2 = coordinates[name]
        images.append(record["image"][y1:y2, x1:x2])
        titles.append(f"{name} [{x1},{y1},{x2},{y2}] | {x2-x1}×{y2-y1}")
    figure, axes = plt.subplots(1, 4, figsize=(18, 4), constrained_layout=True)
    for axis, image, title in zip(axes, images, titles):
        axis.imshow(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        axis.set_title(title, fontsize=9)
        axis.axis("off")
    figure.suptitle(f"Image {record['id']}: fixed original-crop regions")
    figure.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(figure)


def save_marker_figures(
    overlay_path: Path,
    gallery_path: Path,
    records: Sequence[dict[str, Any]],
    marker_crops: dict[str, np.ndarray],
) -> None:
    figure, axes = plt.subplots(len(records), 1, figsize=(15, 2.8 * len(records)), squeeze=False, constrained_layout=True)
    for axis, record in zip(axes[:, 0], records):
        shown = record["image"].copy()
        x1, y1, x2, y2 = record["marker_bbox"]
        cv2.rectangle(shown, (x1, y1), (x2 - 1, y2 - 1), (0, 255, 0), 2)
        axis.imshow(cv2.cvtColor(shown, cv2.COLOR_BGR2RGB))
        axis.set_title(f"{record['id']} {record['category']} | marker xyxy={record['marker_bbox']}")
        axis.axis("off")
    figure.suptitle("Supplied manual marker boxes — display overlays only")
    figure.savefig(overlay_path, dpi=160, bbox_inches="tight")
    plt.close(figure)

    figure, axes = plt.subplots(len(records), 1, figsize=(10, 2.4 * len(records)), squeeze=False, constrained_layout=True)
    for axis, record in zip(axes[:, 0], records):
        crop = marker_crops[str(record["id"])]
        axis.imshow(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
        axis.set_title(f"{record['id']} {record['category']} | complete supplied marker crop {crop.shape[1]}×{crop.shape[0]}")
        axis.axis("off")
    figure.suptitle("Extracted manual marker crops")
    figure.savefig(gallery_path, dpi=160, bbox_inches="tight")
    plt.close(figure)


def heatmaps(path: Path, matrices: dict[str, np.ndarray], labels: Sequence[str], title: str) -> None:
    figure, axes = plt.subplots(1, len(matrices), figsize=(6.5 * len(matrices), 6), squeeze=False, constrained_layout=True)
    minimum = min(float(matrix.min()) for matrix in matrices.values())
    for axis, (name, matrix) in zip(axes[0], matrices.items()):
        shown = axis.imshow(matrix, vmin=minimum, vmax=1.0, cmap="viridis")
        axis.set_title(DISPLAY_NAMES[name])
        axis.set_xticks(range(len(labels)), labels, rotation=45, ha="right")
        axis.set_yticks(range(len(labels)), labels)
        figure.colorbar(shown, ax=axis, fraction=0.046)
    figure.suptitle(title + " — common colour scale")
    figure.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(figure)


def confusion_figure(path: Path, evaluations: dict[str, Any], title: str) -> None:
    figure, axes = plt.subplots(1, len(evaluations), figsize=(5 * len(evaluations), 4.5), squeeze=False, constrained_layout=True)
    maximum = max(max(max(row) for row in value["classification"]["confusion_matrix"]) for value in evaluations.values())
    for axis, (name, value) in zip(axes[0], evaluations.items()):
        metrics = value["classification"]
        matrix = np.asarray(metrics["confusion_matrix"])
        axis.imshow(matrix, vmin=0, vmax=maximum, cmap="Blues")
        labels = metrics["categories"]
        axis.set_xticks(range(len(labels)), labels, rotation=40, ha="right")
        axis.set_yticks(range(len(labels)), labels)
        axis.set_xlabel("predicted")
        axis.set_ylabel("true")
        axis.set_title(DISPLAY_NAMES[name])
        for row in range(len(labels)):
            for column in range(len(labels)):
                axis.text(column, row, str(matrix[row, column]), ha="center", va="center")
    figure.suptitle(title)
    figure.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(figure)


def distribution_figure(
    path: Path, matrices: dict[str, np.ndarray], records: Sequence[dict[str, Any]], title: str
) -> None:
    figure, axes = plt.subplots(1, len(matrices), figsize=(4.5 * len(matrices), 4.5), squeeze=False, constrained_layout=True)
    for axis, (name, matrix) in zip(axes[0], matrices.items()):
        same, different = [], []
        for first in range(len(records)):
            for second in range(first + 1, len(records)):
                (same if records[first]["category"] == records[second]["category"] else different).append(float(matrix[first, second]))
        axis.boxplot([same, different], tick_labels=["same", "different"], showmeans=True)
        axis.set_title(DISPLAY_NAMES[name])
        axis.grid(axis="y", alpha=0.25)
    figure.suptitle(title + " — descriptive correlated pair scores")
    figure.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(figure)


def margin_figure(path: Path, rows: Sequence[dict[str, Any]], subset: str) -> None:
    filtered = [row for row in rows if row["evaluation_subset"] == subset]
    representations = list(dict.fromkeys(row["representation"] for row in filtered))
    query_ids = sorted({row["query_id"] for row in filtered}, key=int)
    x = np.arange(len(query_ids))
    width = 0.8 / len(representations)
    figure, axis = plt.subplots(figsize=(13, 5), constrained_layout=True)
    for number, representation in enumerate(representations):
        by_query = {row["query_id"]: row for row in filtered if row["representation"] == representation}
        values = [by_query[query]["correct_category_margin"] for query in query_ids]
        axis.bar(x - 0.4 + width / 2 + number * width, values, width, label=DISPLAY_NAMES[representation])
    axis.axhline(0, color="black", linewidth=1)
    axis.set_xticks(x, query_ids)
    axis.set_xlabel("query image ID")
    axis.set_ylabel("best same − best different cosine score")
    axis.set_title(f"Correct-category margins — {subset}")
    axis.legend(fontsize=8)
    figure.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(figure)


def prior_r0_equivalence(
    prior_dir: Path,
    current_matrix: np.ndarray,
    current_predictions: Sequence[dict[str, Any]],
    records: Sequence[dict[str, Any]],
    current_tensor_hashes: dict[str, str],
    manifest_hash: str,
    current_manifest_images: Sequence[dict[str, Any]],
    model_identifier: str,
    resolved_revision: str | None,
    policy: dict[str, Any],
) -> dict[str, Any]:
    required = [prior_dir / "all_pairs.csv", prior_dir / "leave_one_out_predictions.csv", prior_dir / "controlled_results.json"]
    if not all(path.is_file() for path in required):
        return {"available": False, "reason": f"Prior P0 artifacts are incomplete in {prior_dir}."}
    prior_json = json.loads(required[2].read_text(encoding="utf-8"))
    compatibility = {
        "manifest_hash_matches": prior_json["manifest"]["manifest_sha256"] == manifest_hash,
        "model_identifier_matches": prior_json["model"]["identifier"] == model_identifier,
        "resolved_revision_matches": prior_json["model"]["resolved_revision"] == resolved_revision,
        "budget_policy_matches": prior_json["budget_policy"]
        == json.loads(json.dumps(policy)),
    }
    prior_per_image = prior_json["conditions"]["p0_budget"]["per_image"]
    prior_hashes = {str(item["image_id"]): item["model_input"]["tensor_sha256"] for item in prior_per_image}
    compatibility["all_tensor_hashes_match"] = prior_hashes == current_tensor_hashes
    prior_source = {
        str(item["id"]): (item.get("sha256"), item.get("category"), item.get("decoded_dimensions_wh"))
        for item in prior_json["manifest"]["images"]
    }
    current_source = {
        str(item["id"]): (item.get("sha256"), item.get("category"), item.get("decoded_dimensions_wh"))
        for item in current_manifest_images
    }
    compatibility["source_hashes_categories_dimensions_match"] = prior_source == current_source
    required_compatibility = (
        "model_identifier_matches",
        "resolved_revision_matches",
        "budget_policy_matches",
        "all_tensor_hashes_match",
        "source_hashes_categories_dimensions_match",
    )
    if not all(compatibility[key] for key in required_compatibility):
        return {"available": True, "compatible": False, "compatibility": compatibility}
    size = len(records)
    prior_matrix = np.eye(size, dtype=np.float32)
    index = {str(record["id"]): number for number, record in enumerate(records)}
    with required[0].open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            first, second = index[row["image_1_id"]], index[row["image_2_id"]]
            prior_matrix[first, second] = prior_matrix[second, first] = float(row["p0_budget_similarity"])
    maximum_difference = float(np.max(np.abs(prior_matrix - current_matrix)))
    with required[1].open(newline="", encoding="utf-8") as handle:
        prior_predictions = {row["query_id"]: row for row in csv.DictReader(handle) if row["condition"] == "p0_budget"}
    current = {row["query_id"]: row for row in current_predictions}
    prediction_matches = all(
        prior_predictions[query]["nearest_reference_id"] == current[query]["nearest_reference_id"]
        and prior_predictions[query]["predicted_category"] == current[query]["predicted_category"]
        for query in current
    )
    equivalent = maximum_difference <= R0_EQUIVALENCE_TOLERANCE and prediction_matches
    if not equivalent:
        raise VariantExperimentError(
            "R0 failed prior P0-budget equivalence despite matching inputs/configuration: "
            f"max matrix difference={maximum_difference}, predictions_match={prediction_matches}."
        )
    return {
        "available": True,
        "compatible": True,
        "compatibility": compatibility,
        "tolerance": R0_EQUIVALENCE_TOLERANCE,
        "maximum_absolute_similarity_difference": maximum_difference,
        "nearest_neighbor_predictions_match": prediction_matches,
        "equivalent": equivalent,
    }


def prediction_changes(rows: Sequence[dict[str, Any]], subset: str, representation: str) -> dict[str, Any]:
    selected = [row for row in rows if row["evaluation_subset"] == subset]
    baseline = {row["query_id"]: row for row in selected if row["representation"] == "r0_cls"}
    current = {row["query_id"]: row for row in selected if row["representation"] == representation}
    improved, worsened, changed = [], [], []
    for query in sorted(baseline, key=int):
        before, after = baseline[query], current[query]
        if not before["correct"] and after["correct"]:
            improved.append(query)
        if before["correct"] and not after["correct"]:
            worsened.append(query)
        if before["predicted_category"] != after["predicted_category"]:
            changed.append(f"{query}: {before['predicted_category']} -> {after['predicted_category']}")
    nt = {
        query: {
            "r0_correct": baseline[query]["correct"],
            "new_correct": current[query]["correct"],
            "r0_prediction": baseline[query]["predicted_category"],
            "new_prediction": current[query]["predicted_category"],
        }
        for query in baseline
        if baseline[query]["query_category"] == "nt"
    }
    return {"improved": improved, "worsened": worsened, "changed": changed, "nt_queries": nt}


def report_table(lines: list[str], title: str, evaluation: dict[str, Any]) -> None:
    lines.extend([
        f"## {title}", "",
        "| Representation | Correct | Macro-F1 | Balanced accuracy | Same mean | Different mean |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for name, value in evaluation.items():
        metrics, pairs = value["classification"], value["pair_statistics"]
        lines.append(
            f"| {DISPLAY_NAMES[name]} | {metrics['correct']}/{metrics['total']} | "
            f"{metrics['macro_f1']:.3f} | {metrics['balanced_accuracy']:.3f} | "
            f"{pairs['same_category']['mean']:.6f} | {pairs['different_category']['mean']:.6f} |"
        )
    lines.append("")
    for name, value in evaluation.items():
        detail = ", ".join(
            f"{category}={item['correct']}/{item['total']} ({item['recall']:.3f})"
            for category, item in value["classification"]["per_category"].items()
        )
        lines.append(f"- {DISPLAY_NAMES[name]} per-category recall: {detail}")
    lines.append("")


def write_report(
    path: Path,
    full_evaluation: dict[str, Any],
    marker_evaluation: dict[str, Any],
    prediction_rows_all: Sequence[dict[str, Any]],
    equivalence: dict[str, Any],
) -> None:
    lines = [
        "# DINOv2 Plate Representation Experiment", "",
        "This experiment changes what is pooled or compared while keeping DINOv2-small, colour input, budgeted model-input preprocessing, float32 L2 normalization, and cosine comparison fixed.", "",
        "R0 and R1 share each whole-plate tensor and forward output. R2 uses the whole view plus three independently processed regional views and a fixed corresponding-region score. R3 uses supplied manual marker boxes and does not demonstrate automatic localization.", "",
        "## R0 reproducibility", "",
        f"- Equivalent to prior P0 budget: {equivalence.get('equivalent', False)}",
        f"- Maximum absolute similarity difference: {equivalence.get('maximum_absolute_similarity_difference', 'unavailable')}",
        f"- Nearest-neighbor predictions match: {equivalence.get('nearest_neighbor_predictions_match', 'unavailable')}", "",
    ]
    report_table(lines, "A. Full ten-image/four-category evaluation", full_evaluation)
    report_table(lines, "B. Matched seven-image/three-category marker subset", marker_evaluation)
    lines.extend(["## Manual marker visual review", ""])
    for image_id, note in MARKER_VISUAL_REVIEW.items():
        lines.append(f"- Image {image_id}: {note}")
    lines.append("")
    lines.extend(["## Prediction changes relative to R0", ""])
    for subset, representations in (("full_10", ("r1_mean_patch", "r2_global_regional")), ("marker_7", ("r1_mean_patch", "r2_global_regional", "r3_marker_cls"))):
        for representation in representations:
            changes = prediction_changes(prediction_rows_all, subset, representation)
            lines.append(
                f"- {subset}, {DISPLAY_NAMES[representation]}: improved={changes['improved'] or 'none'}; "
                f"worsened={changes['worsened'] or 'none'}; changed={changes['changed'] or 'none'}; "
                f"NT={changes['nt_queries']}."
            )
    lines.extend([
        "", "## Workload and limitations", "",
        "- R0 and R1 share 10 whole-plate forward passes. R1 adds pooling only.",
        "- R2 reuses each whole pass and adds 30 regional forward passes: four views per plate in total.",
        "- R3 adds seven marker-crop forward passes. All embeddings are cached before pair/query evaluation.",
        "- Patch overlays use green for full-content patches, orange for partially padded selected patches, red for padding-only excluded patches, and magenta for the unpadded content boundary.",
        "- No latency was measured. Different views and patch counts imply different computation.",
        "- Corresponding fixed regions may misalign category markings when their positions differ.",
        "- R3 is evaluated only on seven eligible images. Its result must not be compared with ten-image R0 as evidence of improvement.",
        "- These are ten development images; pair scores are correlated and physical independence is unverified where group identifiers are null. No generalization or calibrated confidence is claimed.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    if args.output_dir.exists():
        raise PlateExperimentError(f"Output directory already exists; refusing to overwrite it: {args.output_dir}")
    records, manifest_report = validate_manifest(args.manifest)
    if not manifest_report["valid"] or len(records) != 10 or not all(record.get("available") for record in records):
        raise PlateExperimentError("The controlled ten-image manifest is invalid: " + "; ".join(manifest_report["errors"]))
    marker_records = [record for record in records if record.get("marker_bbox") is not None]
    if len(marker_records) != 7 or {record["category"] for record in marker_records} != {"nt", "shorta", "tunis"}:
        raise PlateExperimentError("Marker subset must contain seven images across nt, shorta, and tunis.")
    policy = ResolutionPolicy(args.target_area, args.max_long_edge, args.max_patch_count, tuple(args.padding_rgb), args.resize_interpolation)
    try:
        validate_policy(policy)
    except PreprocessingError as exc:
        raise PlateExperimentError(str(exc)) from exc
    args.output_dir.mkdir(parents=True)
    exact_dir = args.output_dir / "exact_tensor_inputs"
    region_dir = args.output_dir / "regions"
    patch_dir = args.output_dir / "patch_selection"
    for name in ("whole", *REGION_NAMES, "marker"):
        (exact_dir / name).mkdir(parents=True)
    region_dir.mkdir()
    patch_dir.mkdir()

    set_reproducible_seeds()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    processor, model = load_embedding_model(args.embedding_model, device, revision=args.revision, local_files_only=not args.allow_download)
    model.eval()
    try:
        patch_size = derive_patch_size(model)
        if patch_size != (14, 14):
            raise PreprocessingError(f"Expected 14x14 patches, got {patch_size}.")
        rectangular = verify_rectangular_model_support(model, device, patch_size)
    except PreprocessingError as exc:
        raise PlateExperimentError(str(exc)) from exc

    r0_embeddings: list[torch.Tensor] = []
    r1_embeddings: list[torch.Tensor] = []
    regional_embeddings: dict[str, list[torch.Tensor]] = {name: [] for name in REGION_NAMES}
    marker_embeddings: list[torch.Tensor] = []
    per_image: list[dict[str, Any]] = []
    marker_crops: dict[str, np.ndarray] = {}
    current_tensor_hashes: dict[str, str] = {}
    try:
        for count, record in enumerate(records, start=1):
            image_id = str(record["id"])
            whole = prepare_variable_input(record["image"], processor, "budget", patch_size, policy)
            r0, r1, forward_metadata = forward_whole_representations(whole.pixel_values, model, device, whole.metadata, patch_size)
            r0_embeddings.append(r0)
            r1_embeddings.append(r1)
            current_tensor_hashes[image_id] = whole.metadata["tensor_sha256"]
            save_png(exact_dir / "whole" / f"{image_id}.png", whole.display_bgr)
            save_patch_overlay(patch_dir / f"{image_id}.png", whole.padded_bgr, forward_metadata["r1"]["patch_selection"])

            coordinates = region_coordinates(record["image"].shape[1], record["image"].shape[0])
            save_region_figure(region_dir / f"{image_id}.png", record, coordinates)
            region_metadata: dict[str, Any] = {}
            for name in REGION_NAMES:
                x1, y1, x2, y2 = coordinates[name]
                crop = record["image"][y1:y2, x1:x2].copy()
                prepared = prepare_variable_input(crop, processor, "budget", patch_size, policy)
                embedding, embedding_metadata = normalized_cls_from_tensor(prepared.pixel_values, model, device)
                regional_embeddings[name].append(embedding)
                save_png(exact_dir / name / f"{image_id}.png", prepared.display_bgr)
                region_metadata[name] = {"original_crop_xyxy": coordinates[name], "source_dimensions_wh": [x2-x1, y2-y1], "model_input": prepared.metadata, "embedding": embedding_metadata}

            marker_metadata = None
            if record.get("marker_bbox") is not None:
                x1, y1, x2, y2 = (int(value) for value in record["marker_bbox"])
                crop = record["image"][y1:y2, x1:x2].copy()
                marker_crops[image_id] = crop
                prepared = prepare_variable_input(crop, processor, "budget", patch_size, policy)
                embedding, embedding_metadata = normalized_cls_from_tensor(prepared.pixel_values, model, device)
                marker_embeddings.append(embedding)
                save_png(exact_dir / "marker" / f"{image_id}.png", prepared.display_bgr)
                marker_metadata = {"eligible": True, "supplied_xyxy": [x1, y1, x2, y2], "clipped_or_adjusted": False, "crop_dimensions_wh": [x2-x1, y2-y1], "visual_review": MARKER_VISUAL_REVIEW[image_id], "model_input": prepared.metadata, "embedding": embedding_metadata}
            else:
                marker_metadata = {"eligible": False, "reason": "No supplied marker annotation; excluded from R3."}
            per_image.append({"image_id": image_id, "whole_model_input": whole.metadata, "whole_forward": forward_metadata, "regions": region_metadata, "marker": marker_metadata})
            print(f"[{count}/10] cached whole/region representations for image {image_id}")
    except (PreprocessingError, VariantExperimentError) as exc:
        raise PlateExperimentError(str(exc)) from exc

    save_marker_figures(args.output_dir / "marker_overlays.png", args.output_dir / "marker_crops.png", marker_records, marker_crops)
    full_matrices = {"r0_cls": cosine_matrix(r0_embeddings), "r1_mean_patch": cosine_matrix(r1_embeddings)}
    r2_matrix, r2_metadata = global_regional_matrix({"whole": r0_embeddings, **regional_embeddings})
    full_matrices["r2_global_regional"] = r2_matrix
    marker_indices = [index for index, record in enumerate(records) if record.get("marker_bbox") is not None]
    marker_matrices = {name: matrix[np.ix_(marker_indices, marker_indices)] for name, matrix in full_matrices.items()}
    marker_matrices["r3_marker_cls"] = cosine_matrix(marker_embeddings)

    full_pairs = pair_rows(records, full_matrices, include_r3=False)
    marker_pairs = pair_rows(marker_records, marker_matrices, include_r3=True)
    if len(full_pairs) != 45 or len(marker_pairs) != 21:
        raise PlateExperimentError(f"Unexpected pair counts: full={len(full_pairs)}, marker={len(marker_pairs)}.")
    write_csv(args.output_dir / "all_pairs_full.csv", full_pairs, FULL_PAIR_COLUMNS)
    write_csv(args.output_dir / "all_pairs_marker_subset.csv", marker_pairs, MARKER_PAIR_COLUMNS)

    full_predictions, full_evaluation = prediction_rows("full_10", records, full_matrices)
    marker_predictions, marker_evaluation = prediction_rows("marker_7", marker_records, marker_matrices)
    predictions = full_predictions + marker_predictions
    write_csv(args.output_dir / "leave_one_out_predictions.csv", predictions, PREDICTION_COLUMNS)
    equivalence = prior_r0_equivalence(
        args.prior_p0_results_dir,
        full_matrices["r0_cls"],
        [row for row in full_predictions if row["representation"] == "r0_cls"],
        records,
        current_tensor_hashes,
        manifest_report["manifest_sha256"],
        manifest_report["images"],
        args.embedding_model,
        getattr(model.config, "_commit_hash", None),
        policy_record(policy),
    )

    labels_full = [str(record["id"]) for record in records]
    labels_marker = [str(record["id"]) for record in marker_records]
    heatmaps(args.output_dir / "similarity_heatmaps_full.png", full_matrices, labels_full, "Full ten-image evaluation")
    heatmaps(args.output_dir / "similarity_heatmaps_marker_subset.png", marker_matrices, labels_marker, "Matched seven-image marker subset")
    confusion_figure(args.output_dir / "confusion_matrices_full.png", full_evaluation, "Full ten-image confusion matrices")
    confusion_figure(args.output_dir / "confusion_matrices_marker_subset.png", marker_evaluation, "Matched marker-subset confusion matrices")
    distribution_figure(args.output_dir / "score_distributions_full.png", full_matrices, records, "Full ten-image evaluation")
    distribution_figure(args.output_dir / "score_distributions_marker_subset.png", marker_matrices, marker_records, "Matched marker-subset evaluation")
    margin_figure(args.output_dir / "margins_full.png", predictions, "full_10")
    margin_figure(args.output_dir / "margins_marker_subset.png", predictions, "marker_7")
    write_report(args.output_dir / "REPRESENTATION_EXPERIMENT_REPORT.md", full_evaluation, marker_evaluation, predictions, equivalence)

    result = {
        "schema_version": 1,
        "experiment": "fixed DINOv2-small representation comparison",
        "manifest": manifest_report,
        "model": {"identifier": args.embedding_model, "requested_revision": args.revision, "resolved_revision": getattr(model.config, "_commit_hash", None), "device": str(device), "eval_mode": not model.training, "precision": "float32 pooled representations; L2-normalized", "patch_size_hw": list(patch_size), "hidden_size": int(model.config.hidden_size), "rectangular_positional_interpolation": rectangular},
        "packages": dependency_versions(),
        "processor": processor_settings(processor),
        "budget_policy": policy_record(policy),
        "representation_rules": {
            "r0_cls": "Whole-plate CLS from the shared whole forward output.",
            "r1_mean_patch": "Uniform mean of spatial patch tokens intersecting unpadded resized content; padding-only excluded; partial included; then L2-normalized.",
            "r2_global_regional": r2_metadata,
            "r3_marker_cls": "CLS of exact supplied marker xyxy crop; seven eligible images only.",
        },
        "marker_visual_review": MARKER_VISUAL_REVIEW,
        "region_rounding": "Original-crop lower x bounds use floor; upper x bounds use ceil; all bounds clamped; full source height retained.",
        "evaluation_policy": {"classifier": "leave-one-out 1-nearest-neighbour", "similarity": "cosine; R2 is fixed weighted corresponding-component cosine", "group_exclusion": "exclude matching non-null physical_plate_id or group_id", "tie_tolerance": TIE_TOLERANCE, "tie_resolution": "smallest numerical reference ID while tie_flag remains true", "threshold": None},
        "workload": {"actual_cached_forward_passes": 47, "whole_passes_shared_by_r0_r1": 10, "additional_r2_region_passes": 30, "additional_r3_marker_passes": 7, "latency_measured": False},
        "r0_prior_equivalence": equivalence,
        "subsets": {"full_10": {"image_ids": labels_full, "pair_count": len(full_pairs), "representations": full_evaluation}, "marker_7": {"image_ids": labels_marker, "pair_count": len(marker_pairs), "representations": marker_evaluation}},
        "prediction_changes_relative_to_r0": {"full_10": {name: prediction_changes(predictions, "full_10", name) for name in ("r1_mean_patch", "r2_global_regional")}, "marker_7": {name: prediction_changes(predictions, "marker_7", name) for name in ("r1_mean_patch", "r2_global_regional", "r3_marker_cls")}},
        "per_image": per_image,
        "limitations": ["Ten development images; no generalization claim.", "Pair scores are correlated.", "Physical independence is unverified where IDs are null.", "R3 uses manual localization and cannot evaluate idara.", "No latency measurement."],
    }
    (args.output_dir / "representation_results.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Representation experiment written to: {args.output_dir.resolve()}")


def main() -> int:
    try:
        run(parse_args())
        return 0
    except (PlateExperimentError, VariantExperimentError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted by user.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
