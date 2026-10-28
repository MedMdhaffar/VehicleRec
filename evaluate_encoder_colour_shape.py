#!/usr/bin/env python3
"""Compare frozen encoders, colour descriptors, and manual-marker shape methods."""

from __future__ import annotations

import argparse
import csv
import gc
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
import torch
import torch.nn.functional as F
from PIL import Image
from transformers import AutoImageProcessor, AutoModel, AutoProcessor

from compare_plate_embeddings import (
    DEFAULT_EMBEDDING_MODEL,
    PlateExperimentError,
    load_embedding_model,
    processor_settings,
    save_png,
    set_reproducible_seeds,
)
from plate_feature_branches import (
    ADAPTIVE_BLOCK_SIZE,
    ADAPTIVE_C,
    CANNY_THRESHOLDS,
    HSV_BINS,
    HSV_RANGES,
    MIN_GROUP_FRACTION,
    RED_HUE_INTERVALS,
    RED_MIN_SATURATION,
    RED_MIN_VALUE,
    SHAPE_CANVAS_SIZE,
    SHAPE_CONTENT_LONG_EDGE,
    colour_descriptor,
    colour_matrices,
    evaluate_leave_one_out,
    normalize_shape_views,
    shape_matrices,
)
from plate_preprocessing import (
    INTERPOLATIONS,
    PreprocessingError,
    ResolutionPolicy,
    derive_patch_size,
    extract_embedding_from_tensor,
    policy_record,
    prepare_variable_input,
    validate_policy,
)
from plate_representations import cosine_matrix
from plate_variants import (
    VariantExperimentError,
    classification_metrics,
    validate_manifest,
)


CHECKPOINTS = {
    "e0_dinov2_small": {
        "identifier": "facebook/dinov2-small",
        "documentation": "https://huggingface.co/facebook/dinov2-small",
        "license": "Apache-2.0",
    },
    "e1_dinov3_small": {
        "identifier": "facebook/dinov3-vits16-pretrain-lvd1689m",
        "documentation": "https://huggingface.co/facebook/dinov3-vits16-pretrain-lvd1689m",
        "license": "DINOv3 License; Hugging Face access requires accepting the agreement",
    },
    "e2_siglip2_naflex_base": {
        "identifier": "google/siglip2-base-patch16-naflex",
        "documentation": "https://huggingface.co/google/siglip2-base-patch16-naflex",
        "license": "Apache-2.0",
    },
}
PAIR_COLUMNS = (
    "branch", "method", "input_scope", "evaluation_subset", "pair_index",
    "image_1_id", "image_2_id", "category_1", "category_2", "same_category",
    "raw_score", "score_type", "higher_is_better", "valid", "failure_reason",
)
PREDICTION_COLUMNS = (
    "branch", "method", "input_scope", "evaluation_subset", "query_id",
    "query_category", "nearest_reference_id", "predicted_category",
    "nearest_raw_score", "best_correct_category_raw_score",
    "best_incorrect_category_raw_score", "correct_category_margin", "correct",
    "tie_flag", "eligible_reference_count", "valid", "failure_reason",
)
SUMMARY_COLUMNS = (
    "branch", "method", "input_scope", "evaluation_subset", "score_type",
    "higher_is_better", "valid_predictions", "total_queries", "failed_predictions",
    "correct", "accuracy", "macro_f1", "balanced_accuracy", "coverage",
    "per_category_json", "confusion_matrix_json", "failure_summary",
)
EQUIVALENCE_TOLERANCE = 1e-6
SIGLIP2_MAX_PATCHES = 256


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("controlled_plate_manifest.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("encoder_colour_shape_experiment"))
    parser.add_argument("--prior-representation-dir", type=Path, default=Path("representation_experiment_v3"))
    parser.add_argument("--allow-download", action="store_true", help="Allow public Hub access; gated checkpoints still require pre-authorized local access.")
    parser.add_argument("--target-area", type=int, default=50_176)
    parser.add_argument("--max-long-edge", type=int, default=1_008)
    parser.add_argument("--max-patch-count", type=int, default=512)
    parser.add_argument("--padding-rgb", nargs=3, type=int, default=(124, 116, 104), metavar=("R", "G", "B"))
    parser.add_argument("--resize-interpolation", choices=tuple(sorted(INTERPOLATIONS)), default="bicubic")
    return parser.parse_args()


def versions() -> dict[str, Any]:
    result: dict[str, Any] = {"python": platform.python_version()}
    for name in ("torch", "transformers", "opencv-python", "numpy", "Pillow", "matplotlib", "huggingface-hub"):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    result.update({"cuda_available": torch.cuda.is_available(), "torch_cuda_build": torch.version.cuda})
    return result


def write_csv(path: Path, rows: Sequence[dict[str, Any]], columns: Sequence[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def crop_markers(records: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[np.ndarray]]:
    eligible, crops = [], []
    for record in records:
        if record.get("marker_bbox") is None:
            continue
        x1, y1, x2, y2 = (int(value) for value in record["marker_bbox"])
        crop = record["image"][y1:y2, x1:x2].copy()
        eligible.append(record)
        crops.append(crop)
    if [str(record["id"]) for record in eligible] != ["4", "5", "6", "7", "8", "9", "10"]:
        raise VariantExperimentError("Corrected marker subset IDs are not 4 through 10.")
    if eligible[1]["marker_bbox"] != [89, 6, 120, 27]:
        raise VariantExperimentError("Image 5 marker box is not the corrected (89,6,120,27).")
    return eligible, crops


def model_attempt(identifier: str, loader: Any, allow_download: bool) -> tuple[Any | None, str | None]:
    try:
        return loader.from_pretrained(identifier, local_files_only=not allow_download), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:1000]}"


def reconstruct_naflex(inputs: dict[str, torch.Tensor], processor: Any) -> tuple[np.ndarray, dict[str, Any]]:
    patches = inputs["pixel_values"][0].detach().cpu().float()
    mask = inputs["pixel_attention_mask"][0].detach().cpu().bool()
    spatial = inputs["spatial_shapes"][0].detach().cpu().tolist()
    height_patches, width_patches = int(spatial[0]), int(spatial[1])
    valid_count = int(mask.sum())
    if valid_count != height_patches * width_patches:
        raise VariantExperimentError("NaFlex valid patch mask does not match spatial_shapes.")
    patch_size = int(processor.image_processor.patch_size)
    valid = patches[mask]
    expected_width = 3 * patch_size * patch_size
    if valid.shape[1] != expected_width:
        raise VariantExperimentError("NaFlex packed patch width is inconsistent with RGB patch size.")
    # Installed SigLIP 2 processor flattens each patch as (patch_y, patch_x,
    # channel), after arranging patches in row-major (grid_y, grid_x) order.
    image = valid.reshape(height_patches, width_patches, patch_size, patch_size, 3)
    image = image.permute(0, 2, 1, 3, 4).reshape(
        height_patches * patch_size, width_patches * patch_size, 3
    )
    mean = torch.tensor(processor.image_processor.image_mean, dtype=image.dtype)[None, None, :]
    std = torch.tensor(processor.image_processor.image_std, dtype=image.dtype)[None, None, :]
    rgb = ((image * std + mean).clamp(0, 1) * 255).round().to(torch.uint8).numpy()
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), {
        "packed_pixel_values_shape": list(inputs["pixel_values"].shape),
        "pixel_attention_mask_shape": list(inputs["pixel_attention_mask"].shape),
        "valid_patch_count": valid_count,
        "padded_patch_slots": int(mask.numel() - valid_count),
        "spatial_shapes_hw_patches": [height_patches, width_patches],
        "patch_size": patch_size,
        "reconstructed_dimensions_hw": [height_patches * patch_size, width_patches * patch_size],
        "display_note": "Official packed patches reconstructed in row-major spatial order after reversing normalization; uint8 clipping/rounding is display-only.",
    }


