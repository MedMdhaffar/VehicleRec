#!/usr/bin/env python3
"""Compare DINOv2 CLS embeddings for three complete license-plate crops."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import cv2
import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import torch
from transformers import AutoImageProcessor, AutoModel
from ultralytics import YOLO

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


DEFAULT_EMBEDDING_MODEL = "facebook/dinov2-small"
FINAL_MESSAGE = (
    "Cosine similarities rank these three frozen visual embeddings only. They do "
    "not establish license-plate category identity, classification accuracy, or "
    "that one preprocessing policy is more accurate than another."
)


class PlateExperimentError(RuntimeError):
    """An expected, user-actionable experiment failure."""


@dataclass(frozen=True)
class PlateDetection:
    confidence: float
    bbox: tuple[float, float, float, float]
    detection_count: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare DINOv2 embeddings using complete rectangular plate inputs "
            "or the explicit historical processor mode."
        )
    )
    parser.add_argument("--image1", required=True, type=Path)
    parser.add_argument("--image2", required=True, type=Path)
    parser.add_argument("--image3", required=True, type=Path)
    parser.add_argument("--yolo-weights", type=Path)
    parser.add_argument(
        "--cropped-plates",
        action="store_true",
        help="Use each entire source as a selected plate crop and skip YOLO.",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("variable_outputs"))
    parser.add_argument("--crop-padding", type=float, default=0.05)
    parser.add_argument("--det-conf", type=float, default=0.25)
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--revision")
    parser.add_argument(
        "--allow-download",
        action="store_true",
        help="Allow model downloads; the default uses local cache files only.",
    )
    parser.add_argument(
        "--preprocessing-mode",
        choices=("budget", "native", "original"),
        default="budget",
        help=(
            "budget (default): proportional target-area resize; native: no resize; "
            "original: unchanged model processor with square center crop."
        ),
    )
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


def set_reproducible_seeds(seed: int = 0) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def validate_args(args: argparse.Namespace) -> list[Path]:
    image_paths = [args.image1, args.image2, args.image3]
    for path in image_paths:
        if not path.is_file():
            raise PlateExperimentError(f"Image file does not exist: {path}")
    if args.cropped_plates and args.yolo_weights is not None:
        raise PlateExperimentError("Do not combine --cropped-plates with --yolo-weights.")
    if not args.cropped_plates:
        if args.yolo_weights is None or not args.yolo_weights.is_file():
            raise PlateExperimentError(
                "--yolo-weights is required unless --cropped-plates is used."
            )
    if not np.isfinite(args.crop_padding) or args.crop_padding < 0:
        raise PlateExperimentError("--crop-padding must be finite and non-negative.")
    if not np.isfinite(args.det_conf) or not 0 <= args.det_conf <= 1:
        raise PlateExperimentError("--det-conf must be between 0 and 1 inclusive.")
    if not args.embedding_model.strip():
        raise PlateExperimentError("--embedding-model cannot be empty.")
    if args.output_dir.exists():
        raise PlateExperimentError(
            f"Output directory already exists; refusing to overwrite it: {args.output_dir}"
        )
    return image_paths


def read_image(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise PlateExperimentError(f"Could not decode image: {path}")
    return image


def load_plate_detector(weights_path: Path) -> YOLO:
    try:
        return YOLO(str(weights_path))
    except Exception as exc:
        raise PlateExperimentError(
            f"Could not load YOLO weights from '{weights_path}': {exc}"
        ) from exc


def detect_plate(
    detector: YOLO,
    image: np.ndarray,
    image_path: Path,
    confidence_threshold: float,
) -> PlateDetection:
    try:
        results = detector.predict(source=image, conf=confidence_threshold, verbose=False)
    except Exception as exc:
        raise PlateExperimentError(f"YOLO inference failed for '{image_path}': {exc}") from exc
    if not results or results[0].boxes is None or len(results[0].boxes) == 0:
        raise PlateExperimentError(
            f"No plate detected in '{image_path}' at confidence {confidence_threshold:.3f}."
        )
    boxes = results[0].boxes
    confidences = boxes.conf.detach().float().cpu()
    if not torch.isfinite(confidences).all():
        raise PlateExperimentError(f"YOLO returned non-finite confidence for: {image_path}")
    best_index = int(torch.argmax(confidences).item())
    bbox = boxes.xyxy[best_index].detach().cpu().tolist()
    if len(bbox) != 4 or not np.all(np.isfinite(bbox)):
        raise PlateExperimentError(f"YOLO returned an invalid box for: {image_path}")
    return PlateDetection(
        float(confidences[best_index].item()),
        tuple(float(value) for value in bbox),
        len(boxes),
    )


def crop_plate(
    image: np.ndarray, bbox: Sequence[float], padding: float
) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    height, width = image.shape[:2]
    x1, y1, x2, y2 = bbox
    if x2 <= x1 or y2 <= y1:
        raise PlateExperimentError(f"Detected box has non-positive size: {bbox}")
    pad_x, pad_y = (x2 - x1) * padding, (y2 - y1) * padding
    crop_x1 = max(0, int(np.floor(x1 - pad_x)))
    crop_y1 = max(0, int(np.floor(y1 - pad_y)))
    crop_x2 = min(width, int(np.ceil(x2 + pad_x)))
    crop_y2 = min(height, int(np.ceil(y2 + pad_y)))
    if crop_x2 <= crop_x1 or crop_y2 <= crop_y1:
        raise PlateExperimentError(f"Padded box lies outside image: {bbox}")
    return (
        image[crop_y1:crop_y2, crop_x1:crop_x2].copy(),
        (crop_x1, crop_y1, crop_x2, crop_y2),
    )


def draw_detection(
    image: np.ndarray, detection: PlateDetection, plate_number: int
) -> np.ndarray:
    annotated = image.copy()
    height, width = image.shape[:2]
    x1, y1, x2, y2 = detection.bbox
    point1 = (max(0, min(width - 1, round(x1))), max(0, min(height - 1, round(y1))))
    point2 = (max(0, min(width - 1, round(x2))), max(0, min(height - 1, round(y2))))
    line_width = max(2, round(min(height, width) / 350))
    cv2.rectangle(annotated, point1, point2, (0, 255, 0), line_width)
    cv2.putText(
        annotated,
        f"Plate {plate_number}: {detection.confidence:.3f}",
        (point1[0], max(20, point1[1] - 5)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (0, 255, 0),
        line_width,
        cv2.LINE_AA,
    )
    return annotated


def load_embedding_model(
    model_name: str,
    device: torch.device,
    revision: str | None = None,
    local_files_only: bool = False,
):
    kwargs: dict[str, Any] = {"local_files_only": local_files_only}
    if revision:
        kwargs["revision"] = revision
    try:
        processor = AutoImageProcessor.from_pretrained(model_name, **kwargs)
        model = AutoModel.from_pretrained(model_name, **kwargs)
        model.to(device)
        model.eval()
        return processor, model
    except Exception as exc:
        raise PlateExperimentError(f"Could not load model '{model_name}': {exc}") from exc


def compute_similarity_matrix(embeddings: Sequence[torch.Tensor]) -> np.ndarray:
    if len(embeddings) != 3:
        raise ValueError("Exactly three embeddings are required.")
    stacked = torch.stack(list(embeddings)).float()
    stacked = torch.nn.functional.normalize(stacked, p=2, dim=1)
    return (stacked @ stacked.T).cpu().numpy()


def find_most_similar_pair(matrix: np.ndarray) -> tuple[int, int, float]:
    if matrix.shape != (3, 3):
        raise ValueError("Expected a 3x3 similarity matrix.")
    rows, columns = np.triu_indices(3, k=1)
    best = int(np.argmax(matrix[rows, columns]))
    return int(rows[best]), int(columns[best]), float(matrix[rows[best], columns[best]])


def print_similarity_matrix(matrix: np.ndarray) -> None:
    print("\nCosine similarity matrix\n")
    print(f"{'':10s}{'Plate 1':>10s}{'Plate 2':>10s}{'Plate 3':>10s}")
    for row in range(3):
        values = "".join(f"{matrix[row, column]:10.3f}" for column in range(3))
        print(f"Plate {row + 1:<4d}{values}")


def save_png(path: Path, image: np.ndarray) -> None:
    if not cv2.imwrite(str(path), image, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
        raise PlateExperimentError(f"Failed to save image: {path}")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def processor_settings(processor: Any) -> dict[str, Any]:
    return {
        "class": f"{type(processor).__module__}.{type(processor).__name__}",
        "do_convert_rgb": processor.do_convert_rgb,
        "do_resize_default": processor.do_resize,
        "size_default": str(processor.size),
        "do_center_crop_default": processor.do_center_crop,
        "crop_size_default": str(processor.crop_size),
        "do_rescale": processor.do_rescale,
        "rescale_factor": processor.rescale_factor,
        "do_normalize": processor.do_normalize,
        "image_mean_rgb": list(processor.image_mean),
        "image_std_rgb": list(processor.image_std),
    }


def create_summary_figure(
    path: Path,
    crops: Sequence[np.ndarray],
    prepared: Sequence[PreparedInput],
    matrix: np.ndarray,
    best_pair: tuple[int, int, float],
    mode: str,
) -> None:
    figure, axes = plt.subplots(2, 3, figsize=(15, 8), constrained_layout=True)
    for index, (crop, item) in enumerate(zip(crops, prepared)):
        axes[0, index].imshow(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
        axes[0, index].set_title(f"Plate {index + 1} crop: {crop.shape[1]}×{crop.shape[0]}")
        axes[0, index].axis("off")
        display = item.display_bgr
        axes[1, index].imshow(cv2.cvtColor(display, cv2.COLOR_BGR2RGB))
        grid = item.metadata["patch_grid_hw"]
        axes[1, index].set_title(
            f"{mode} tensor view: {display.shape[1]}×{display.shape[0]}\n"
            f"grid {grid[1]}×{grid[0]}, {item.metadata['patch_count']} patches"
        )
        axes[1, index].axis("off")
    first, second, score = best_pair
    figure.suptitle(
        f"{mode} preprocessing | P1-P2 {matrix[0,1]:.3f}, "
        f"P1-P3 {matrix[0,2]:.3f}, P2-P3 {matrix[1,2]:.3f}\n"
        f"Most similar: Plate {first + 1} / Plate {second + 1} ({score:.3f})",
        fontsize=14,
    )
    figure.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(figure)


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
    set_reproducible_seeds()
    args.output_dir.mkdir(parents=True)
    detector = None if args.cropped_plates else load_plate_detector(args.yolo_weights)

    originals: list[np.ndarray] = []
    crops: list[np.ndarray] = []
    crop_boxes: list[tuple[int, int, int, int]] = []
    detections: list[PlateDetection | None] = []
    for index, path in enumerate(image_paths, start=1):
        original = read_image(path)
        if detector is None:
            crop = original.copy()
            height, width = original.shape[:2]
            crop_box = (0, 0, width, height)
            detection = None
        else:
            detection = detect_plate(detector, original, path, args.det_conf)
            crop, crop_box = crop_plate(original, detection.bbox, args.crop_padding)
            save_png(
                args.output_dir / f"detection_{index}.png",
                draw_detection(original, detection, index),
            )
        save_png(args.output_dir / f"plate_{index}.png", crop)
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
                f"Expected DINOv2-small to report 14x14 patches, got {patch_size}."
            )
        rectangular_check = verify_rectangular_model_support(model, device, patch_size)
        prepared: list[PreparedInput] = []
        embeddings: list[torch.Tensor] = []
        embedding_records: list[dict[str, Any]] = []
        for index, crop in enumerate(crops, start=1):
            if args.preprocessing_mode == "original":
                item = prepare_original_input(crop, processor)
                add_original_patch_metadata(item, patch_size)
            else:
                item = prepare_variable_input(
                    crop, processor, args.preprocessing_mode, patch_size, policy
                )
            embedding, embedding_record = extract_embedding_from_tensor(
                item.pixel_values, model, device
            )
            if embedding.numel() != 384:
                raise PreprocessingError(
                    f"Expected a 384-dimensional DINOv2-small embedding, got {embedding.numel()}."
                )
            save_png(args.output_dir / f"resized_{index}.png", item.resized_bgr)
            save_png(args.output_dir / f"padded_or_cropped_{index}.png", item.padded_bgr)
            save_png(args.output_dir / f"model_input_display_{index}.png", item.display_bgr)
            prepared.append(item)
            embeddings.append(embedding)
            embedding_records.append(embedding_record)
    except PreprocessingError as exc:
        raise PlateExperimentError(str(exc)) from exc

    matrix = compute_similarity_matrix(embeddings)
    best_pair = find_most_similar_pair(matrix)
    print_similarity_matrix(matrix)
    create_summary_figure(
        args.output_dir / "comparison.png",
        crops,
        prepared,
        matrix,
        best_pair,
        args.preprocessing_mode,
    )
    first, second, score = best_pair
    results = {
        "schema_version": 2,
        "input_mode": "cropped_plates" if detector is None else "yolo_detection",
        "preprocessing_mode": args.preprocessing_mode,
        "inputs": [
            {
                "path": str(path),
                "sha256": sha256_file(path),
                "source_dimensions_hw": list(original.shape[:2]),
                "detection": (
                    {"status": "skipped"}
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
                "preprocessing": item.metadata,
                "embedding": embedding_record,
            }
            for path, original, detection, crop_box, crop, item, embedding_record in zip(
                image_paths,
                originals,
                detections,
                crop_boxes,
                crops,
                prepared,
                embedding_records,
            )
        ],
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
        "resolution_policy": policy_record(policy),
        "similarities": {
            "plate_1_plate_2": float(matrix[0, 1]),
            "plate_1_plate_3": float(matrix[0, 2]),
            "plate_2_plate_3": float(matrix[1, 2]),
        },
        "most_similar_pair": [f"plate_{first + 1}", f"plate_{second + 1}"],
        "most_similar_score": score,
        "interpretation_warning": FINAL_MESSAGE,
    }
    with (args.output_dir / "similarity_results.json").open("w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2)
        handle.write("\n")
    print(f"\nResults saved to: {args.output_dir.resolve()}")
    print(FINAL_MESSAGE)


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
