#!/usr/bin/env python3
"""Detect and crop one highest-confidence license plate from every car image."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import cv2

from compare_plate_embeddings import (
    PlateExperimentError,
    crop_plate,
    detect_plate,
    draw_detection,
    load_plate_detector,
    read_image,
    save_png,
)


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Crop the highest-confidence detected plate from every image in a directory."
    )
    parser.add_argument("--input-dir", type=Path, default=Path("cars"))
    parser.add_argument("--yolo-weights", type=Path, default=Path("best.pt"))
    parser.add_argument("--output-dir", type=Path, default=Path("car_plate_crops"))
    parser.add_argument("--det-conf", type=float, default=0.25)
    parser.add_argument("--crop-padding", type=float, default=0.05)
    parser.add_argument(
        "--overrides",
        type=Path,
        help="Optional JSON mapping filenames to reviewed fallback bounding boxes.",
    )
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args: argparse.Namespace) -> None:
    if not args.input_dir.is_dir():
        raise PlateExperimentError(f"Input directory does not exist: {args.input_dir}")
    if not args.yolo_weights.is_file():
        raise PlateExperimentError(f"YOLO weights do not exist: {args.yolo_weights}")
    if args.output_dir.exists():
        raise PlateExperimentError(
            f"Output directory already exists; refusing to overwrite it: {args.output_dir}"
        )
    overrides = {}
    if args.overrides is not None:
        if not args.overrides.is_file():
            raise PlateExperimentError(f"Override file does not exist: {args.overrides}")
        try:
            overrides = json.loads(args.overrides.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PlateExperimentError(f"Could not read override JSON: {exc}") from exc
    if not 0 <= args.det_conf <= 1:
        raise PlateExperimentError("--det-conf must be between 0 and 1 inclusive.")
    if args.crop_padding < 0:
        raise PlateExperimentError("--crop-padding must be non-negative.")
    image_paths = sorted(
        path
        for path in args.input_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )
    if not image_paths:
        raise PlateExperimentError(f"No supported images found in: {args.input_dir}")

    args.output_dir.mkdir(parents=True)
    crops_dir = args.output_dir / "crops"
    detections_dir = args.output_dir / "detections"
    crops_dir.mkdir()
    detections_dir.mkdir()
    detector = load_plate_detector(args.yolo_weights)
    records = []

    for index, image_path in enumerate(image_paths, start=1):
        try:
            image = read_image(image_path)
            detection = detect_plate(detector, image, image_path, args.det_conf)
            crop, crop_box = crop_plate(image, detection.bbox, args.crop_padding)
            crop_path = crops_dir / f"{image_path.stem}_plate.png"
            detection_path = detections_dir / f"{image_path.stem}_detection.png"
            save_png(crop_path, crop)
            save_png(detection_path, draw_detection(image, detection, index))
            records.append(
                {
                    "source": str(image_path),
                    "source_sha256": sha256_file(image_path),
                    "status": "cropped",
                    "source_dimensions_hw": list(image.shape[:2]),
                    "confidence": detection.confidence,
                    "detections_found": detection.detection_count,
                    "detected_bbox_xyxy": list(detection.bbox),
                    "padded_crop_bbox_xyxy": list(crop_box),
                    "crop_dimensions_hw": list(crop.shape[:2]),
                    "crop": str(crop_path),
                    "detection_preview": str(detection_path),
                }
            )
            print(
                f"[{index}/{len(image_paths)}] {image_path.name}: "
                f"confidence={detection.confidence:.4f}, crop={crop.shape[1]}x{crop.shape[0]}"
            )
        except PlateExperimentError as exc:
            override = overrides.get(image_path.name)
            if override is not None:
                try:
                    image = read_image(image_path)
                    bbox = [float(value) for value in override["bbox_xyxy"]]
                    if len(bbox) != 4:
                        raise ValueError("bbox_xyxy must contain four values")
                    crop, crop_box = crop_plate(image, bbox, args.crop_padding)
                    crop_path = crops_dir / f"{image_path.stem}_plate.png"
                    detection_path = detections_dir / f"{image_path.stem}_detection.png"
                    save_png(crop_path, crop)
                    preview = image.copy()
                    x1, y1, x2, y2 = [int(round(value)) for value in bbox]
                    cv2.rectangle(preview, (x1, y1), (x2, y2), (0, 165, 255), 5)
                    cv2.putText(
                        preview,
                        "reviewed fallback box",
                        (x1, max(35, y1 - 10)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        1.0,
                        (0, 165, 255),
                        3,
                        cv2.LINE_AA,
                    )
                    save_png(detection_path, preview)
                    records.append(
                        {
                            "source": str(image_path),
                            "source_sha256": sha256_file(image_path),
                            "status": "cropped_with_reviewed_fallback",
                            "strict_detection_error": str(exc),
                            "source_dimensions_hw": list(image.shape[:2]),
                            "fallback_bbox_xyxy": bbox,
                            "fallback_basis": override.get("basis"),
                            "review_note": override.get("note"),
                            "padded_crop_bbox_xyxy": list(crop_box),
                            "crop_dimensions_hw": list(crop.shape[:2]),
                            "crop": str(crop_path),
                            "detection_preview": str(detection_path),
                        }
                    )
                    print(
                        f"[{index}/{len(image_paths)}] {image_path.name}: "
                        f"reviewed fallback, crop={crop.shape[1]}x{crop.shape[0]}"
                    )
                    continue
                except (KeyError, TypeError, ValueError, PlateExperimentError) as override_exc:
                    exc = PlateExperimentError(
                        f"{exc} Fallback override also failed: {override_exc}"
                    )
            records.append(
                {
                    "source": str(image_path),
                    "source_sha256": sha256_file(image_path),
                    "status": "failed",
                    "error": str(exc),
                }
            )
            print(f"[{index}/{len(image_paths)}] {image_path.name}: FAILED: {exc}")

    manifest = {
        "input_directory": str(args.input_dir),
        "weights": str(args.yolo_weights),
        "weights_sha256": sha256_file(args.yolo_weights),
        "det_conf": args.det_conf,
        "crop_padding_each_side": args.crop_padding,
        "selection": "highest-confidence detection per image",
        "images_found": len(image_paths),
        "successful_crops": sum(record["status"].startswith("cropped") for record in records),
        "failed_images": sum(record["status"] == "failed" for record in records),
        "override_file": None if args.overrides is None else str(args.overrides),
        "records": records,
    }
    with (args.output_dir / "manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
        handle.write("\n")

    print(
        f"Saved {manifest['successful_crops']} crops; "
        f"{manifest['failed_images']} failures; output={args.output_dir.resolve()}"
    )


def main() -> int:
    try:
        run(parse_args())
        return 0
    except PlateExperimentError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
