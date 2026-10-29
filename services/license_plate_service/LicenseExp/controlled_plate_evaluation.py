"""Controlled P0-P4 plate-preparation experiment used by evaluate_plate_dataset.py."""

from __future__ import annotations

import csv
import importlib.metadata
import json
import platform
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import torch

from compare_plate_embeddings import (
    PlateExperimentError,
    load_embedding_model,
    processor_settings,
    save_png,
    set_reproducible_seeds,
)
from evaluate_plate_dataset import similarity_matrix, summary
from plate_preprocessing import (
    PreprocessingError,
    ResolutionPolicy,
    add_original_patch_metadata,
    derive_patch_size,
    extract_embedding_from_tensor,
    policy_record,
    prepare_original_input,
    prepare_variable_input,
    validate_policy,
    verify_rectangular_model_support,
)
from plate_variants import (
    CONTROLLED_VARIANTS as VARIANTS,
    TIE_TOLERANCE,
    VariantExperimentError,
    classification_metrics,
    leave_one_out_predictions,
    prepare_controlled_variants as prepare_variants,
    validate_manifest,
)


HISTORICAL = ("original", "native", "budget")
PAIR_LEGACY_COLUMNS = (
    "pair_index", "image_1_id", "image_2_id", "category_1", "category_2",
    "image_1_path", "image_2_path", "same_category", "original_similarity",
    "native_similarity", "budget_similarity", "native_delta_from_original",
    "budget_delta_from_original",
)
PAIR_EXPERIMENT_COLUMNS = (
    "p0_budget_similarity", "grayscale_budget_similarity",
    "clahe_budget_similarity", "grayscale_delta_from_p0",
    "clahe_delta_from_p0",
)
PREDICTION_COLUMNS = (
    "condition", "query_id", "query_category", "nearest_reference_id",
    "predicted_category", "nearest_similarity", "best_same_category_reference_id",
    "best_same_category_similarity", "best_different_category_reference_id",
    "best_different_category_similarity", "correct_category_margin",
    "predicted_category_margin", "correct", "tie_flag", "eligible_reference_count",
)


def dependency_versions() -> dict[str, str | None]:
    result: dict[str, str | None] = {"python": platform.python_version()}
    for distribution in ("torch", "transformers", "opencv-python", "numpy", "matplotlib"):
        try:
            result[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            result[distribution] = None
    return result


def serializable_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: (str(value) if isinstance(value, Path) else value) for key, value in record.items() if key != "image"}


