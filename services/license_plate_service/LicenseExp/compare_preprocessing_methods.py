#!/usr/bin/env python3
"""Compare original, native rectangular, and budgeted rectangular preprocessing."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import shutil
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
    FINAL_MESSAGE,
    PlateDetection,
    PlateExperimentError,
    compute_similarity_matrix,
    crop_plate,
    detect_plate,
    find_most_similar_pair,
    load_embedding_model,
    load_plate_detector,
    processor_settings,
    read_image,
    save_png,
    set_reproducible_seeds,
    sha256_file,
)
from plate_preprocessing import (
    INTERPOLATIONS,
    PreparedInput,
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


METHODS = ("original", "native", "budget")
PAIR_KEYS = ("plate_1_plate_2", "plate_1_plate_3", "plate_2_plate_3")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run original/native/budget preprocessing on identical plate crops."
    )
    parser.add_argument("--image1", required=True, type=Path)
    parser.add_argument("--image2", required=True, type=Path)
    parser.add_argument("--image3", required=True, type=Path)
    parser.add_argument("--cropped-plates", action="store_true")
    parser.add_argument("--yolo-weights", type=Path)
    parser.add_argument("--det-conf", type=float, default=0.25)
    parser.add_argument("--crop-padding", type=float, default=0.05)
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--revision")
    parser.add_argument("--allow-download", action="store_true")
    parser.add_argument("--target-area", type=int, default=50_176)
    parser.add_argument("--max-long-edge", type=int, default=1_008)
    parser.add_argument("--max-patch-count", type=int, default=512)
    parser.add_argument(
        "--padding-rgb", nargs=3, type=int, metavar=("R", "G", "B"), default=(124, 116, 104)
    )
    parser.add_argument(
        "--resize-interpolation",
        choices=tuple(sorted(INTERPOLATIONS)),
        default="bicubic",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("preprocessing_comparison")
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> list[Path]:
    paths = [args.image1, args.image2, args.image3]
    for path in paths:
        if not path.is_file():
            raise PlateExperimentError(f"Image file does not exist: {path}")
    if args.cropped_plates and args.yolo_weights is not None:
        raise PlateExperimentError("Do not combine --cropped-plates and --yolo-weights.")
    if not args.cropped_plates:
        if args.yolo_weights is None or not args.yolo_weights.is_file():
            raise PlateExperimentError(
                "--yolo-weights is required unless --cropped-plates is used."
            )
    if not np.isfinite(args.det_conf) or not 0 <= args.det_conf <= 1:
        raise PlateExperimentError("--det-conf must be between 0 and 1 inclusive.")
    if not np.isfinite(args.crop_padding) or args.crop_padding < 0:
        raise PlateExperimentError("--crop-padding must be finite and non-negative.")
    if args.output_dir.exists():
        raise PlateExperimentError(
            f"Output directory already exists; refusing to overwrite it: {args.output_dir}"
        )
    return paths


def package_versions() -> dict[str, str | None]:
    result: dict[str, str | None] = {"python": sys.version.replace("\n", " ")}
    for name in (
        "transformers",
        "torch",
        "torchvision",
        "ultralytics",
        "opencv-python",
        "Pillow",
        "numpy",
        "matplotlib",
        "huggingface-hub",
    ):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    return result


def retained_overlay(image: np.ndarray, box: Sequence[float]) -> np.ndarray:
    height, width = image.shape[:2]
    x1, y1, x2, y2 = box
    left, top = max(0, int(np.floor(x1))), max(0, int(np.floor(y1)))
    right, bottom = min(width, int(np.ceil(x2))), min(height, int(np.ceil(y2)))
    tinted = cv2.addWeighted(image, 0.55, np.full_like(image, (32, 32, 190)), 0.45, 0)
    tinted[top:bottom, left:right] = image[top:bottom, left:right]
    cv2.rectangle(
        tinted,
        (left, top),
        (max(left, right - 1), max(top, bottom - 1)),
        (0, 220, 0),
        max(1, round(min(height, width) / 80)),
    )
    return tinted


def padding_boundary_copy(image: np.ndarray, metadata: dict[str, Any]) -> np.ndarray:
    display = image.copy()
    left, top, right, bottom = metadata["padding_ltrb"]
    content_height, content_width = metadata["resized_dimensions_hw"]
    x2, y2 = left + content_width - 1, top + content_height - 1
    cv2.rectangle(
        display,
        (left, top),
        (x2, y2),
        (0, 255, 0),
        max(1, round(min(image.shape[:2]) / 100)),
    )
    return display


def title_for_variable(item: PreparedInput, label: str) -> str:
    metadata = item.metadata
    height, width = metadata["final_dimensions_hw"]
    grid_height, grid_width = metadata["patch_grid_hw"]
    left, top, right, bottom = metadata["padding_ltrb"]
    return (
        f"{label}: {width}×{height}\n"
        f"pad L/T/R/B={left}/{top}/{right}/{bottom}; "
        f"grid {grid_width}×{grid_height}={metadata['patch_count']}"
    )


def create_crop_stage_figure(
    output_path: Path,
    source_path: Path,
    original: np.ndarray,
    crop: np.ndarray,
    crop_box: Sequence[int],
    detection: PlateDetection | None,
    prepared: dict[str, PreparedInput],
) -> None:
    original_item = prepared["original"]
    native_item = prepared["native"]
    budget_item = prepared["budget"]
    retained = original_item.metadata[
        "mapped_retained_region_clipped_xyxy_in_plate_crop"
    ]
    overlay = retained_overlay(crop, retained)
    budget_boundary = padding_boundary_copy(
        budget_item.padded_bgr, budget_item.metadata
    )
    native_boundary = padding_boundary_copy(
        native_item.padded_bgr, native_item.metadata
    )

    panels = [
        (original, f"1. Original source: {original.shape[1]}×{original.shape[0]}"),
        (crop, f"2. Selected crop: {crop.shape[1]}×{crop.shape[0]}"),
        (overlay, "Original retained map\ngreen retained; red excluded"),
        (
            original_item.display_bgr,
            f"3. Original model input: {original_item.display_bgr.shape[1]}×"
            f"{original_item.display_bgr.shape[0]}",
        ),
        (
            budget_item.resized_bgr,
            f"4. Budget resized: {budget_item.resized_bgr.shape[1]}×"
            f"{budget_item.resized_bgr.shape[0]}\n"
            f"scale x/y={budget_item.metadata['effective_scale_xy'][0]:.4f}/"
            f"{budget_item.metadata['effective_scale_xy'][1]:.4f}",
        ),
        (budget_boundary, "5. Budget padded (display overlay)\n" + title_for_variable(budget_item, "green=content")),
        (budget_item.display_bgr, "6. Budget exact-tensor reconstruction\n" + title_for_variable(budget_item, "tensor")),
        (native_boundary, "Native padded (display overlay)\n" + title_for_variable(native_item, "green=content")),
        (native_item.display_bgr, "Native exact-tensor reconstruction\n" + title_for_variable(native_item, "tensor")),
    ]
    figure, axes = plt.subplots(3, 3, figsize=(18, 13), constrained_layout=True)
    for axis, (image, title) in zip(axes.flat, panels):
        axis.imshow(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        axis.set_title(title, fontsize=10)
        axis.axis("off")
    detection_text = (
        "detection skipped; selected crop is the entire source"
        if detection is None
        else f"YOLO bbox={list(detection.bbox)}; padded crop bbox={list(crop_box)}"
    )
    figure.suptitle(
        f"{source_path} | {detection_text}\n"
        "Exact-tensor views reverse normalization/rescaling; display copies are clipped/rounded uint8.",
        fontsize=13,
    )
    figure.savefig(output_path, dpi=160, bbox_inches="tight")
    plt.close(figure)


def similarities_record(matrix: np.ndarray) -> dict[str, float]:
    return {
        "plate_1_plate_2": float(matrix[0, 1]),
        "plate_1_plate_3": float(matrix[0, 2]),
        "plate_2_plate_3": float(matrix[1, 2]),
    }


def create_overall_figure(
    output_path: Path,
    crops: Sequence[np.ndarray],
    prepared_by_method: dict[str, list[PreparedInput]],
    method_records: dict[str, dict[str, Any]],
) -> None:
    figure = plt.figure(figsize=(17, 14), constrained_layout=True)
    grid = figure.add_gridspec(4, 4, height_ratios=[1, 1, 1, 0.72])
    headers = ("Selected crop", "Original square", "Native rectangular", "Budget rectangular")
    for row in range(3):
        images = [
            crops[row],
            prepared_by_method["original"][row].display_bgr,
            prepared_by_method["native"][row].display_bgr,
            prepared_by_method["budget"][row].display_bgr,
        ]
        for column, (header, image) in enumerate(zip(headers, images)):
            axis = figure.add_subplot(grid[row, column])
            axis.imshow(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
            axis.set_title(
                f"Plate {row + 1}: {header}\n{image.shape[1]}×{image.shape[0]}"
            )
            axis.axis("off")
    summary_axis = figure.add_subplot(grid[3, :])
    summary_axis.axis("off")
    lines = ["Method comparison (cosine similarity; higher is not an accuracy measure)"]
    for method in METHODS:
        record = method_records[method]
        scores = record["similarities"]
        pair = " / ".join(record["most_similar_pair"])
        lines.append(
            f"{method:8s}  P1-P2 {scores['plate_1_plate_2']:.6f}   "
            f"P1-P3 {scores['plate_1_plate_3']:.6f}   "
            f"P2-P3 {scores['plate_2_plate_3']:.6f}   most similar: {pair}"
        )
    summary_axis.text(
        0.5,
        0.5,
        "\n".join(lines),
        ha="center",
        va="center",
        family="monospace",
        fontsize=12,
        bbox={"boxstyle": "round,pad=0.7", "facecolor": "#f4f6f8", "edgecolor": "#555"},
    )
    figure.suptitle("DINOv2 plate preprocessing comparison", fontsize=17, fontweight="bold")
    figure.savefig(output_path, dpi=160, bbox_inches="tight")
    plt.close(figure)


def write_markdown_table(path: Path, method_records: dict[str, dict[str, Any]]) -> None:
    original_scores = method_records["original"]["similarities"]
    lines = [
        "# Preprocessing Similarity Comparison",
        "",
        "Higher cosine similarity is not a category-accuracy measure.",
        "",
        "| Method | P1–P2 | Δ vs original | P1–P3 | Δ vs original | P2–P3 | Δ vs original | Most similar |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for method in METHODS:
        scores = method_records[method]["similarities"]
        values: list[str] = []
        for key in PAIR_KEYS:
            values.extend([f"{scores[key]:.6f}", f"{scores[key] - original_scores[key]:+.6f}"])
        pair = " / ".join(method_records[method]["most_similar_pair"])
        lines.append(f"| {method} | {' | '.join(values)} | {pair} |")
    lines.extend(
        [
            "",
            "Methods have different patch counts and therefore different computational costs. No latency was measured.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    image_paths = validate_args(args)
    policy = ResolutionPolicy(
        target_area=args.target_area,
        max_long_edge=args.max_long_edge,
        max_patch_count=args.max_patch_count,
        padding_rgb=tuple(args.padding_rgb),
        interpolation=args.resize_interpolation,
    )
    try:
        validate_policy(policy)
    except PreprocessingError as exc:
        raise PlateExperimentError(str(exc)) from exc
    args.output_dir.mkdir(parents=True)
    set_reproducible_seeds()
    detector = None if args.cropped_plates else load_plate_detector(args.yolo_weights)

    originals: list[np.ndarray] = []
    crops: list[np.ndarray] = []
    crop_boxes: list[tuple[int, int, int, int]] = []
    detections: list[PlateDetection | None] = []
    for index, path in enumerate(image_paths, start=1):
        item_dir = args.output_dir / f"image_{index}"
        item_dir.mkdir()
        original = read_image(path)
        shutil.copy2(path, item_dir / f"original{path.suffix.lower()}")
        if detector is None:
            crop = original.copy()
            height, width = original.shape[:2]
            crop_box = (0, 0, width, height)
            detection = None
        else:
            detection = detect_plate(detector, original, path, args.det_conf)
            crop, crop_box = crop_plate(original, detection.bbox, args.crop_padding)
        save_png(item_dir / "selected_crop.png", crop)
        originals.append(original)
        crops.append(crop)
        crop_boxes.append(crop_box)
        detections.append(detection)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    processor, model = load_embedding_model(
        args.embedding_model,
        device,
        revision=args.revision,
        local_files_only=not args.allow_download,
    )
    try:
        patch_size = derive_patch_size(model)
        if patch_size != (14, 14):
            raise PreprocessingError(
                f"Expected DINOv2-small 14x14 patches, got {patch_size}."
            )
        rectangular_check = verify_rectangular_model_support(model, device, patch_size)
        prepared_by_method: dict[str, list[PreparedInput]] = {method: [] for method in METHODS}
        embeddings_by_method: dict[str, list[torch.Tensor]] = {method: [] for method in METHODS}
        embedding_records: dict[str, list[dict[str, Any]]] = {method: [] for method in METHODS}

        for index, crop in enumerate(crops, start=1):
            item_dir = args.output_dir / f"image_{index}"
            original_item = prepare_original_input(crop, processor)
            add_original_patch_metadata(original_item, patch_size)
            native_item = prepare_variable_input(crop, processor, "native", patch_size, policy)
            budget_item = prepare_variable_input(crop, processor, "budget", patch_size, policy)
            for method, item in (
                ("original", original_item),
                ("native", native_item),
                ("budget", budget_item),
            ):
                embedding, embedding_record = extract_embedding_from_tensor(
                    item.pixel_values, model, device
                )
                if embedding.numel() != 384:
                    raise PreprocessingError(
                        f"{method} returned {embedding.numel()} dimensions; expected 384."
                    )
                prepared_by_method[method].append(item)
                embeddings_by_method[method].append(embedding)
                embedding_records[method].append(embedding_record)
                save_png(item_dir / f"{method}_resized.png", item.resized_bgr)
                save_png(item_dir / f"{method}_padded_or_cropped.png", item.padded_bgr)
                save_png(item_dir / f"{method}_tensor_display.png", item.display_bgr)
            create_crop_stage_figure(
                item_dir / "crop_stages.png",
                image_paths[index - 1],
                originals[index - 1],
                crop,
                crop_boxes[index - 1],
                detections[index - 1],
                {method: prepared_by_method[method][-1] for method in METHODS},
            )
    except PreprocessingError as exc:
        raise PlateExperimentError(str(exc)) from exc

    method_records: dict[str, dict[str, Any]] = {}
    original_scores: dict[str, float] | None = None
    for method in METHODS:
        matrix = compute_similarity_matrix(embeddings_by_method[method])
        best = find_most_similar_pair(matrix)
        scores = similarities_record(matrix)
        if original_scores is None:
            original_scores = scores
        first, second, best_score = best
        method_records[method] = {
            "similarities": scores,
            "differences_from_original": {
                key: scores[key] - original_scores[key] for key in PAIR_KEYS
            },
            "most_similar_pair": [f"plate_{first + 1}", f"plate_{second + 1}"],
            "most_similar_score": best_score,
            "per_image": [
                {
                    "input_index": index,
                    "preprocessing": item.metadata,
                    "embedding": embedding_record,
                }
                for index, (item, embedding_record) in enumerate(
                    zip(prepared_by_method[method], embedding_records[method]), start=1
                )
            ],
        }

    create_overall_figure(
        args.output_dir / "overall_comparison.png",
        crops,
        prepared_by_method,
        method_records,
    )
    write_markdown_table(args.output_dir / "comparison_table.md", method_records)

    results = {
        "schema_version": 1,
        "input_mode": "cropped_plates" if detector is None else "yolo_detection",
        "inputs": [
            {
                "index": index,
                "path": str(path),
                "sha256": sha256_file(path),
                "source_dimensions_hw": list(original.shape[:2]),
                "detection": (
                    {"status": "skipped", "reason": "--cropped-plates supplied"}
                    if detection is None
                    else {
                        "status": "performed",
                        "bbox_xyxy": list(detection.bbox),
                        "confidence": detection.confidence,
                        "detections_found": detection.detection_count,
                    }
                ),
                "selected_crop_bbox_xyxy": list(crop_box),
                "selected_crop_dimensions_hw": list(crop.shape[:2]),
                "crop_stage_figure": str(args.output_dir / f"image_{index}" / "crop_stages.png"),
            }
            for index, (path, original, detection, crop_box, crop) in enumerate(
                zip(image_paths, originals, detections, crop_boxes, crops), start=1
            )
        ],
        "model": {
            "identifier": args.embedding_model,
            "requested_revision": args.revision,
            "resolved_revision": getattr(model.config, "_commit_hash", None),
            "class": f"{type(model).__module__}.{type(model).__name__}",
            "device": str(device),
            "patch_size_hw": list(patch_size),
            "hidden_size": int(model.config.hidden_size),
            "rectangular_positional_interpolation": rectangular_check,
        },
        "processor": processor_settings(processor),
        "budget_resolution_policy": policy_record(policy),
        "rounding_and_padding": {
            "resize": "round-half-up independently after a shared proportional scale",
            "patch_alignment": "ceil each dimension to the next multiple of configured patch size",
            "centering": "floor half to left/top; any odd extra pixel goes right/bottom",
        },
        "methods": method_records,
        "dependency_versions": package_versions(),
        "downloads_required": False if not args.allow_download else "allowed; not measured by tool",
        "latency": {
            "measured": False,
            "note": "Patch counts differ, so computational costs differ by method.",
        },
        "interpretation_warning": FINAL_MESSAGE,
    }
    with (args.output_dir / "comparison_results.json").open("w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2)
        handle.write("\n")
    print((args.output_dir / "comparison_table.md").read_text(encoding="utf-8"))
    print(f"Comparison written to: {args.output_dir.resolve()}")


def main() -> int:
    try:
        run(parse_args())
        return 0
    except PlateExperimentError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted by user.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
