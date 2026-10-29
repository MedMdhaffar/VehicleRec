#!/usr/bin/env python3
"""Interactively annotate tight whole-plate boxes and ordered plate corners."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from plate_variants import (
    RECTIFICATION_STATUSES,
    VariantExperimentError,
    load_json,
    validate_annotations,
    validate_manifest,
)


WINDOW = "Manual plate geometry — bbox then TL,TR,BR,BL corners"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create/review manual whole-plate geometry without automatic estimates."
    )
    parser.add_argument("--manifest", type=Path, default=Path("controlled_plate_manifest.json"))
    parser.add_argument("--annotations", type=Path, default=Path("plate_geometry_annotations.json"))
    parser.add_argument("--review-gallery", type=Path, default=Path("plate_geometry_review.png"))
    parser.add_argument("--review-only", action="store_true", help="Validate and export overlays without opening a GUI.")
    parser.add_argument("--max-display-width", type=int, default=1400)
    parser.add_argument("--max-display-height", type=int, default=850)
    return parser.parse_args()


def load_annotation_document(path: Path) -> dict[str, Any]:
    if path.exists():
        document = load_json(path)
    else:
        document = {
            "schema_version": 1,
            "coordinate_convention": (
                "Original decoded-image coordinates. tight_plate_bbox is xyxy with exclusive "
                "x2/y2; corners are ordered top-left, top-right, bottom-right, bottom-left."
            ),
            "images": {},
        }
    if not isinstance(document.get("images"), dict):
        raise VariantExperimentError("Annotation images must be an object keyed by image ID.")
    return document


def save_document(path: Path, document: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def display_scale(image: np.ndarray, max_width: int, max_height: int) -> float:
    height, width = image.shape[:2]
    # Upscale small crops for usable clicking, but respect the configured screen bounds.
    return min(max_width / width, max_height / height, max(1.0, 700.0 / width))


def annotate_one(record: dict[str, Any], existing: dict[str, Any] | None, args: argparse.Namespace) -> dict[str, Any] | None:
    image = record["image"]
    scale = display_scale(image, args.max_display_width, args.max_display_height)
    shown = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST if scale >= 3 else cv2.INTER_LINEAR)
    state: dict[str, Any] = {
        "bbox": None if existing is None else list(existing.get("tight_plate_bbox", [])),
        "corners": [] if existing is None else [list(point) for point in existing.get("plate_corners", [])],
        "status": "needed" if existing is None else existing.get("rectification_status", "needed"),
        "notes": "" if existing is None else existing.get("notes", ""),
        "drag_start": None,
    }

    def original_point(x: int, y: int) -> list[float]:
        return [min(image.shape[1], max(0.0, x / scale)), min(image.shape[0], max(0.0, y / scale))]

    def mouse(event: int, x: int, y: int, _flags: int, _parameter: Any) -> None:
        point = original_point(x, y)
        if event == cv2.EVENT_LBUTTONDOWN:
            state["drag_start"] = point
        elif event == cv2.EVENT_LBUTTONUP and state["drag_start"] is not None:
            start = state.pop("drag_start")
            if abs(point[0] - start[0]) >= 2 and abs(point[1] - start[1]) >= 2:
                state["bbox"] = [min(start[0], point[0]), min(start[1], point[1]), max(start[0], point[0]), max(start[1], point[1])]
        elif event == cv2.EVENT_RBUTTONDOWN:
            if len(state["corners"]) == 4:
                state["corners"] = []
            state["corners"].append(point)

    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(WINDOW, mouse)
    while True:
        canvas = shown.copy()
        if state["bbox"] and len(state["bbox"]) == 4:
            x1, y1, x2, y2 = [int(round(value * scale)) for value in state["bbox"]]
            cv2.rectangle(canvas, (x1, y1), (x2, y2), (0, 255, 0), 2)
        points = [(int(round(x * scale)), int(round(y * scale))) for x, y in state["corners"]]
        for number, point in enumerate(points):
            cv2.circle(canvas, point, 6, (0, 0, 255), -1)
            cv2.putText(canvas, ("TL", "TR", "BR", "BL")[number], (point[0] + 8, point[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2)
        if len(points) == 4:
            cv2.polylines(canvas, [np.asarray(points, dtype=np.int32)], True, (255, 0, 255), 2)
        title = f"ID {record['id']} | left-drag bbox (redraw adjusts) | right-click TL TR BR BL | status={state['status']}"
        cv2.putText(canvas, title, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 0), 2)
        cv2.putText(canvas, "S save/next  R reset  D needed  U unnecessary  X unavailable  Q save/quit", (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 1)
        cv2.imshow(WINDOW, canvas)
        key = cv2.waitKey(30) & 0xFF
        if key in (ord("r"), ord("R")):
            state.update({"bbox": None, "corners": []})
        elif key in (ord("d"), ord("D")):
            state["status"] = "needed"
        elif key in (ord("u"), ord("U")):
            state["status"] = "unnecessary"
        elif key in (ord("x"), ord("X")):
            state["status"] = "unavailable"
        elif key in (ord("s"), ord("S"), ord("q"), ord("Q")):
            if state["bbox"] is None or len(state["corners"]) != 4:
                print(f"Image {record['id']}: bbox and exactly four corners are required before save.", file=sys.stderr)
                continue
            result = {
                "tight_plate_bbox": [round(float(value), 4) for value in state["bbox"]],
                "plate_corners": [[round(float(value), 4) for value in point] for point in state["corners"]],
                "rectification_status": state["status"],
                "notes": state["notes"],
                "annotation_method": "manual interactive display",
            }
            result["quit_after_save"] = key in (ord("q"), ord("Q"))
            return result
        elif key == 27:
            return None


def export_review_gallery(path: Path, records: list[dict[str, Any]], annotations: dict[str, dict[str, Any]]) -> None:
    available = [record for record in records if record.get("available")]
    figure, axes = plt.subplots(len(available), 1, figsize=(15, max(3, 2.8 * len(available))), squeeze=False, constrained_layout=True)
    for axis, record in zip(axes[:, 0], available):
        rgb = cv2.cvtColor(record["image"], cv2.COLOR_BGR2RGB)
        axis.imshow(rgb)
        annotation = annotations.get(str(record["id"]))
        if annotation:
            bbox = annotation.get("tight_plate_bbox_rounded_xyxy", annotation.get("tight_plate_bbox"))
            if bbox:
                x1, y1, x2, y2 = bbox
                axis.add_patch(plt.Rectangle((x1, y1), x2 - x1, y2 - y1, fill=False, color="lime", linewidth=2))
            corners = np.asarray(annotation.get("plate_corners", []), dtype=float)
            if corners.shape == (4, 2):
                closed = np.vstack([corners, corners[0]])
                axis.plot(closed[:, 0], closed[:, 1], "m-o", linewidth=1.5)
                for label, point in zip(("TL", "TR", "BR", "BL"), corners):
                    axis.text(point[0], point[1], label, color="yellow", fontsize=8)
            status = annotation.get("rectification_status", "missing")
        else:
            status = "MISSING"
        marker = record.get("marker_bbox")
        if marker:
            x1, y1, x2, y2 = marker
            axis.add_patch(plt.Rectangle((x1, y1), x2 - x1, y2 - y1, fill=False, color="cyan", linestyle="--", linewidth=1))
        axis.set_title(f"{record['id']}. {record['category']} | rectification={status} | green=bbox, magenta=corners, cyan=marker metadata")
        axis.axis("off")
    figure.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(figure)


def run(args: argparse.Namespace) -> int:
    records, manifest_report = validate_manifest(args.manifest)
    for error in manifest_report["errors"]:
        print(f"Manifest: {error}", file=sys.stderr)
    document = load_annotation_document(args.annotations)
    if not args.review_only:
        for record in records:
            if not record.get("available"):
                continue
            result = annotate_one(record, document["images"].get(str(record["id"])), args)
            if result is None:
                break
            quit_after = result.pop("quit_after_save")
            document["images"][str(record["id"])] = result
            save_document(args.annotations, document)
            if quit_after:
                break
        cv2.destroyAllWindows()
    annotations, annotation_report = validate_annotations(args.annotations, records, require_complete=False)
    export_review_gallery(args.review_gallery, records, annotations)
    print(json.dumps({"manifest": manifest_report, "annotations": annotation_report}, indent=2))
    print(f"Review gallery: {args.review_gallery.resolve()}")
    return 0 if manifest_report["valid"] and annotation_report["complete"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(run(parse_args()))
    except VariantExperimentError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