def extract_e0(
    images: Sequence[np.ndarray],
    processor: Any,
    model: Any,
    device: torch.device,
    policy: ResolutionPolicy,
    output_dir: Path,
    prefix: str,
) -> tuple[list[torch.Tensor], list[dict[str, Any]]]:
    patch_size = derive_patch_size(model)
    embeddings, metadata = [], []
    output_dir.mkdir(parents=True, exist_ok=True)
    for index, image in enumerate(images):
        prepared = prepare_variable_input(image, processor, "budget", patch_size, policy)
        embedding, embedding_metadata = extract_embedding_from_tensor(prepared.pixel_values, model, device)
        embeddings.append(embedding)
        metadata.append({"preprocessing": prepared.metadata, "embedding": embedding_metadata})
        save_png(output_dir / f"{prefix}_{index + 1}.png", prepared.display_bgr)
    return embeddings, metadata


def extract_e1(
    images: Sequence[np.ndarray], processor: Any, model: Any, device: torch.device,
    policy: ResolutionPolicy, output_dir: Path, prefix: str,
) -> tuple[list[torch.Tensor], list[dict[str, Any]]]:
    patch = getattr(model.config, "patch_size", None)
    patch_size = (int(patch), int(patch)) if isinstance(patch, int) else tuple(int(v) for v in patch)
    embeddings, metadata = [], []
    output_dir.mkdir(parents=True, exist_ok=True)
    for index, image in enumerate(images):
        prepared = prepare_variable_input(image, processor, "budget", patch_size, policy)
        with torch.no_grad():
            outputs = model(pixel_values=prepared.pixel_values.to(device))
            vector = outputs.pooler_output.float()
            vector = F.normalize(vector, p=2, dim=1)[0].cpu()
        embeddings.append(vector)
        metadata.append({"preprocessing": prepared.metadata, "embedding_dimension": int(vector.numel()), "l2_norm": float(torch.linalg.vector_norm(vector)), "feature_path": "official pooler_output; register/patch tokens not pooled manually", "num_register_tokens": getattr(model.config, "num_register_tokens", None)})
        save_png(output_dir / f"{prefix}_{index + 1}.png", prepared.display_bgr)
    return embeddings, metadata


