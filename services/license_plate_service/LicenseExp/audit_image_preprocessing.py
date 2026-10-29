#!/usr/bin/env python3
"""Audit the exact image tensor supplied to the existing DINOv2 model."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import shutil
import sys
from dataclasses import asdict, is_dataclass
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
from transformers import AutoImageProcessor, AutoModel

from compare_plate_embeddings import (
    DEFAULT_EMBEDDING_MODEL,
    PlateDetection,
    PlateExperimentError,
    crop_plate,
    detect_plate,
    load_plate_detector,
    read_image,
    set_reproducible_seeds,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit the exact DINOv2 preprocessing tensor without computing similarities."
    )
    parser.add_argument("--images", required=True, nargs="+", type=Path)
    parser.add_argument(
        "--cropped-plates",
        action="store_true",
        help="Treat every source image as an already-selected plate crop and skip YOLO.",
    )
    parser.add_argument("--yolo-weights", type=Path)
    parser.add_argument("--det-conf", type=float, default=0.25)
    parser.add_argument("--crop-padding", type=float, default=0.05)
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--revision", help="Optional Hugging Face model revision.")
    parser.add_argument(
        "--output-dir", type=Path, default=Path("input_audit_outputs")
    )
    parser.add_argument(
        "--allow-download",
        action="store_true",
        help="Allow Hugging Face downloads. By default, only local cache files are used.",
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if not args.images:
        raise PlateExperimentError("At least one --images path is required.")
    for path in args.images:
        if not path.is_file():
            raise PlateExperimentError(f"Image file does not exist: {path}")
    if args.cropped_plates and args.yolo_weights is not None:
        raise PlateExperimentError(
            "Do not provide --yolo-weights together with --cropped-plates."
        )
    if not args.cropped_plates:
        if args.yolo_weights is None or not args.yolo_weights.is_file():
            raise PlateExperimentError(
                "A readable --yolo-weights file is required unless --cropped-plates is used."
            )
    if not np.isfinite(args.det_conf) or not 0 <= args.det_conf <= 1:
        raise PlateExperimentError("--det-conf must be between 0 and 1 inclusive.")
    if not np.isfinite(args.crop_padding) or args.crop_padding < 0:
        raise PlateExperimentError("--crop-padding must be finite and non-negative.")
    if args.output_dir.exists():
        raise PlateExperimentError(
            f"Audit output directory already exists; refusing to overwrite it: {args.output_dir}"
        )
    protected = [*args.images]
    if args.yolo_weights is not None:
        protected.append(args.yolo_weights)
    if args.output_dir.resolve() in {path.resolve() for path in protected}:
        raise PlateExperimentError("The audit output directory cannot be an input path.")


def package_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
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
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    versions["python"] = sys.version.replace("\n", " ")
    return versions


def json_value(value: Any) -> Any:
    if is_dataclass(value):
        return json_value(asdict(value))
    if hasattr(value, "items"):
        return {key: json_value(item) for key, item in value.items() if item is not None}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def tensor_sha256(tensor: torch.Tensor) -> str:
    array = tensor.detach().cpu().contiguous().numpy()
    return hashlib.sha256(array.tobytes()).hexdigest()


def save_png(path: Path, image_bgr: np.ndarray) -> None:
    if not cv2.imwrite(str(path), image_bgr, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
        raise PlateExperimentError(f"Failed to save image: {path}")


def reconstruct_display(
    pixel_values: torch.Tensor, processor: Any
) -> tuple[np.ndarray, dict[str, Any]]:
    """Reverse normalization/rescaling from the exact model-input tensor for display."""
    if pixel_values.shape[0] != 1 or pixel_values.shape[1] != 3:
        raise PlateExperimentError(
            f"Expected a [1, 3, H, W] pixel tensor, got {list(pixel_values.shape)}"
        )
    display = pixel_values[0].detach().cpu().float().clone()
    if processor.do_normalize:
        mean = torch.tensor(processor.image_mean, dtype=display.dtype)[:, None, None]
        std = torch.tensor(processor.image_std, dtype=display.dtype)[:, None, None]
        display = display * std + mean
    if processor.do_rescale:
        display = display / float(processor.rescale_factor)

    raw_min = float(display.min().item())
    raw_max = float(display.max().item())
    clipped = display.clamp(0, 255)
    clipped_count = int(torch.count_nonzero(display != clipped).item())
    rounded = clipped.round().to(torch.uint8)
    rgb = rounded.permute(1, 2, 0).numpy()
    details = {
        "description": (
            "Display reconstruction made from the exact normalized tensor passed to the model. "
            "Normalization and rescaling were reversed, then values were clipped to [0, 255], "
            "rounded to nearest integers, and stored as uint8 PNG."
        ),
        "pre_clip_range": [raw_min, raw_max],
        "clipped_value_count": clipped_count,
        "rounded_for_display": True,
        "display_dtype": "uint8",
    }
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), details


def retained_geometry(
    crop_shape: Sequence[int], resized_shape: Sequence[int], crop_size: Sequence[int]
) -> dict[str, Any]:
    crop_height, crop_width = int(crop_shape[0]), int(crop_shape[1])
    resized_height, resized_width = int(resized_shape[0]), int(resized_shape[1])
    target_height, target_width = int(crop_size[0]), int(crop_size[1])
    top = int((resized_height - target_height) / 2.0)
    left = int((resized_width - target_width) / 2.0)
    bottom = top + target_height
    right = left + target_width

    scale_x = resized_width / crop_width
    scale_y = resized_height / crop_height
    mapped = [left / scale_x, top / scale_y, right / scale_x, bottom / scale_y]
    clipped = [
        max(0.0, min(float(crop_width), mapped[0])),
        max(0.0, min(float(crop_height), mapped[1])),
        max(0.0, min(float(crop_width), mapped[2])),
        max(0.0, min(float(crop_height), mapped[3])),
    ]
    retained_fraction = (
        max(0.0, clipped[2] - clipped[0])
        * max(0.0, clipped[3] - clipped[1])
        / (crop_width * crop_height)
    )
    return {
        "resized_dimensions_hw": [resized_height, resized_width],
        "center_crop_window_xyxy_in_resized_pixels": [left, top, right, bottom],
        "resize_scale_xy": [scale_x, scale_y],
        "mapped_retained_region_xyxy_in_plate_crop": mapped,
        "mapped_retained_region_clipped_xyxy_in_plate_crop": clipped,
        "mapped_retained_area_fraction": retained_fraction,
        "mapping_note": (
            "The resize dimensions and integer center-crop window are exact for this processor run. "
            "The inverse rectangle uses dimension ratios and is an approximate geometric footprint: "
            "bicubic sampling coordinates and its interpolation kernel can draw information from "
            "neighboring source pixels outside this rectangle."
        ),
    }


def retained_overlay(image: np.ndarray, box: Sequence[float]) -> np.ndarray:
    height, width = image.shape[:2]
    x1, y1, x2, y2 = box
    ix1 = max(0, min(width, int(np.floor(x1))))
    iy1 = max(0, min(height, int(np.floor(y1))))
    ix2 = max(0, min(width, int(np.ceil(x2))))
    iy2 = max(0, min(height, int(np.ceil(y2))))
    overlay = image.copy()
    excluded = np.full_like(image, (32, 32, 190))
    overlay = cv2.addWeighted(overlay, 0.55, excluded, 0.45, 0)
    overlay[iy1:iy2, ix1:ix2] = image[iy1:iy2, ix1:ix2]
    cv2.rectangle(
        overlay,
        (max(0, ix1), max(0, iy1)),
        (max(0, ix2 - 1), max(0, iy2 - 1)),
        (0, 220, 0),
        max(1, int(round(min(height, width) / 80))),
    )
    return overlay


def original_retained_box(
    crop_box: Sequence[int], retained_box: Sequence[float]
) -> list[float]:
    return [
        crop_box[0] + retained_box[0],
        crop_box[1] + retained_box[1],
        crop_box[0] + retained_box[2],
        crop_box[1] + retained_box[3],
    ]


def create_stage_figure(
    output_path: Path,
    original: np.ndarray,
    crop: np.ndarray,
    crop_overlay: np.ndarray,
    model_display: np.ndarray,
    source_label: str,
    mode_label: str,
) -> None:
    images = [original, crop, crop_overlay, model_display]
    titles = [
        f"Original source\n{original.shape[1]}×{original.shape[0]}",
        f"Selected plate crop\n{crop.shape[1]}×{crop.shape[0]}",
        "Retained mapping\ngreen=retained, red=excluded",
        f"Model-input display reconstruction\n{model_display.shape[1]}×{model_display.shape[0]}",
    ]
    figure, axes = plt.subplots(1, 4, figsize=(20, 5), constrained_layout=True)
    for axis, image, title in zip(axes, images, titles):
        axis.imshow(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        axis.set_title(title)
        axis.axis("off")
    figure.suptitle(
        f"Input audit: {source_label} ({mode_label})\n"
        "Display reconstruction reverses normalization/rescaling; it is clipped/rounded uint8, not the tensor.",
        fontsize=14,
    )
    figure.savefig(output_path, dpi=160, bbox_inches="tight")
    plt.close(figure)


def processor_record(
    processor: Any,
    model: Any,
    args: argparse.Namespace,
    versions: dict[str, str | None],
) -> dict[str, Any]:
    resample_value = int(processor.resample)
    resample_names = {0: "nearest", 1: "lanczos", 2: "bilinear", 3: "bicubic", 4: "box", 5: "hamming"}
    return {
        "requested_model_identifier": args.embedding_model,
        "requested_revision": args.revision,
        "resolved_model_revision": getattr(model.config, "_commit_hash", None),
        "processor_class": f"{type(processor).__module__}.{type(processor).__name__}",
        "processor_name_or_path": getattr(processor, "name_or_path", None),
        "model_name_or_path": getattr(model, "name_or_path", None),
        "local_files_only": not args.allow_download,
        "backend_mro": [f"{item.__module__}.{item.__name__}" for item in type(processor).__mro__],
        "settings": {
            "do_convert_rgb": processor.do_convert_rgb,
            "do_resize": processor.do_resize,
            "size": json_value(processor.size),
            "resample_integer": resample_value,
            "resample_name": resample_names.get(resample_value, "unknown"),
            "resize_antialias": True,
            "do_center_crop": processor.do_center_crop,
            "crop_size": json_value(processor.crop_size),
            "do_rescale": processor.do_rescale,
            "rescale_factor": processor.rescale_factor,
            "do_normalize": processor.do_normalize,
            "image_mean_rgb": list(processor.image_mean),
            "image_std_rgb": list(processor.image_std),
        },
        "confirmed_operation_order": [
            "The experiment converts OpenCV BGR to an RGB PIL image.",
            "The processor converts to RGB (already RGB here) and creates a channels-first uint8 tensor.",
            "Resize the shortest edge to 256 while preserving aspect ratio; the longer edge is int(256 * long / short).",
            "Use torchvision bicubic interpolation with antialias=True.",
            "Center-crop 224x224 using int((resized_dimension - crop_dimension) / 2.0) offsets.",
            "Convert to float32, rescale by 1/255, and normalize each RGB channel by mean/std (fused implementation).",
        ],
        "package_versions": versions,
    }


def run(args: argparse.Namespace) -> None:
    validate_args(args)
    args.output_dir.mkdir(parents=True)
    set_reproducible_seeds()

    load_kwargs: dict[str, Any] = {"local_files_only": not args.allow_download}
    if args.revision:
        load_kwargs["revision"] = args.revision
    try:
        processor = AutoImageProcessor.from_pretrained(args.embedding_model, **load_kwargs)
        model = AutoModel.from_pretrained(args.embedding_model, **load_kwargs)
    except Exception as exc:
        raise PlateExperimentError(f"Could not load the audit processor/model: {exc}") from exc
    model.eval()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)

    detector = None if args.cropped_plates else load_plate_detector(args.yolo_weights)
    versions = package_versions()
    record: dict[str, Any] = {
        "audit_schema_version": 1,
        "configuration": {
            "input_mode": "cropped_plates" if args.cropped_plates else "yolo_detection",
            "source_paths": [str(path) for path in args.images],
            "yolo_weights": None if args.yolo_weights is None else str(args.yolo_weights),
            "det_conf": args.det_conf,
            "crop_padding": args.crop_padding,
            "output_directory": str(args.output_dir),
            "device": str(device),
            "downloads_allowed": args.allow_download,
        },
        "processor": processor_record(processor, model, args, versions),
        "images": [],
    }

    for index, source_path in enumerate(args.images, start=1):
        item_dir = args.output_dir / f"image_{index}"
        item_dir.mkdir()
        original = read_image(source_path)
        copied_source = item_dir / f"original{source_path.suffix.lower()}"
        shutil.copy2(source_path, copied_source)

        detection: PlateDetection | None
        if detector is None:
            crop = original.copy()
            height, width = original.shape[:2]
            crop_box = (0, 0, width, height)
            detection = None
            mode_label = "already-cropped input; YOLO skipped"
        else:
            detection = detect_plate(detector, original, source_path, args.det_conf)
            crop, crop_box = crop_plate(original, detection.bbox, args.crop_padding)
            mode_label = "YOLO detection + padded crop"
        save_png(item_dir / "selected_plate_crop.png", crop)

        crop_rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        pil_image = Image.fromarray(crop_rgb)
        processor_inputs = processor(images=pil_image, return_tensors="pt")
        if set(processor_inputs.keys()) != {"pixel_values"}:
            raise PlateExperimentError(
                f"Unexpected processor outputs: {sorted(processor_inputs.keys())}"
            )
        exact_cpu_tensor = processor_inputs["pixel_values"].detach().cpu()

        prepared = processor.process_image(
            pil_image, do_convert_rgb=processor.do_convert_rgb
        )
        resized = processor.resize(
            prepared, size=processor.size, resample=processor.resample
        )
        center_cropped = processor.center_crop(resized, processor.crop_size)
        staged_tensor = processor.rescale_and_normalize(
            center_cropped.unsqueeze(0),
            processor.do_rescale,
            processor.rescale_factor,
            processor.do_normalize,
            processor.image_mean,
            processor.image_std,
        )
        staged_exact_match = torch.equal(exact_cpu_tensor, staged_tensor.cpu())
        if not staged_exact_match:
            raise PlateExperimentError(
                "Staged processor operations did not exactly reproduce processor(...)."
            )

        model_inputs = {
            name: value.to(device) if isinstance(value, torch.Tensor) else value
            for name, value in processor_inputs.items()
        }
        passed_tensor_exact_match = torch.equal(
            exact_cpu_tensor, model_inputs["pixel_values"].detach().cpu()
        )
        with torch.no_grad():
            outputs = model(**model_inputs)
            embedding = outputs.last_hidden_state[:, 0, :]
            embedding = F.normalize(embedding.float(), p=2, dim=1)
        if not passed_tensor_exact_match:
            raise PlateExperimentError("The recorded tensor changed while moving to the model device.")

        display, display_record = reconstruct_display(exact_cpu_tensor, processor)
        save_png(item_dir / "model_input_display_reconstruction.png", display)

        crop_size = (int(processor.crop_size.height), int(processor.crop_size.width))
        geometry = retained_geometry(
            crop.shape[:2], resized.shape[-2:], crop_size
        )
        retained_crop_box = geometry[
            "mapped_retained_region_clipped_xyxy_in_plate_crop"
        ]
        crop_overlay = retained_overlay(crop, retained_crop_box)
        save_png(item_dir / "retained_region_on_plate_crop.png", crop_overlay)
        retained_original_box = original_retained_box(crop_box, retained_crop_box)
        original_overlay = retained_overlay(original, retained_original_box)
        save_png(item_dir / "retained_region_on_original.png", original_overlay)
        create_stage_figure(
            item_dir / "preprocessing_stages.png",
            original,
            crop,
            crop_overlay,
            display,
            str(source_path),
            mode_label,
        )

        image_record = {
            "index": index,
            "source_path": str(source_path),
            "saved_original_copy": str(copied_source),
            "source_dimensions_hw": list(original.shape[:2]),
            "input_mode": "cropped_plates" if detection is None else "yolo_detection",
            "yolo_detection": (
                {"status": "skipped", "reason": "--cropped-plates was supplied"}
                if detection is None
                else {
                    "status": "performed",
                    "confidence": detection.confidence,
                    "bbox_xyxy": list(detection.bbox),
                    "detections_found": detection.detection_count,
                }
            ),
            "padded_plate_crop_bbox_xyxy_in_original": list(crop_box),
            "plate_crop_dimensions_hw": list(crop.shape[:2]),
            "geometry": {
                **geometry,
                "mapped_retained_region_xyxy_in_original": retained_original_box,
            },
            "tensor": {
                "key": "pixel_values",
                "shape": list(exact_cpu_tensor.shape),
                "dtype": str(exact_cpu_tensor.dtype),
                "sha256_of_contiguous_tensor_bytes": tensor_sha256(exact_cpu_tensor),
                "value_range": [
                    float(exact_cpu_tensor.min().item()),
                    float(exact_cpu_tensor.max().item()),
                ],
                "staged_operations_bit_exact_with_processor_call": staged_exact_match,
                "recorded_tensor_bit_exact_with_tensor_passed_to_model": passed_tensor_exact_match,
            },
            "embedding_verification": {
                "logic": "last_hidden_state[:, 0, :], float32, L2 normalization",
                "shape": list(embedding.shape),
                "dtype": str(embedding.dtype),
                "l2_norm": float(torch.linalg.vector_norm(embedding).item()),
                "similarities_computed": False,
            },
            "display_reconstruction": display_record,
            "artifacts": {
                "selected_plate_crop": str(item_dir / "selected_plate_crop.png"),
                "model_input_display_reconstruction": str(
                    item_dir / "model_input_display_reconstruction.png"
                ),
                "retained_region_on_plate_crop": str(
                    item_dir / "retained_region_on_plate_crop.png"
                ),
                "retained_region_on_original": str(
                    item_dir / "retained_region_on_original.png"
                ),
                "preprocessing_stages": str(item_dir / "preprocessing_stages.png"),
            },
        }
        record["images"].append(image_record)
        with (item_dir / "audit_record.json").open("w", encoding="utf-8") as handle:
            json.dump(image_record, handle, indent=2)
            handle.write("\n")
        print(
            f"Audited {source_path}: crop {crop.shape[1]}x{crop.shape[0]} -> "
            f"resize {resized.shape[-1]}x{resized.shape[-2]} -> "
            f"tensor {list(exact_cpu_tensor.shape)}"
        )

    with (args.output_dir / "audit_record.json").open("w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2)
        handle.write("\n")
    print(f"Audit written to: {args.output_dir}")


def main() -> int:
    try:
        run(parse_args())
        return 0
    except PlateExperimentError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