def write_validation_output(
    output_dir: Path,
    manifest_report: dict[str, Any],
) -> None:
    payload = {"manifest": manifest_report}
    (output_dir / "validation.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    lines = ["# Controlled Experiment Validation", ""]
    for name, report in payload.items():
        lines.extend([f"## {name.title()}", ""])
        errors = report.get("errors", [])
        lines.append(f"- Status: {'valid' if not errors else 'blocked'}")
        if errors:
            lines.extend(f"- {error}" for error in errors)
        lines.append("")
    lines.extend([
        "The numbered images are treated as complete plate crops; no geometry annotation is used.",
        "Registration text is retained as metadata only and is never supplied to the model.",
    ])
    (output_dir / "VALIDATION_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: Sequence[dict[str, Any]], columns: Sequence[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def score_descriptions(matrix: np.ndarray, records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    same, different = [], []
    for first in range(len(records)):
        for second in range(first + 1, len(records)):
            target = same if records[first]["category"] == records[second]["category"] else different
            target.append(float(matrix[first, second]))
    return {"same_category": summary(same), "different_category": summary(different)}


def build_pair_rows(
    matrices: dict[str, np.ndarray], records: Sequence[dict[str, Any]], full: bool
) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for first in range(len(records)):
        for second in range(first + 1, len(records)):
            original = float(matrices["original"][first, second])
            native = float(matrices["native"][first, second])
            budget = float(matrices["budget"][first, second])
            row: dict[str, Any] = {
                "pair_index": len(pairs) + 1,
                "image_1_id": records[first]["id"],
                "image_2_id": records[second]["id"],
                "category_1": records[first]["category"],
                "category_2": records[second]["category"],
                "image_1_path": str(records[first]["path"]),
                "image_2_path": str(records[second]["path"]),
                "same_category": records[first]["category"] == records[second]["category"],
                "original_similarity": original,
                "native_similarity": native,
                "budget_similarity": budget,
                "native_delta_from_original": native - original,
                "budget_delta_from_original": budget - original,
            }
            if full:
                scores = {variant: float(matrices[f"{variant}_budget"][first, second]) for variant in VARIANTS}
                if not np.isclose(budget, scores["p0"], rtol=0, atol=1e-7):
                    raise VariantExperimentError("Legacy budget and P0 budget similarities differ.")
                row.update({
                    **{f"{variant}_budget_similarity": score for variant, score in scores.items()},
                    "grayscale_delta_from_p0": scores["grayscale"] - scores["p0"],
                    "clahe_delta_from_p0": scores["clahe"] - scores["p0"],
                })
            pairs.append(row)
    expected = len(records) * (len(records) - 1) // 2
    if len(pairs) != expected or len({(row["image_1_id"], row["image_2_id"]) for row in pairs}) != expected:
        raise VariantExperimentError("Pair construction produced missing or duplicated unordered pairs.")
    return pairs


def create_heatmaps(path: Path, matrices: dict[str, np.ndarray], labels: Sequence[str]) -> None:
    columns = 3
    rows = int(np.ceil(len(matrices) / columns))
    figure, axes = plt.subplots(rows, columns, figsize=(7 * columns, 6 * rows), squeeze=False, constrained_layout=True)
    minimum = min(float(matrix.min()) for matrix in matrices.values())
    for axis, (condition, matrix) in zip(axes.flat, matrices.items()):
        shown = axis.imshow(matrix, vmin=minimum, vmax=1.0, cmap="viridis")
        axis.set_title(condition)
        axis.set_xticks(range(len(labels)), labels, rotation=45, ha="right")
        axis.set_yticks(range(len(labels)), labels)
        figure.colorbar(shown, ax=axis, fraction=0.046)
    for axis in list(axes.flat)[len(matrices):]:
        axis.axis("off")
    figure.suptitle("Cosine similarity — common colour scale", fontsize=16)
    figure.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(figure)


def create_distributions(path: Path, descriptions: dict[str, dict[str, Any]], matrices: dict[str, np.ndarray], records: Sequence[dict[str, Any]]) -> None:
    figure, axes = plt.subplots(1, len(matrices), figsize=(4.3 * len(matrices), 4.5), constrained_layout=True)
    axes = np.atleast_1d(axes)
    for axis, (condition, matrix) in zip(axes, matrices.items()):
        same, different = [], []
        for first in range(len(records)):
            for second in range(first + 1, len(records)):
                (same if records[first]["category"] == records[second]["category"] else different).append(float(matrix[first, second]))
        axis.boxplot([same, different], tick_labels=["same", "different"], showmeans=True)
        axis.set_title(condition)
        axis.grid(axis="y", alpha=0.25)
    figure.suptitle("Correlated pair scores: descriptive only")
    figure.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(figure)


def create_stage_figure(
    path: Path,
    record: dict[str, Any],
    variants: dict[str, Any],
    prepared: dict[str, Any],
) -> None:
    figure, axes = plt.subplots(2, 3, figsize=(15, 8), constrained_layout=True)
    for column, variant in enumerate(VARIANTS):
        top = variants[variant].image_bgr
        bottom = prepared[f"{variant}_budget"].display_bgr
        for axis, image, prefix in ((axes[0, column], top, "prepared"), (axes[1, column], bottom, "exact tensor display")):
            axis.imshow(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
            axis.set_title(f"{variant.upper()} {prefix}\n{image.shape[1]}×{image.shape[0]}")
            axis.axis("off")
    figure.suptitle(
        f"Image {record['id']} | complete crop retained in every preparation variant\n"
        "Tensor displays reverse exact normalization/rescaling and use documented uint8 clipping/rounding"
    )
    figure.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(figure)


def create_variant_gallery(path: Path, records: Sequence[dict[str, Any]], variants_by_image: Sequence[dict[str, Any]]) -> None:
    figure, axes = plt.subplots(len(records), 3, figsize=(15, 2.8 * len(records)), constrained_layout=True)
    for row, (record, variants) in enumerate(zip(records, variants_by_image)):
        for column, variant in enumerate(VARIANTS):
            image = variants[variant].image_bgr
            axes[row, column].imshow(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
            axes[row, column].set_title(f"{record['id']} {variant.upper()} | {image.shape[1]}×{image.shape[0]}", fontsize=9)
            axes[row, column].axis("off")
    figure.suptitle("Colour, replicated grayscale, and Lab-luminance CLAHE before shared budget preprocessing")
    figure.savefig(path, dpi=140, bbox_inches="tight")
    plt.close(figure)


def create_summary(path: Path, metrics: dict[str, dict[str, Any]]) -> None:
    labels = list(metrics)
    accuracy = [metrics[label]["accuracy"] for label in labels]
    macro_f1 = [metrics[label]["macro_f1"] for label in labels]
    balanced = [metrics[label]["balanced_accuracy"] for label in labels]
    x = np.arange(len(labels))
    figure, axis = plt.subplots(figsize=(12, 5), constrained_layout=True)
    axis.bar(x - 0.25, accuracy, 0.25, label="LOO accuracy")
    axis.bar(x, macro_f1, 0.25, label="macro-F1")
    axis.bar(x + 0.25, balanced, 0.25, label="balanced accuracy")
    axis.set_xticks(x, labels, rotation=30, ha="right")
    axis.set_ylim(0, 1)
    axis.legend()
    axis.grid(axis="y", alpha=0.25)
    axis.set_title("Exploratory leave-one-out category evaluation")
    figure.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(figure)


def prediction_changes(predictions: Sequence[dict[str, Any]], first: str, second: str) -> dict[str, list[str]]:
    by_condition = {(row["condition"], row["query_id"]): row for row in predictions}
    improved, worsened, changed = [], [], []
    query_ids = sorted({row["query_id"] for row in predictions}, key=int)
    for query_id in query_ids:
        before, after = by_condition[(first, query_id)], by_condition[(second, query_id)]
        if not before["correct"] and after["correct"]:
            improved.append(query_id)
        if before["correct"] and not after["correct"]:
            worsened.append(query_id)
        if before["predicted_category"] != after["predicted_category"]:
            changed.append(f"{query_id}: {before['predicted_category']} -> {after['predicted_category']}")
    return {"improved": improved, "worsened": worsened, "prediction_changes": changed}


def write_report(
    path: Path,
    metrics: dict[str, dict[str, Any]],
    descriptions: dict[str, dict[str, Any]],
    predictions: Sequence[dict[str, Any]],
    full: bool,
) -> None:
    lines = [
        "# Controlled Plate-Preparation Evaluation", "",
        "Encoder, CLS extraction, float32 L2 normalization, cosine similarity, and model-input policy are fixed. Colour/grayscale/CLAHE describe image preparation; original/native/budget are historical model-input modes computed on unchanged colour P0.", "",
        "## Classification summary", "",
        "| Condition | Correct | Macro-F1 | Balanced accuracy | Same mean | Different mean |", "|---|---:|---:|---:|---:|---:|",
    ]
    for condition in metrics:
        item, scores = metrics[condition], descriptions[condition]
        lines.append(f"| {condition} | {item['correct']}/{item['total']} | {item['macro_f1']:.3f} | {item['balanced_accuracy']:.3f} | {scores['same_category']['mean']:.6f} | {scores['different_category']['mean']:.6f} |")
    lines.extend(["", "## Per-category recall", ""])
    for condition, item in metrics.items():
        detail = ", ".join(f"{category}={values['correct']}/{values['total']} ({values['recall']:.3f})" for category, values in item["per_category"].items())
        lines.append(f"- {condition}: {detail}")
    lines.extend(["", "## Confusion matrices", ""])
    for condition, item in metrics.items():
        categories = item["categories"]
        lines.extend(
            [
                f"### {condition}", "",
                "Rows are true categories; columns are predicted categories.", "",
                "| true / predicted | " + " | ".join(categories) + " |",
                "|---|" + "---:|" * len(categories),
            ]
        )
        for category, row in zip(categories, item["confusion_matrix"]):
            lines.append(f"| {category} | " + " | ".join(str(value) for value in row) + " |")
        lines.append("")
    lines.extend(["## Per-query correct-category margins", ""])
    for condition in metrics:
        values = [
            f"{row['query_id']}={row['correct_category_margin']:.6f}"
            if row["correct_category_margin"] is not None
            else f"{row['query_id']}=unavailable"
            for row in predictions
            if row["condition"] == condition
        ]
        lines.append(f"- {condition}: " + ", ".join(values))
    if full:
        lines.extend(["", "## Controlled comparisons", ""])
        for first, second in (("p0_budget", "grayscale_budget"), ("p0_budget", "clahe_budget")):
            changes = prediction_changes(predictions, first, second)
            lines.append(f"- {second} versus {first}: improved queries={changes['improved'] or 'none'}; worsened queries={changes['worsened'] or 'none'}; changed predictions={changes['prediction_changes'] or 'none'}.")
    lines.extend([
        "", "## Interpretation limits", "",
        "These ten images are exploratory development data, not a held-out test set. The 45 pair scores are correlated observations. Repeated configuration selection on these same images does not establish generalization. Higher average similarity does not select a winner; category predictions and margins must be inspected. No acceptance threshold or calibrated confidence is reported. Physical independence is unverified where group metadata is null. Perspective correction is postponed.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_controlled(args: Any) -> None:
    if args.output_dir.exists():
        raise PlateExperimentError(f"Output directory already exists; refusing to overwrite it: {args.output_dir}")
    args.output_dir.mkdir(parents=True)
    try:
        records, manifest_report = validate_manifest(args.controlled_manifest)
    except VariantExperimentError as exc:
        raise PlateExperimentError(str(exc)) from exc
    write_validation_output(args.output_dir, manifest_report)
    if args.validate_only:
        if not manifest_report["valid"]:
            raise PlateExperimentError("Validation is blocked; see VALIDATION_REPORT.md in the new output directory.")
        print(f"Validation written to: {args.output_dir.resolve()}")
        return
    if not manifest_report["valid"]:
        raise PlateExperimentError("Manifest validation failed; see VALIDATION_REPORT.md. No embeddings were computed.")

    records = [record for record in records if record["available"]]
    if len(records) != 10:
        raise PlateExperimentError(f"Controlled evaluation requires exactly 10 valid files; found {len(records)}.")
    policy = ResolutionPolicy(args.target_area, args.max_long_edge, args.max_patch_count, tuple(args.padding_rgb), args.resize_interpolation)
    try:
        validate_policy(policy)
    except PreprocessingError as exc:
        raise PlateExperimentError(str(exc)) from exc
    set_reproducible_seeds()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    processor, model = load_embedding_model(args.embedding_model, device, revision=args.revision, local_files_only=not args.allow_download)
    try:
        patch_size = derive_patch_size(model)
        if patch_size != (14, 14):
            raise PreprocessingError(f"Expected 14x14 patches, got {patch_size}.")
        rectangular = verify_rectangular_model_support(model, device, patch_size)
    except PreprocessingError as exc:
        raise PlateExperimentError(str(exc)) from exc

    condition_embeddings: dict[str, list[torch.Tensor]] = {name: [] for name in HISTORICAL}
    condition_prepared: dict[str, list[Any]] = {name: [] for name in HISTORICAL}
    variants_by_image: list[dict[str, Any]] = []
    if not args.baseline_only:
        for variant in VARIANTS:
            condition_embeddings[f"{variant}_budget"] = []
            condition_prepared[f"{variant}_budget"] = []
    inputs_dir = args.output_dir / "model_inputs"
    stages_dir = args.output_dir / "stages"
    inputs_dir.mkdir()
    if not args.baseline_only:
        stages_dir.mkdir()
    embedding_metadata: dict[str, list[dict[str, Any]]] = {key: [] for key in condition_embeddings}

    try:
        for count, record in enumerate(records, start=1):
            original = prepare_original_input(record["image"], processor)
            add_original_patch_metadata(original, patch_size)
            native = prepare_variable_input(record["image"], processor, "native", patch_size, policy)
            budget = prepare_variable_input(record["image"], processor, "budget", patch_size, policy)
            for condition, prepared in zip(HISTORICAL, (original, native, budget)):
                embedding, metadata = extract_embedding_from_tensor(prepared.pixel_values, model, device)
                condition_embeddings[condition].append(embedding)
                condition_prepared[condition].append(prepared)
                embedding_metadata[condition].append(metadata)
                folder = inputs_dir / condition
                folder.mkdir(exist_ok=True)
                save_png(folder / f"{record['id']}.png", prepared.display_bgr)
            if not args.baseline_only:
                variants = prepare_variants(record["image"])
                variants_by_image.append(variants)
                for variant in VARIANTS:
                    condition = f"{variant}_budget"
                    # P0 deliberately reuses the exact legacy budget tensor and embedding.
                    if variant == "p0":
                        prepared, embedding, metadata = budget, condition_embeddings["budget"][-1].clone(), dict(embedding_metadata["budget"][-1])
                    else:
                        prepared = prepare_variable_input(variants[variant].image_bgr, processor, "budget", patch_size, policy)
                        embedding, metadata = extract_embedding_from_tensor(prepared.pixel_values, model, device)
                    condition_embeddings[condition].append(embedding)
                    condition_prepared[condition].append(prepared)
                    embedding_metadata[condition].append(metadata)
                    folder = inputs_dir / condition
                    folder.mkdir(exist_ok=True)
                    save_png(folder / f"{record['id']}.png", prepared.display_bgr)
                create_stage_figure(stages_dir / f"{record['id']}.png", record, variants, {key: values[-1] for key, values in condition_prepared.items() if key.endswith("_budget")})
            print(f"[{count}/10] embedded image {record['id']}")
    except (PreprocessingError, VariantExperimentError) as exc:
        raise PlateExperimentError(str(exc)) from exc

    matrices = {condition: similarity_matrix(values) for condition, values in condition_embeddings.items()}
    if not args.baseline_only and not np.allclose(matrices["budget"], matrices["p0_budget"], rtol=0, atol=1e-7):
        raise PlateExperimentError("P0 budget matrix does not equal legacy budget matrix.")
    pair_rows = build_pair_rows(matrices, records, not args.baseline_only)
    pair_columns = PAIR_LEGACY_COLUMNS + (() if args.baseline_only else PAIR_EXPERIMENT_COLUMNS)
    write_csv(args.output_dir / "all_pairs.csv", pair_rows, pair_columns)

    predictions: list[dict[str, Any]] = []
    metrics: dict[str, dict[str, Any]] = {}
    descriptions: dict[str, dict[str, Any]] = {}
    categories = sorted({record["category"] for record in records})
    for condition, matrix in matrices.items():
        rows = leave_one_out_predictions(condition, matrix, records, TIE_TOLERANCE)
        predictions.extend(rows)
        metrics[condition] = classification_metrics(rows, categories)
        descriptions[condition] = score_descriptions(matrix, records)
    write_csv(args.output_dir / "leave_one_out_predictions.csv", predictions, PREDICTION_COLUMNS)
    labels = [str(record["id"]) for record in records]
    create_heatmaps(args.output_dir / "similarity_heatmaps.png", matrices, labels)
    create_distributions(args.output_dir / "score_distributions.png", descriptions, matrices, records)
    create_summary(args.output_dir / "classification_summary.png", metrics)
    if not args.baseline_only:
        create_variant_gallery(args.output_dir / "colour_grayscale_clahe_gallery.png", records, variants_by_image)
    write_report(args.output_dir / "CONTROLLED_EXPERIMENT_REPORT.md", metrics, descriptions, predictions, not args.baseline_only)

    result = {
        "schema_version": 1,
        "run_type": "baseline_only" if args.baseline_only else "controlled_colour_grayscale_clahe",
        "manifest": manifest_report,
        "geometry": {
            "manual_annotations_required": False,
            "source_images_are_complete_plate_crops": True,
            "perspective_correction": "postponed",
        },
        "records": [serializable_record(record) for record in records],
        "model": {"identifier": args.embedding_model, "requested_revision": args.revision, "resolved_revision": getattr(model.config, "_commit_hash", None), "device": str(device), "precision": "float32 CLS followed by float32 L2 normalization", "patch_size_hw": list(patch_size), "hidden_size": int(model.config.hidden_size), "rectangular_positional_interpolation": rectangular},
        "packages": dependency_versions(),
        "processor": processor_settings(processor),
        "budget_policy": policy_record(policy),
        "evaluation_policy": {"similarity": "cosine", "leave_one_out": "1-nearest eligible reference using the same condition", "group_exclusion": "matching non-null physical_plate_id or group_id", "tie_tolerance": TIE_TOLERANCE, "tie_resolution": "smallest numerical reference ID; tie_flag remains true", "acceptance_threshold": None},
        "conditions": {
            condition: {
                "classification": metrics[condition],
                "pair_statistics": descriptions[condition],
                "per_image": [
                    {"image_id": record["id"], "model_input": prepared.metadata, "embedding": embedding_meta, **({"image_preparation": variants_by_image[index][condition.removesuffix("_budget")].metadata} if condition.endswith("_budget") and condition.startswith("p") else {})}
                    for index, (record, prepared, embedding_meta) in enumerate(zip(records, condition_prepared[condition], embedding_metadata[condition]))
                ],
            }
            for condition in matrices
        },
        "pair_count": len(pair_rows),
        "limitations": ["Ten exploratory development images.", "The 45 pairs are correlated.", "Physical independence is unverified where group identifiers are null.", "Perspective correction was not evaluated."],
    }
    (args.output_dir / "controlled_results.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Controlled evaluation written to: {args.output_dir.resolve()}")
