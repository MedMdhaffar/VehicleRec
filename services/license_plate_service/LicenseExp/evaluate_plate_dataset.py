#!/usr/bin/env python3
"""Evaluate all pairwise DINOv2 similarities in a labeled plate-crop tree."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import sys
from collections import Counter, defaultdict
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
    read_image,
    save_png,
    set_reproducible_seeds,
    sha256_file,
)
from plate_preprocessing import (
    INTERPOLATIONS,
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
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare every pair of labeled plate crops under three preprocessing methods."
    )
    parser.add_argument("--dataset-dir", type=Path, default=Path("plates"))
    parser.add_argument("--output-dir", type=Path, default=Path("plate_dataset_evaluation"))
    parser.add_argument(
        "--controlled-manifest",
        type=Path,
        help=(
            "Run the controlled numbered-image experiment from this explicit manifest "
            "instead of legacy category-folder discovery."
        ),
    )
    parser.add_argument(
        "--baseline-only",
        action="store_true",
        help="With --controlled-manifest, evaluate only historical P0 original/native/budget.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the controlled numbered-image manifest and write no embeddings.",
    )
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
    return parser.parse_args()


def discover_dataset(dataset_dir: Path) -> list[dict[str, Any]]:
    if not dataset_dir.is_dir():
        raise PlateExperimentError(f"Dataset directory does not exist: {dataset_dir}")
    records: list[dict[str, Any]] = []
    categories = sorted(path for path in dataset_dir.iterdir() if path.is_dir())
    for category_dir in categories:
        category_paths = sorted(
            path
            for path in category_dir.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        )
        if not category_paths:
            raise PlateExperimentError(f"Category has no supported images: {category_dir}")
        for category_index, path in enumerate(category_paths, start=1):
            image = read_image(path)
            records.append(
                {
                    "index": len(records),
                    "display_id": f"{category_dir.name}:{category_index}",
                    "category": category_dir.name,
                    "path": path,
                    "sha256": sha256_file(path),
                    "image": image,
                    "dimensions_hw": list(image.shape[:2]),
                }
            )
    if len(records) < 2:
        raise PlateExperimentError("At least two dataset images are required.")
    return records


def summary(values: Sequence[float]) -> dict[str, float | int]:
    array = np.asarray(values, dtype=np.float64)
    return {
        "count": int(array.size),
        "mean": float(array.mean()),
        "standard_deviation": float(array.std(ddof=0)),
        "median": float(np.median(array)),
        "minimum": float(array.min()),
        "maximum": float(array.max()),
    }


def similarity_matrix(embeddings: Sequence[torch.Tensor]) -> np.ndarray:
    if len(embeddings) < 2:
        raise PlateExperimentError("At least two embeddings are required.")
    stacked = torch.stack(list(embeddings)).float()
    stacked = torch.nn.functional.normalize(stacked, p=2, dim=1)
    return (stacked @ stacked.T).cpu().numpy()


def pairwise_auc(positive: Sequence[float], negative: Sequence[float]) -> float:
    wins = 0.0
    comparisons = 0
    for positive_score in positive:
        for negative_score in negative:
            comparisons += 1
            if positive_score > negative_score:
                wins += 1.0
            elif positive_score == negative_score:
                wins += 0.5
    return wins / comparisons


def matrix_to_csv(path: Path, labels: Sequence[str], matrix: np.ndarray) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["image", *labels])
        for label, row in zip(labels, matrix):
            writer.writerow([label, *(f"{float(value):.9f}" for value in row)])


def write_pairs_csv(path: Path, pairs: Sequence[dict[str, Any]]) -> None:
    columns = [
        "pair_index",
        "image_1_id",
        "image_2_id",
        "category_1",
        "category_2",
        "image_1_path",
        "image_2_path",
        "same_category",
        "original_similarity",
        "native_similarity",
        "budget_similarity",
        "native_delta_from_original",
        "budget_delta_from_original",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(pairs)


def create_heatmaps(
    path: Path, labels: Sequence[str], matrices: dict[str, np.ndarray]
) -> None:
    figure, axes = plt.subplots(1, 3, figsize=(23, 7), constrained_layout=True)
    minimum = min(float(matrix.min()) for matrix in matrices.values())
    for axis, method in zip(axes, METHODS):
        matrix = matrices[method]
        shown = axis.imshow(matrix, vmin=minimum, vmax=1.0, cmap="viridis")
        axis.set_title(f"{method} preprocessing")
        axis.set_xticks(range(len(labels)), labels, rotation=55, ha="right")
        axis.set_yticks(range(len(labels)), labels)
        for row in range(len(labels)):
            for column in range(len(labels)):
                axis.text(
                    column,
                    row,
                    f"{matrix[row, column]:.2f}",
                    ha="center",
                    va="center",
                    fontsize=7,
                    color="white" if matrix[row, column] < 0.72 else "black",
                )
        figure.colorbar(shown, ax=axis, fraction=0.046, pad=0.04)
    figure.suptitle("All-pair DINOv2 cosine similarity", fontsize=17, fontweight="bold")
    figure.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(figure)


def create_distribution_figure(
    path: Path, method_records: dict[str, dict[str, Any]]
) -> None:
    figure, axes = plt.subplots(1, 3, figsize=(16, 5), constrained_layout=True)
    rng = np.random.default_rng(0)
    for axis, method in zip(axes, METHODS):
        same = np.asarray(method_records[method]["same_category_scores"])
        different = np.asarray(method_records[method]["different_category_scores"])
        axis.boxplot([same, different], tick_labels=["same", "different"], showmeans=True)
        axis.scatter(
            1 + rng.normal(0, 0.035, len(same)), same, alpha=0.8, label=f"same (n={len(same)})"
        )
        axis.scatter(
            2 + rng.normal(0, 0.035, len(different)), different, alpha=0.55, label=f"different (n={len(different)})"
        )
        axis.set_title(f"{method}\nAUC={method_records[method]['same_vs_different_roc_auc']:.3f}")
        axis.set_ylabel("cosine similarity")
        axis.grid(axis="y", alpha=0.25)
        axis.legend(fontsize=8)
    figure.suptitle(
        "Same-category versus different-category pair scores\n"
        "Descriptive only: 10 images, unequal categories, no held-out evaluation",
        fontsize=14,
    )
    figure.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(figure)


def create_input_gallery(
    path: Path,
    records: Sequence[dict[str, Any]],
    prepared: dict[str, list[Any]],
) -> None:
    figure, axes = plt.subplots(len(records), 4, figsize=(16, 3.1 * len(records)), constrained_layout=True)
    headers = ("plate crop", "original square", "native rectangular", "budget rectangular")
    for row, record in enumerate(records):
        images = [
            record["image"],
            prepared["original"][row].display_bgr,
            prepared["native"][row].display_bgr,
            prepared["budget"][row].display_bgr,
        ]
        for column, (header, image) in enumerate(zip(headers, images)):
            axis = axes[row, column]
            axis.imshow(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
            axis.set_title(
                f"{record['display_id']} | {header}\n{image.shape[1]}×{image.shape[0]}",
                fontsize=9,
            )
            axis.axis("off")
    figure.suptitle("Plate crops and exact-tensor display reconstructions", fontsize=16)
    figure.savefig(path, dpi=140, bbox_inches="tight")
    plt.close(figure)


def nearest_neighbor_records(
    matrix: np.ndarray, records: Sequence[dict[str, Any]]
) -> tuple[list[dict[str, Any]], float]:
    results = []
    matches = 0
    for index, record in enumerate(records):
        scores = matrix[index].copy()
        scores[index] = -np.inf
        neighbor_index = int(np.argmax(scores))
        neighbor = records[neighbor_index]
        category_match = record["category"] == neighbor["category"]
        matches += int(category_match)
        results.append(
            {
                "query": record["display_id"],
                "query_category": record["category"],
                "nearest_neighbor": neighbor["display_id"],
                "neighbor_category": neighbor["category"],
                "similarity": float(scores[neighbor_index]),
                "category_match": category_match,
            }
        )
    return results, matches / len(records)


def write_report(
    path: Path,
    category_counts: Counter[str],
    method_records: dict[str, dict[str, Any]],
    pairs: Sequence[dict[str, Any]],
) -> None:
    lines = [
        "# Plate Dataset Similarity Evaluation",
        "",
        "This is a descriptive frozen-embedding comparison, not a trained classifier or a held-out accuracy study.",
        "",
        "## Dataset",
        "",
        f"- Images: {sum(category_counts.values())}",
        f"- Categories: {len(category_counts)}",
        f"- Unique pairs: {len(pairs)}",
        f"- Same-category pairs: {sum(pair['same_category'] for pair in pairs)}",
        f"- Different-category pairs: {sum(not pair['same_category'] for pair in pairs)}",
        f"- Counts: {', '.join(f'{key}={value}' for key, value in sorted(category_counts.items()))}",
        "",
        "## Summary",
        "",
        "| Method | Same mean | Different mean | Mean gap | ROC-AUC | Nearest-neighbor label match |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for method in METHODS:
        record = method_records[method]
        same_mean = record["same_category_summary"]["mean"]
        different_mean = record["different_category_summary"]["mean"]
        lines.append(
            f"| {method} | {same_mean:.6f} | {different_mean:.6f} | "
            f"{same_mean - different_mean:+.6f} | "
            f"{record['same_vs_different_roc_auc']:.6f} | "
            f"{record['nearest_neighbor_category_match_rate']:.3f} |"
        )
    lines.extend(
        [
            "",
            "Higher similarity is not accuracy. ROC-AUC here only measures how the 8 same-category pair scores rank against the 37 different-category scores in this small, reused dataset. Nearest-neighbor label match is in-sample retrieval, not generalization.",
            "",
            "## Highest-scoring pairs by method",
            "",
        ]
    )
    for method in METHODS:
        lines.append(f"### {method}")
        lines.append("")
        lines.append("| Pair | Categories | Same category | Similarity |")
        lines.append("|---|---|---:|---:|")
        key = f"{method}_similarity"
        for pair in sorted(pairs, key=lambda item: item[key], reverse=True)[:10]:
            lines.append(
                f"| {pair['image_1_id']} / {pair['image_2_id']} | "
                f"{pair['category_1']} / {pair['category_2']} | "
                f"{pair['same_category']} | {pair[key]:.6f} |"
            )
        lines.append("")
    lines.extend(
        [
            "## Limitations",
            "",
            "The dataset has only 10 images, categories contain two or three examples, and crops may share capture conditions or vehicles. Results can be dominated by plate color, layout, resolution, framing, or padding. Positional interpolation preserves rectangular grids but does not make embeddings invariant to resolution or crop context. Use more independent images and a held-out protocol before drawing category-level conclusions.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    if args.controlled_manifest is not None:
        from controlled_plate_evaluation import run_controlled

        run_controlled(args)
        return
    if args.baseline_only or args.validate_only:
        raise PlateExperimentError(
            "--baseline-only and --validate-only require --controlled-manifest."
        )
    if args.output_dir.exists():
        raise PlateExperimentError(
            f"Output directory already exists; refusing to overwrite it: {args.output_dir}"
        )
    records = discover_dataset(args.dataset_dir)
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
    inputs_dir = args.output_dir / "model_inputs"
    for method in METHODS:
        (inputs_dir / method).mkdir(parents=True)

    set_reproducible_seeds()
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
            raise PreprocessingError(f"Expected 14x14 patches, got {patch_size}.")
        rectangular_check = verify_rectangular_model_support(model, device, patch_size)
        prepared: dict[str, list[Any]] = {method: [] for method in METHODS}
        embeddings: dict[str, list[torch.Tensor]] = {method: [] for method in METHODS}
        embedding_metadata: dict[str, list[dict[str, Any]]] = {
            method: [] for method in METHODS
        }
        for number, record in enumerate(records, start=1):
            original = prepare_original_input(record["image"], processor)
            add_original_patch_metadata(original, patch_size)
            native = prepare_variable_input(record["image"], processor, "native", patch_size, policy)
            budget = prepare_variable_input(record["image"], processor, "budget", patch_size, policy)
            for method, item in zip(METHODS, (original, native, budget)):
                embedding, metadata = extract_embedding_from_tensor(
                    item.pixel_values, model, device
                )
                if embedding.numel() != 384:
                    raise PreprocessingError(
                        f"{record['path']} produced {embedding.numel()} dimensions; expected 384."
                    )
                prepared[method].append(item)
                embeddings[method].append(embedding)
                embedding_metadata[method].append(metadata)
                save_png(
                    inputs_dir / method / f"{record['display_id'].replace(':', '_')}.png",
                    item.display_bgr,
                )
            print(f"[{number}/{len(records)}] embedded {record['display_id']}: {record['path']}")
    except PreprocessingError as exc:
        raise PlateExperimentError(str(exc)) from exc

    matrices = {method: similarity_matrix(embeddings[method]) for method in METHODS}
    labels = [record["display_id"] for record in records]
    for method in METHODS:
        matrix_to_csv(args.output_dir / f"similarity_matrix_{method}.csv", labels, matrices[method])

    pairs: list[dict[str, Any]] = []
    for first in range(len(records)):
        for second in range(first + 1, len(records)):
            first_record, second_record = records[first], records[second]
            original_score = float(matrices["original"][first, second])
            native_score = float(matrices["native"][first, second])
            budget_score = float(matrices["budget"][first, second])
            pairs.append(
                {
                    "pair_index": len(pairs) + 1,
                    "image_1_id": first_record["display_id"],
                    "image_2_id": second_record["display_id"],
                    "category_1": first_record["category"],
                    "category_2": second_record["category"],
                    "image_1_path": str(first_record["path"]),
                    "image_2_path": str(second_record["path"]),
                    "same_category": first_record["category"] == second_record["category"],
                    "original_similarity": original_score,
                    "native_similarity": native_score,
                    "budget_similarity": budget_score,
                    "native_delta_from_original": native_score - original_score,
                    "budget_delta_from_original": budget_score - original_score,
                }
            )
    write_pairs_csv(args.output_dir / "all_pairs.csv", pairs)

    method_records: dict[str, dict[str, Any]] = {}
    for method in METHODS:
        key = f"{method}_similarity"
        same_scores = [pair[key] for pair in pairs if pair["same_category"]]
        different_scores = [pair[key] for pair in pairs if not pair["same_category"]]
        nearest, nearest_rate = nearest_neighbor_records(matrices[method], records)
        category_scores: dict[str, list[float]] = defaultdict(list)
        for pair in pairs:
            if pair["same_category"]:
                category_scores[pair["category_1"]].append(pair[key])
        method_records[method] = {
            "same_category_scores": same_scores,
            "different_category_scores": different_scores,
            "same_category_summary": summary(same_scores),
            "different_category_summary": summary(different_scores),
            "same_vs_different_mean_gap": statistics.mean(same_scores)
            - statistics.mean(different_scores),
            "same_vs_different_roc_auc": pairwise_auc(same_scores, different_scores),
            "nearest_neighbors": nearest,
            "nearest_neighbor_category_match_rate": nearest_rate,
            "within_category_summaries": {
                category: summary(values) for category, values in category_scores.items()
            },
        }

    create_heatmaps(args.output_dir / "similarity_heatmaps.png", labels, matrices)
    create_distribution_figure(
        args.output_dir / "same_vs_different_distributions.png", method_records
    )
    create_input_gallery(args.output_dir / "preprocessing_gallery.png", records, prepared)

    category_counts = Counter(record["category"] for record in records)
    result = {
        "schema_version": 1,
        "dataset": {
            "directory": str(args.dataset_dir),
            "image_count": len(records),
            "category_counts": dict(category_counts),
            "unique_pair_count": len(pairs),
            "same_category_pair_count": sum(pair["same_category"] for pair in pairs),
            "different_category_pair_count": sum(not pair["same_category"] for pair in pairs),
            "images": [
                {
                    key: value
                    for key, value in record.items()
                    if key not in {"image", "path"}
                }
                | {"path": str(record["path"])}
                for record in records
            ],
        },
        "model": {
            "identifier": args.embedding_model,
            "requested_revision": args.revision,
            "resolved_revision": getattr(model.config, "_commit_hash", None),
            "device": str(device),
            "patch_size_hw": list(patch_size),
            "hidden_size": int(model.config.hidden_size),
            "rectangular_positional_interpolation": rectangular_check,
        },
        "processor": processor_settings(processor),
        "budget_policy": policy_record(policy),
        "methods": {
            method: {
                **method_records[method],
                "per_image": [
                    {
                        "image_id": record["display_id"],
                        "preprocessing": item.metadata,
                        "embedding": metadata,
                    }
                    for record, item, metadata in zip(
                        records, prepared[method], embedding_metadata[method]
                    )
                ],
            }
            for method in METHODS
        },
        "pairs": pairs,
        "limitations": (
            "Descriptive in-sample results for 10 images with unequal category sizes; "
            "not a trained classifier, held-out accuracy result, or proof of generalization."
        ),
    }
    with (args.output_dir / "evaluation_results.json").open("w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    write_report(
        args.output_dir / "EVALUATION_REPORT.md", category_counts, method_records, pairs
    )
    print(f"Evaluation written to: {args.output_dir.resolve()}")


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
