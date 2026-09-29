#!/usr/bin/env python3
"""Standalone HSV vehicle-colour evaluation."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import cv2

# Make direct execution from scripts/ behave like the project entry points.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from common.colour import estimate_colour
except ModuleNotFoundError as exc:
    if exc.name == "common.colour":
        raise SystemExit("error: common/colour.py is missing; the existing HSV estimator is required") from exc
    raise
from common.config import VEHICLE_MODEL_PATH
from common.detector import VehicleDetector
from common.device import resolve_device

DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "debug/colour_hsv"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--image", type=Path, help="single input image")
    source.add_argument("--dir", type=Path, help="directory of input images")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--device", default="auto", help="auto, cpu, cuda, or cuda:N")
    return parser.parse_args()


def _input_paths(args: argparse.Namespace) -> list[Path]:
    if args.image:
        if not args.image.is_file():
            raise FileNotFoundError(f"input image not found: {args.image}")
        return [args.image]
    if not args.dir.is_dir():
        raise FileNotFoundError(f"input directory not found: {args.dir}")
    paths = sorted(path for path in args.dir.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES)
    if not paths:
        raise ValueError(f"no supported images found in: {args.dir}")
    return paths


def _draw_label(image, bbox: tuple[int, int, int, int], lines: list[str]) -> None:
    x1, y1, x2, y2 = bbox
    colour = (0, 210, 255)
    cv2.rectangle(image, (x1, y1), (x2, y2), colour, 2)
    scale, thickness, line_height = 0.48, 1, 18
    widths = [cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)[0][0] for line in lines]
    box_width = max(widths, default=0) + 8
    box_height = line_height * len(lines) + 6
    label_y = y1 - box_height if y1 >= box_height else y1
    label_x = min(x1, max(0, image.shape[1] - box_width))
    cv2.rectangle(image, (label_x, label_y), (label_x + box_width, label_y + box_height), (20, 20, 20), -1)
    for index, line in enumerate(lines):
        cv2.putText(image, line, (label_x + 4, label_y + 16 + index * line_height),
                    cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), thickness, cv2.LINE_AA)


def _safe_output_path(output_dir: Path, source: Path) -> Path:
    candidate = output_dir / f"{source.stem}_colour_hsv.jpg"
    serial = 2
    while candidate.exists():
        candidate = output_dir / f"{source.stem}_colour_hsv_{serial}.jpg"
        serial += 1
    return candidate


def main() -> int:
    args = _parse_args()
    try:
        paths = _input_paths(args)
        device = resolve_device(args.device)
        detector = VehicleDetector(VEHICLE_MODEL_PATH, device=device)
        args.output_dir.mkdir(parents=True, exist_ok=True)
    except (FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"device: {device}")
    csv_rows = []
    failures = 0
    for path in paths:
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None or image.size == 0:
            print(f"warning: cannot decode image, skipped: {path}", file=sys.stderr)
            failures += 1
            continue
        annotated = image.copy()
        try:
            detections = detector.predict(image)
        except Exception as exc:
            print(f"warning: detection failed for {path}: {exc}", file=sys.stderr)
            failures += 1
            continue
        height, width = image.shape[:2]
        for index, detection in enumerate(detections):
            bx1, by1, bx2, by2 = detection.bbox
            x1, y1 = max(0, min(width, bx1)), max(0, min(height, by1))
            x2, y2 = max(0, min(width, bx2)), max(0, min(height, by2))
            if x2 <= x1 or y2 <= y1:
                print(f"warning: {path.name} vehicle {index} has an empty clamped crop; skipped", file=sys.stderr)
                continue
            crop = image[y1:y2, x1:x2]
            if crop.size == 0:
                print(f"warning: {path.name} vehicle {index} has an empty crop; skipped", file=sys.stderr)
                continue
            try:
                hsv_label, hsv_confidence = estimate_colour(crop)
            except Exception as exc:
                print(f"warning: HSV failed for {path.name} vehicle {index}: {exc}", file=sys.stderr)
                hsv_label, hsv_confidence = "error", 0.0
                failures += 1
            print(f"\n{path.name} — Vehicle {index} ({detection.type})\nbbox: [{x1}, {y1}, {x2}, {y2}]")
            print(f"HSV:\n  {hsv_label} {hsv_confidence:.3f}")
            _draw_label(annotated, (x1, y1, x2, y2), [
                detection.type,
                f"HSV: {hsv_label} {hsv_confidence:.2f}",
            ])
            csv_rows.append({
                "image": path.name, "vehicle_index": index, "vehicle_type": detection.type,
                "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                "hsv_label": hsv_label, "hsv_confidence": f"{hsv_confidence:.6f}",
            })
        output_path = _safe_output_path(args.output_dir, path)
        if not cv2.imwrite(str(output_path), annotated):
            print(f"warning: failed to save annotated image: {output_path}", file=sys.stderr)
            failures += 1
        else:
            print(f"saved: {output_path}")
    csv_path = args.output_dir / "results.csv"
    fieldnames = [
        "image", "vehicle_index", "vehicle_type", "x1", "y1", "x2", "y2",
        "hsv_label", "hsv_confidence",
    ]
    try:
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(csv_rows)
        print(f"saved: {csv_path}")
    except OSError as exc:
        print(f"warning: failed to save CSV {csv_path}: {exc}", file=sys.stderr)
        failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