def extract_e2(
    images: Sequence[np.ndarray], processor: Any, model: Any, device: torch.device,
    output_dir: Path, prefix: str,
) -> tuple[list[torch.Tensor], list[dict[str, Any]]]:
    embeddings, metadata = [], []
    output_dir.mkdir(parents=True, exist_ok=True)
    for index, image_bgr in enumerate(images):
        image = Image.fromarray(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB))
        inputs = processor(images=[image], return_tensors="pt", max_num_patches=SIGLIP2_MAX_PATCHES)
        display, packed_metadata = reconstruct_naflex(inputs, processor)
        device_inputs = {key: value.to(device) for key, value in inputs.items()}
        with torch.no_grad():
            feature_output = model.get_image_features(**device_inputs)
            vector = siglip2_pooled_image_feature(feature_output).float()
            vector = F.normalize(vector, p=2, dim=1)[0].cpu()
        embeddings.append(vector)
        metadata.append({"official_processor_outputs": packed_metadata, "embedding_dimension": int(vector.numel()), "l2_norm": float(torch.linalg.vector_norm(vector)), "feature_path": "official model.get_image_features(**processor_outputs)", "max_num_patches": SIGLIP2_MAX_PATCHES})
        save_png(output_dir / f"{prefix}_{index + 1}.png", display)
    return embeddings, metadata


def siglip2_pooled_image_feature(feature_output: Any) -> torch.Tensor:
    """Return SigLIP 2's pooled image feature across Transformers API versions."""
    if isinstance(feature_output, torch.Tensor):
        vector = feature_output
    else:
        vector = getattr(feature_output, "pooler_output", None)
        if vector is None and isinstance(feature_output, (tuple, list)) and len(feature_output) > 1:
            vector = feature_output[1]
    if not isinstance(vector, torch.Tensor) or vector.ndim != 2:
        raise PlateExperimentError(
            "SigLIP 2 get_image_features() did not provide a two-dimensional "
            "pooled image feature tensor."
        )
    return vector


def pairwise_rows_for_method(
    definition: dict[str, Any], records: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    matrix = definition.get("matrix")
    rows = []
    for first in range(len(records)):
        for second in range(first + 1, len(records)):
            valid = matrix is not None and np.isfinite(matrix[first, second])
            failure = None
            if not valid:
                failure = definition.get("unavailable_reason") or definition.get("pair_failures", {}).get(f"{first}:{second}") or "invalid pair score"
            rows.append({
                "branch": definition["branch"], "method": definition["method"],
                "input_scope": definition["input_scope"], "evaluation_subset": definition["evaluation_subset"],
                "pair_index": len(rows) + 1, "image_1_id": str(records[first]["id"]),
                "image_2_id": str(records[second]["id"]), "category_1": records[first]["category"],
                "category_2": records[second]["category"], "same_category": records[first]["category"] == records[second]["category"],
                "raw_score": None if not valid else float(matrix[first, second]), "score_type": definition["score_type"],
                "higher_is_better": definition["higher_is_better"], "valid": valid, "failure_reason": failure,
            })
    return rows


def predictions_for_method(definition: dict[str, Any], records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = evaluate_leave_one_out(records, definition.get("matrix"), definition["higher_is_better"], definition.get("unavailable_reason"))
    return [{"branch": definition["branch"], "method": definition["method"], "input_scope": definition["input_scope"], "evaluation_subset": definition["evaluation_subset"], **row} for row in rows]


def summarize(definition: dict[str, Any], predictions: Sequence[dict[str, Any]], records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    valid = [row for row in predictions if row["valid"]]
    failures = [row for row in predictions if not row["valid"]]
    if valid:
        metrics = classification_metrics(valid, sorted({record["category"] for record in records}))
        accuracy, macro_f1, balanced = metrics["accuracy"], metrics["macro_f1"], metrics["balanced_accuracy"]
        per_category, confusion, correct = metrics["per_category"], metrics["confusion_matrix"], metrics["correct"]
    else:
        accuracy = macro_f1 = balanced = None
        per_category, confusion, correct = {}, [], None
    return {
        "branch": definition["branch"], "method": definition["method"], "input_scope": definition["input_scope"],
        "evaluation_subset": definition["evaluation_subset"], "score_type": definition["score_type"],
        "higher_is_better": definition["higher_is_better"], "valid_predictions": len(valid), "total_queries": len(predictions),
        "failed_predictions": len(failures), "correct": correct, "accuracy": accuracy, "macro_f1": macro_f1,
        "balanced_accuracy": balanced, "coverage": len(valid) / len(predictions), "per_category_json": json.dumps(per_category),
        "confusion_matrix_json": json.dumps(confusion), "failure_summary": "; ".join(sorted({row["failure_reason"] for row in failures if row["failure_reason"]})) or None,
    }


def colour_diagnostic_figure(path: Path, image: np.ndarray, descriptor: dict[str, Any], image_id: str) -> None:
    red_overlay = image.copy()
    red_overlay[descriptor["red_mask"] > 0] = (0, 0, 255)
    panels = [image, descriptor["dark_mask"], descriptor["bright_mask"], red_overlay]
    titles = ["source", "dark Otsu group", "bright Otsu group", "fixed saturated-red overlay"]
    figure, axes = plt.subplots(1, 4, figsize=(16, 4), constrained_layout=True)
    for axis, panel, title in zip(axes, panels, titles):
        axis.imshow(panel if panel.ndim == 2 else cv2.cvtColor(panel, cv2.COLOR_BGR2RGB), cmap="gray")
        axis.set_title(title)
        axis.axis("off")
    figure.suptitle(f"Image {image_id} colour diagnostics")
    figure.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(figure)


def shape_gallery(path: Path, records: Sequence[dict[str, Any]], crops: Sequence[np.ndarray], views: Sequence[dict[str, Any]]) -> None:
    figure, axes = plt.subplots(len(records), 5, figsize=(18, 3 * len(records)), constrained_layout=True)
    for row, (record, crop, view) in enumerate(zip(records, crops, views)):
        panels = [crop, view["gray_canvas"], view["otsu_canvas"], view["adaptive_canvas"], view["edge_canvas"]]
        titles = ["marker", "scaled grayscale", "Otsu", "adaptive Gaussian", "Canny edges"]
        for column, (panel, title) in enumerate(zip(panels, titles)):
            axes[row, column].imshow(panel if panel.ndim == 2 else cv2.cvtColor(panel, cv2.COLOR_BGR2RGB), cmap="gray")
            axes[row, column].set_title(f"{record['id']} {title}", fontsize=9)
            axes[row, column].axis("off")
    figure.suptitle("Manual-marker shape views; foreground normalized white; no component removal")
    figure.savefig(path, dpi=140, bbox_inches="tight")
    plt.close(figure)


def image_gallery(path: Path, records: Sequence[dict[str, Any]], images: Sequence[np.ndarray], title: str) -> None:
    figure, axes = plt.subplots(len(images), 1, figsize=(13, 2.6 * len(images)), squeeze=False, constrained_layout=True)
    for axis, record, image in zip(axes[:, 0], records, images):
        axis.imshow(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        axis.set_title(f"{record['id']} {record['category']} | {image.shape[1]}×{image.shape[0]}")
        axis.axis("off")
    figure.suptitle(title)
    figure.savefig(path, dpi=140, bbox_inches="tight")
    plt.close(figure)


def heatmap_figure(path: Path, definitions: Sequence[dict[str, Any]], records: Sequence[dict[str, Any]], title: str) -> None:
    available = [definition for definition in definitions if definition.get("matrix") is not None]
    columns = 3
    rows = max(1, int(np.ceil(len(available) / columns)))
    figure, axes = plt.subplots(rows, columns, figsize=(6 * columns, 5 * rows), squeeze=False, constrained_layout=True)
    labels = [str(record["id"]) for record in records]
    for axis, definition in zip(axes.flat, available):
        matrix = definition["matrix"]
        finite = matrix[np.isfinite(matrix)]
        shown = axis.imshow(matrix, vmin=float(finite.min()), vmax=float(finite.max()), cmap="viridis_r" if not definition["higher_is_better"] else "viridis")
        axis.set_title(f"{definition['method']} ({definition['score_type']})")
        axis.set_xticks(range(len(labels)), labels)
        axis.set_yticks(range(len(labels)), labels)
        figure.colorbar(shown, ax=axis, fraction=0.046)
    for axis in list(axes.flat)[len(available):]: axis.axis("off")
    figure.suptitle(title + " — per-method scales; raw magnitudes are not comparable")
    figure.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(figure)


def confusion_figure(path: Path, summaries: Sequence[dict[str, Any]], title: str) -> None:
    valid = [summary for summary in summaries if summary["valid_predictions"] > 0]
    columns = 3
    rows = max(1, int(np.ceil(len(valid) / columns)))
    figure, axes = plt.subplots(rows, columns, figsize=(5 * columns, 4.5 * rows), squeeze=False, constrained_layout=True)
    for axis, item in zip(axes.flat, valid):
        matrix = np.asarray(json.loads(item["confusion_matrix_json"]))
        categories = list(json.loads(item["per_category_json"]))
        axis.imshow(matrix, cmap="Blues", vmin=0, vmax=max(1, int(matrix.max())))
        axis.set_xticks(range(len(categories)), categories, rotation=35, ha="right")
        axis.set_yticks(range(len(categories)), categories)
        axis.set_title(item["method"])
        axis.set_xlabel("predicted"); axis.set_ylabel("true")
        for row in range(len(categories)):
            for column in range(len(categories)): axis.text(column, row, str(matrix[row, column]), ha="center", va="center")
    for axis in list(axes.flat)[len(valid):]: axis.axis("off")
    figure.suptitle(title)
    figure.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(figure)


def margin_figure(path: Path, predictions: Sequence[dict[str, Any]], title: str) -> None:
    valid = [row for row in predictions if row["valid"] and row["correct_category_margin"] is not None]
    series = list(dict.fromkeys((row["method"], row["input_scope"]) for row in valid))
    query_ids = sorted({row["query_id"] for row in valid}, key=int)
    figure, axis = plt.subplots(figsize=(14, 6), constrained_layout=True)
    for method, scope in series:
        values = {row["query_id"]: row["correct_category_margin"] for row in valid if row["method"] == method and row["input_scope"] == scope}
        axis.plot(query_ids, [values.get(query, np.nan) for query in query_ids], marker="o", label=f"{method}/{scope}")
    axis.axhline(0, color="black", linewidth=1)
    axis.set_ylabel("positive-is-better correct-category margin")
    axis.set_xlabel("query ID")
    axis.legend(fontsize=8, ncol=2)
    axis.set_title(title)
    figure.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(figure)


def prior_matrix(prior_dir: Path, filename: str, column: str, records: Sequence[dict[str, Any]]) -> np.ndarray:
    matrix = np.eye(len(records), dtype=np.float64)
    index = {str(record["id"]): number for number, record in enumerate(records)}
    with (prior_dir / filename).open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            first, second = index[row["image_1_id"]], index[row["image_2_id"]]
            matrix[first, second] = matrix[second, first] = float(row[column])
    return matrix


def write_report(path: Path, summaries: Sequence[dict[str, Any]], availability: dict[str, Any], baseline: dict[str, Any], historical_r2: dict[str, Any], predictions: Sequence[dict[str, Any]]) -> None:
    completed_models = [method for method, item in availability.items() if item["status"] == "completed"]
    blocked_models = [method for method, item in availability.items() if item["status"] != "completed"]
    blocked_statement = (
        f"Blocked encoders ({', '.join(blocked_models)}) have explicit missing results; no scores were fabricated."
        if blocked_models else
        "All requested encoders completed."
    )
    lines = ["# Encoder, Colour, and Shape Plate Experiment", "", "Branches remain separate; raw scores and accuracies across unequal subsets are not fused or treated as calibrated confidence.", "", "## Model availability", ""]
    for method, item in availability.items():
        lines.append(f"- {method}: **{item['status']}** — {item.get('reason') or 'loaded successfully'}")
    lines.extend(["", "## Baseline reproduction", "", f"- Whole E0 maximum matrix difference: {baseline['whole_max_abs_difference']:.9g}", f"- Marker E0 maximum matrix difference: {baseline['marker_max_abs_difference']:.9g}", f"- Tolerance: {baseline['tolerance']}", ""])
    for subset, heading in (("full_10", "Full ten-image/four-category"), ("marker_7", "Matched seven-image/three-category")):
        lines.extend([f"## {heading} results", "", "| Branch | Method | Scope | Coverage | Correct | Macro-F1 | Balanced accuracy |", "|---|---|---|---:|---:|---:|---:|"])
        for item in summaries:
            if item["evaluation_subset"] != subset: continue
            correct = "blocked" if item["correct"] is None else f"{item['correct']}/{item['valid_predictions']}"
            macro = "—" if item["macro_f1"] is None else f"{item['macro_f1']:.3f}"
            balanced = "—" if item["balanced_accuracy"] is None else f"{item['balanced_accuracy']:.3f}"
            lines.append(f"| {item['branch']} | {item['method']} | {item['input_scope']} | {item['valid_predictions']}/{item['total_queries']} | {correct} | {macro} | {balanced} |")
        lines.append("")
    lines.extend(["## Historical reference", "", f"Previous DINOv2 regional R2 (not rerun as a new encoder configuration): full={historical_r2['full_correct']}/{historical_r2['full_total']}, matched marker subset={historical_r2['marker_correct']}/{historical_r2['marker_total']}.", "", "## Error comparison", ""])
    for subset in ("full_10", "marker_7"):
        selected = [row for row in predictions if row["evaluation_subset"] == subset and row["valid"]]
        e0 = {row["query_id"]: row for row in selected if row["method"] == "e0_dinov2_small" and row["input_scope"] == "whole"}
        for method in sorted({row["method"] for row in selected}):
            method_rows = {row["query_id"]: row for row in selected if row["method"] == method}
            if not method_rows or method == "e0_dinov2_small" or set(method_rows) != set(e0): continue
            improved = [query for query in e0 if not e0[query]["correct"] and method_rows[query]["correct"]]
            worsened = [query for query in e0 if e0[query]["correct"] and not method_rows[query]["correct"]]
            nt = {query: method_rows[query]["predicted_category"] for query in method_rows if method_rows[query]["query_category"] == "nt"}
            lines.append(f"- {subset} {method}: improved={improved or 'none'}, worsened={worsened or 'none'}, NT predictions={nt}.")
    lines.extend([
        "",
        "## Runtime measurement",
        "",
        f"Warm feature-extraction latency was not measured; no synchronized repeated timing protocol was executed. Completed encoders: {', '.join(completed_models)}. Model loading and downloads are not treated as inference latency.",
        "",
        "## Interpretation limits",
        "",
        f"E0/E1/E2 compare encoder-plus-preprocessing systems because their official preprocessors differ. {blocked_statement} Colour groups are Otsu bright/dark intensity proxies, not guaranteed foreground/background. Shape methods assume ideal manual marker localization. These ten images are development data, pair observations are correlated, and physical independence is unverified where group IDs are null.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    if args.output_dir.exists():
        raise PlateExperimentError(f"Output directory already exists; refusing to overwrite: {args.output_dir}")
    records, manifest_report = validate_manifest(args.manifest)
    if not manifest_report["valid"] or len(records) != 10:
        raise PlateExperimentError("Invalid controlled manifest: " + "; ".join(manifest_report["errors"]))
    marker_records, marker_crops = crop_markers(records)
    policy = ResolutionPolicy(args.target_area, args.max_long_edge, args.max_patch_count, tuple(args.padding_rgb), args.resize_interpolation)
    validate_policy(policy)
    args.output_dir.mkdir(parents=True)
    for directory in ("model_inputs/e0/whole", "model_inputs/e0/marker", "colour_diagnostics", "shape_views", "caches"):
        (args.output_dir / directory).mkdir(parents=True, exist_ok=True)
    image_gallery(args.output_dir / "whole_plate_gallery.png", records, [record["image"] for record in records], "Whole plate crops")
    image_gallery(args.output_dir / "corrected_marker_gallery.png", marker_records, marker_crops, "Corrected manual marker crops")

    set_reproducible_seeds()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    availability: dict[str, Any] = {}
    embedding_metadata: dict[str, Any] = {}
    embedding_matrices: dict[str, dict[str, np.ndarray | None]] = {}

    # E0: cached and required.
    processor_e0, model_e0 = load_embedding_model(DEFAULT_EMBEDDING_MODEL, device, local_files_only=True)
    model_e0.eval()
    whole_e0, whole_meta = extract_e0([record["image"] for record in records], processor_e0, model_e0, device, policy, args.output_dir / "model_inputs/e0/whole", "image")
    marker_e0, marker_meta = extract_e0(marker_crops, processor_e0, model_e0, device, policy, args.output_dir / "model_inputs/e0/marker", "marker")
    embedding_matrices["e0_dinov2_small"] = {"whole": cosine_matrix(whole_e0), "marker": cosine_matrix(marker_e0)}
    availability["e0_dinov2_small"] = {**CHECKPOINTS["e0_dinov2_small"], "status": "completed", "reason": None, "resolved_revision": getattr(model_e0.config, "_commit_hash", None), "processor_class": type(processor_e0).__name__, "model_class": type(model_e0).__name__, "device": str(device)}
    embedding_metadata["e0_dinov2_small"] = {"whole": whole_meta, "marker": marker_meta, "processor": processor_settings(processor_e0)}
    np.savez_compressed(args.output_dir / "caches/e0_embeddings.npz", whole=np.stack([value.numpy() for value in whole_e0]), marker=np.stack([value.numpy() for value in marker_e0]))
    del model_e0
    gc.collect()

    # E1: exact web-pretrained DINOv3 ViT-S/16; gated/uncached is reported, never substituted.
    e1_processor, e1_processor_error = model_attempt(CHECKPOINTS["e1_dinov3_small"]["identifier"], AutoImageProcessor, args.allow_download)
    e1_model, e1_model_error = (None, None)
    if e1_processor is not None:
        e1_model, e1_model_error = model_attempt(CHECKPOINTS["e1_dinov3_small"]["identifier"], AutoModel, args.allow_download)
    if e1_processor is None or e1_model is None:
        reason = e1_processor_error or e1_model_error or "checkpoint unavailable"
        availability["e1_dinov3_small"] = {**CHECKPOINTS["e1_dinov3_small"], "status": "blocked", "reason": reason, "cached": False, "access_requirement": "accept DINOv3 License and obtain repository approval"}
        embedding_matrices["e1_dinov3_small"] = {"whole": None, "marker": None}
    else:
        e1_model.to(device).eval()
        whole, whole_meta_e1 = extract_e1([record["image"] for record in records], e1_processor, e1_model, device, policy, args.output_dir / "model_inputs/e1/whole", "image")
        marker, marker_meta_e1 = extract_e1(marker_crops, e1_processor, e1_model, device, policy, args.output_dir / "model_inputs/e1/marker", "marker")
        embedding_matrices["e1_dinov3_small"] = {"whole": cosine_matrix(whole), "marker": cosine_matrix(marker)}
        availability["e1_dinov3_small"] = {**CHECKPOINTS["e1_dinov3_small"], "status": "completed", "reason": None, "resolved_revision": getattr(e1_model.config, "_commit_hash", None), "processor_class": type(e1_processor).__name__, "model_class": type(e1_model).__name__, "device": str(device)}
        embedding_metadata["e1_dinov3_small"] = {"whole": whole_meta_e1, "marker": marker_meta_e1}
        np.savez_compressed(args.output_dir / "caches/e1_embeddings.npz", whole=np.stack([value.numpy() for value in whole]), marker=np.stack([value.numpy() for value in marker]))
        del e1_model
        gc.collect()

    # E2: official NaFlex packing/masks/spatial metadata and get_image_features.
    e2_processor, e2_processor_error = model_attempt(CHECKPOINTS["e2_siglip2_naflex_base"]["identifier"], AutoProcessor, args.allow_download)
    e2_model, e2_model_error = (None, None)
    if e2_processor is not None:
        e2_model, e2_model_error = model_attempt(CHECKPOINTS["e2_siglip2_naflex_base"]["identifier"], AutoModel, args.allow_download)
    if e2_processor is None or e2_model is None:
        reason = e2_processor_error or e2_model_error or "checkpoint unavailable"
        availability["e2_siglip2_naflex_base"] = {**CHECKPOINTS["e2_siglip2_naflex_base"], "status": "blocked", "reason": reason, "cached": False, "package_compatible": True, "required_processor_outputs": ["pixel_values", "pixel_attention_mask", "spatial_shapes"]}
        embedding_matrices["e2_siglip2_naflex_base"] = {"whole": None, "marker": None}
    else:
        e2_model.to(device).eval()
        whole, whole_meta_e2 = extract_e2([record["image"] for record in records], e2_processor, e2_model, device, args.output_dir / "model_inputs/e2/whole", "image")
        marker, marker_meta_e2 = extract_e2(marker_crops, e2_processor, e2_model, device, args.output_dir / "model_inputs/e2/marker", "marker")
        embedding_matrices["e2_siglip2_naflex_base"] = {"whole": cosine_matrix(whole), "marker": cosine_matrix(marker)}
        availability["e2_siglip2_naflex_base"] = {**CHECKPOINTS["e2_siglip2_naflex_base"], "status": "completed", "reason": None, "resolved_revision": getattr(e2_model.config, "_commit_hash", None), "processor_class": type(e2_processor).__name__, "model_class": type(e2_model).__name__, "device": str(device), "max_num_patches": SIGLIP2_MAX_PATCHES}
        embedding_metadata["e2_siglip2_naflex_base"] = {"whole": whole_meta_e2, "marker": marker_meta_e2}
        np.savez_compressed(args.output_dir / "caches/e2_embeddings.npz", whole=np.stack([value.numpy() for value in whole]), marker=np.stack([value.numpy() for value in marker]))
        del e2_model
        gc.collect()

    # Fixed whole-plate colour branch.
    colour_descriptors = [colour_descriptor(record["image"]) for record in records]
    colour_matrix_all = colour_matrices(colour_descriptors)
    colour_rows = []
    for record, descriptor in zip(records, colour_descriptors):
        colour_diagnostic_figure(args.output_dir / "colour_diagnostics" / f"{record['id']}.png", record["image"], descriptor, str(record["id"]))
        flat = {"image_id": str(record["id"]), "category": record["category"], **{key: value for key, value in descriptor["measurements"].items() if key != "red_spatial_distribution"}, "red_spatial_distribution_json": json.dumps(descriptor["measurements"]["red_spatial_distribution"])}
        colour_rows.append(flat)
    colour_columns = list(colour_rows[0])
    write_csv(args.output_dir / "colour_measurements.csv", colour_rows, colour_columns)
    np.savez_compressed(args.output_dir / "caches/colour_descriptors.npz", c0=np.stack([d["c0_histogram"] for d in colour_descriptors]), bright=np.stack([d["c1_bright_histogram"] for d in colour_descriptors]), dark=np.stack([d["c1_dark_histogram"] for d in colour_descriptors]))

    # Fixed manual-marker shape branch.
    shape_views = [normalize_shape_views(crop) for crop in marker_crops]
    shape_output = shape_matrices(shape_views)
    shape_gallery(args.output_dir / "shape_views.png", marker_records, marker_crops, shape_views)
    np.savez_compressed(args.output_dir / "caches/shape_descriptors.npz", adaptive=np.stack([v["adaptive_canvas"] for v in shape_views]), edges=np.stack([v["edge_canvas"] for v in shape_views]), masks=np.stack([v["valid_mask"] for v in shape_views]))

    marker_indices = [index for index, record in enumerate(records) if record.get("marker_bbox") is not None]
    definitions_full, definitions_marker = [], []
    failure_by_model = {method: availability[method].get("reason") for method in CHECKPOINTS if availability[method]["status"] != "completed"}
    for method in CHECKPOINTS:
        full_matrix = embedding_matrices[method]["whole"]
        definitions_full.append({"branch": "embedding", "method": method, "input_scope": "whole", "evaluation_subset": "full_10", "matrix": full_matrix, "score_type": "cosine_similarity", "higher_is_better": True, "unavailable_reason": failure_by_model.get(method)})
        subset_matrix = None if full_matrix is None else full_matrix[np.ix_(marker_indices, marker_indices)]
        definitions_marker.append({"branch": "embedding", "method": method, "input_scope": "whole", "evaluation_subset": "marker_7", "matrix": subset_matrix, "score_type": "cosine_similarity", "higher_is_better": True, "unavailable_reason": failure_by_model.get(method)})
        definitions_marker.append({"branch": "embedding", "method": method, "input_scope": "manual_marker", "evaluation_subset": "marker_7", "matrix": embedding_matrices[method]["marker"], "score_type": "cosine_similarity", "higher_is_better": True, "unavailable_reason": failure_by_model.get(method)})
    for method, matrix in colour_matrix_all.items():
        definitions_full.append({"branch": "colour", "method": method, "input_scope": "whole", "evaluation_subset": "full_10", "matrix": matrix, "score_type": "hellinger_distance", "higher_is_better": False})
        definitions_marker.append({"branch": "colour", "method": method, "input_scope": "whole", "evaluation_subset": "marker_7", "matrix": matrix[np.ix_(marker_indices, marker_indices)], "score_type": "hellinger_distance", "higher_is_better": False})
    for method, output in shape_output.items():
        definitions_marker.append({"branch": "shape", "method": method, "input_scope": "manual_marker", "evaluation_subset": "marker_7", "matrix": output["matrix"], "pair_failures": output["pair_failures"], "score_type": "normalized_correlation" if method.startswith("s0") else "normalized_chamfer_distance", "higher_is_better": method.startswith("s0")})

    pair_rows, prediction_rows, summaries = [], [], []
    for definition in definitions_full:
        pair_rows.extend(pairwise_rows_for_method(definition, records))
        method_predictions = predictions_for_method(definition, records)
        prediction_rows.extend(method_predictions)
        summaries.append(summarize(definition, method_predictions, records))
    for definition in definitions_marker:
        pair_rows.extend(pairwise_rows_for_method(definition, marker_records))
        method_predictions = predictions_for_method(definition, marker_records)
        prediction_rows.extend(method_predictions)
        summaries.append(summarize(definition, method_predictions, marker_records))
    write_csv(args.output_dir / "pairwise_results.csv", pair_rows, PAIR_COLUMNS)
    write_csv(args.output_dir / "leave_one_out_predictions.csv", prediction_rows, PREDICTION_COLUMNS)
    write_csv(args.output_dir / "summary_metrics.csv", summaries, SUMMARY_COLUMNS)

    prior_full = prior_matrix(args.prior_representation_dir, "all_pairs_full.csv", "r0_cls_similarity", records)
    prior_marker = prior_matrix(args.prior_representation_dir, "all_pairs_marker_subset.csv", "r3_marker_cls_similarity", marker_records)
    baseline = {"tolerance": EQUIVALENCE_TOLERANCE, "whole_max_abs_difference": float(np.max(np.abs(prior_full - embedding_matrices["e0_dinov2_small"]["whole"]))), "marker_max_abs_difference": float(np.max(np.abs(prior_marker - embedding_matrices["e0_dinov2_small"]["marker"])))}
    if baseline["whole_max_abs_difference"] > EQUIVALENCE_TOLERANCE or baseline["marker_max_abs_difference"] > EQUIVALENCE_TOLERANCE:
        raise PlateExperimentError(f"E0 baseline reproduction failed: {baseline}")
    prior_json = json.loads((args.prior_representation_dir / "representation_results.json").read_text(encoding="utf-8"))
    historical_r2 = {"full_correct": prior_json["subsets"]["full_10"]["representations"]["r2_global_regional"]["classification"]["correct"], "full_total": 10, "marker_correct": prior_json["subsets"]["marker_7"]["representations"]["r2_global_regional"]["classification"]["correct"], "marker_total": 7, "source": str(args.prior_representation_dir)}

    heatmap_figure(args.output_dir / "pairwise_heatmaps_full.png", definitions_full, records, "Full ten-image methods")
    heatmap_figure(args.output_dir / "pairwise_heatmaps_marker_subset.png", definitions_marker, marker_records, "Matched marker-subset methods")
    confusion_figure(args.output_dir / "confusion_matrices_full.png", [item for item in summaries if item["evaluation_subset"] == "full_10"], "Full ten-image confusion matrices")
    confusion_figure(args.output_dir / "confusion_matrices_marker_subset.png", [item for item in summaries if item["evaluation_subset"] == "marker_7"], "Matched marker-subset confusion matrices")
    margin_figure(args.output_dir / "margins_full.png", [row for row in prediction_rows if row["evaluation_subset"] == "full_10"], "Full ten-image margins")
    margin_figure(args.output_dir / "margins_marker_subset.png", [row for row in prediction_rows if row["evaluation_subset"] == "marker_7"], "Matched marker-subset margins")
    write_report(args.output_dir / "ENCODER_COLOUR_SHAPE_REPORT.md", summaries, availability, baseline, historical_r2, prediction_rows)

    configuration = {
        "schema_version": 1, "manifest": manifest_report, "environment": versions(), "device": str(device),
        "models": availability, "embedding_metadata": embedding_metadata, "preprocessing": {"e0_and_e1_budget_policy": policy_record(policy), "e2_naflex_max_num_patches": SIGLIP2_MAX_PATCHES, "e2_policy": "official NaFlex processor only; no DINO preprocessing"},
        "colour": {"opencv_hsv_ranges": {"H": [0, 179], "S": [0, 255], "V": [0, 255]}, "joint_histogram_bins_hsv": HSV_BINS, "histogram_ranges": HSV_RANGES, "normalization": "L1", "distance": "Hellinger", "c1_grouping": "grayscale Otsu bright/dark proxies", "c1_group_weights": [0.5, 0.5], "near_empty_group_fraction": MIN_GROUP_FRACTION, "empty_policy": "both empty distance 0; one empty distance 1", "red_hue_intervals_inclusive": RED_HUE_INTERVALS, "red_min_saturation": RED_MIN_SATURATION, "red_min_value": RED_MIN_VALUE},
        "shape": {"manual_marker_subset_ids": [str(record["id"]) for record in marker_records], "corrected_image_5_bbox": [89, 6, 120, 27], "canvas_size": SHAPE_CANVAS_SIZE, "content_long_edge": SHAPE_CONTENT_LONG_EDGE, "adaptive_block_size": ADAPTIVE_BLOCK_SIZE, "adaptive_C": ADAPTIVE_C, "canny_thresholds": CANNY_THRESHOLDS, "S0": "bidirectional normalized correlation on adaptive views, averaged; no translation/scale search", "S1": "bidirectional edge chamfer mean, normalized by canvas diagonal", "empty_policy": "invalid pair; never perfect"},
        "evaluation": {"full_pair_count_per_method": 45, "marker_pair_count_per_method": 21, "classifier": "leave-one-out 1-NN", "tie_tolerance": 1e-12, "tie_rule": "smallest numerical reference ID, tie flagged", "physical_independence": "unverified where group IDs are null", "raw_scores_across_methods_comparable": False},
        "baseline_reproduction": baseline, "historical_r2_reference": historical_r2, "runtime": {"warm_latency_measured": False, "reason": "No synchronized repeated warm-inference timing protocol was executed; model loading and downloads are excluded."},
    }
    (args.output_dir / "experiment_configuration.json").write_text(json.dumps(configuration, indent=2) + "\n", encoding="utf-8")
    print(f"Experiment written to: {args.output_dir.resolve()}")


def main() -> int:
    try:
        run(parse_args())
        return 0
    except (PlateExperimentError, PreprocessingError, VariantExperimentError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted by user.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
