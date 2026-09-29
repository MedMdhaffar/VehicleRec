from __future__ import annotations
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np

from common import config

if TYPE_CHECKING:
    from common.schemas import Detection

log = logging.getLogger(__name__)

_RESULTS_DIR = Path(config.RESULTS_DIR)
_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# BGR colours per vehicle type
_TYPE_COLORS: dict[str, tuple[int, int, int]] = {
    "bus":        (0,   80,  200),
    "car":        (0,  180,  255),
    "truck":      (0,  120,  255),
    "van":        (60, 220,  220),
    "motorcycle": (180, 255,   0),
    "bicycle":    (0,  220,    0),
    "person":     (255, 120,   0),
}
_DEFAULT_COLOR = (80, 80, 200)


def annotate_and_save(
    image: np.ndarray,
    detections: list[Detection],
    request_id: str,
) -> Path | None:
    """Draw detection boxes on *image*, save to results dir, return path."""
    try:
        annotated = image.copy()

        for det in detections:
            x1, y1, x2, y2 = det.bbox
            color = _TYPE_COLORS.get(det.type, _DEFAULT_COLOR)
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            colour_part = f" {det.colour}" if det.colour and det.colour != "unknown" else ""
            label = f"{det.type}{colour_part} {det.type_confidence:.0%}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
            ty = max(y1 - 4, th + 4)
            cv2.rectangle(annotated, (x1, ty - th - 4), (x1 + tw + 4, ty + 2), color, -1)
            cv2.putText(annotated, label, (x1 + 2, ty - 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1, cv2.LINE_AA)

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        fname = f"{ts}_{request_id[:8]}.jpg"
        path = _RESULTS_DIR / fname
        cv2.imwrite(str(path), annotated, [cv2.IMWRITE_JPEG_QUALITY, 85])

        _evict_old()
        return path

    except Exception as exc:
        log.warning("annotate_and_save failed: %s", exc)
        return None


# BGR colors per ROI function (strongest wins for multi-function polygons)
_ROI_PRIORITY = ["restricted", "parking", "read_zone", "occupancy"]
_ROI_COLORS: dict[str, tuple[int, int, int]] = {
    "restricted": (0,   0, 220),
    "parking":    (0, 200,   0),
    "read_zone":  (0, 200, 255),
    "occupancy":  (0, 180, 180),
}
_ROI_DEFAULT_COLOR = (120, 120, 120)


def _roi_color(functions: list[str]) -> tuple[int, int, int]:
    for fn in _ROI_PRIORITY:
        if fn in functions:
            return _ROI_COLORS[fn]
    return _ROI_DEFAULT_COLOR


def draw_overlay(
    ann: np.ndarray,
    profiles,
    roi_config=None,      # RoiConfig | None
    roi_analytics=None,   # RoiAnalytics | None
    show_track_details: bool = False,
) -> None:
    """Draw vehicle tracks + ROI overlays in place onto *ann*. No encode."""
    import time as _time

    # ── Vehicle bboxes ────────────────────────────────────────────────────────
    for p in profiles:
        x1, y1, x2, y2 = p.bbox
        color = _TYPE_COLORS.get(p.type, _DEFAULT_COLOR)
        cv2.rectangle(ann, (x1, y1), (x2, y2), color, 2)
        if show_track_details:
            from live_feed.config import MAX_VALID_COLOR_SAMPLES, MIN_VALID_COLOR_SAMPLES

            sample_count = len(p._colour_samples)
            if p._colour_finalized:
                state = "LOCKED" if p.colour != "unknown" else "FINAL"
                colour = f"{p.colour} {p.colour_conf:.2f} | {state}"
            elif sample_count < MIN_VALID_COLOR_SAMPLES:
                colour = f"colour pending {sample_count}/{MIN_VALID_COLOR_SAMPLES}"
            else:
                colour = (f"{p._colour_candidate} {p._colour_winner_share:.2f} | "
                          f"{sample_count}/{MAX_VALID_COLOR_SAMPLES} pending")
            label = f"ID {p.track_id} | {p.type} | {colour}"
        else:
            label = f"{p.type} {p.colour}"
        if p.plate.locked and p.plate.number:
            label += f" | {p.plate.number} ({p.plate.type or '?'})"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        ty = max(y1 - 4, th + 4)
        cv2.rectangle(ann, (x1, ty - th - 4), (x1 + tw + 4, ty + 2), color, -1)
        cv2.putText(ann, label, (x1 + 2, ty - 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)

    if roi_config is None:
        return

    # ── Polygon overlays ──────────────────────────────────────────────────────
    for poly in roi_config.polygons:
        pts = np.array(poly.points, dtype=np.int32).reshape((-1, 1, 2))
        color = _roi_color(poly.functions)
        # Translucent fill
        overlay = ann.copy()
        cv2.fillPoly(overlay, [pts], color)
        cv2.addWeighted(overlay, 0.20, ann, 0.80, 0, ann)
        # Solid border
        cv2.polylines(ann, [pts], True, color, 2)
        # Label: name + functions near centroid
        cx = int(np.mean([p[0] for p in poly.points]))
        cy = int(np.mean([p[1] for p in poly.points]))
        fn_str = ",".join(poly.functions) if poly.functions else ""
        label = f"{poly.name} [{fn_str}]" if fn_str else poly.name
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        lx = max(0, cx - tw // 2)
        ly = cy
        cv2.rectangle(ann, (lx - 2, ly - th - 4), (lx + tw + 2, ly + 2), (0, 0, 0), -1)
        cv2.putText(ann, label, (lx, ly - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)

    # ── Line overlays ─────────────────────────────────────────────────────────
    for line in roi_config.lines:
        ax, ay = line.a
        bx, by = line.b
        # Shadow + white arrow
        cv2.arrowedLine(ann, (ax, ay), (bx, by), (0, 0, 0), 4, tipLength=0.05)
        cv2.arrowedLine(ann, (ax, ay), (bx, by), (255, 255, 255), 2, tipLength=0.05)
        # Counts label
        mx, my = (ax + bx) // 2, (ay + by) // 2
        if roi_analytics is not None and line.id in roi_analytics._line_counts:
            ltr = sum(roi_analytics._line_counts[line.id]["left_to_right"].values())
            rtl = sum(roi_analytics._line_counts[line.id]["right_to_left"].values())
            count_label = f"{line.name} {ltr}→ ←{rtl}"
        else:
            count_label = line.name
        (tw, th), _ = cv2.getTextSize(count_label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        cv2.rectangle(ann, (mx - 2, my - th - 4), (mx + tw + 2, my + 2), (0, 0, 0), -1)
        cv2.putText(ann, count_label, (mx, my - 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)

    # ── Occupancy slot dwell overlays ─────────────────────────────────────────
    if roi_analytics is not None:
        now = _time.monotonic()
        for poly in roi_config.polygons:
            if "parking" not in poly.functions and "restricted" not in poly.functions:
                continue
            for slot in roi_analytics._slots.get(poly.id, []):
                if not slot.confirmed:
                    continue
                dwell = now - slot.occupied_since
                mins = int(dwell) // 60
                secs = int(dwell) % 60
                dwell_str = f"{mins}:{secs:02d}"
                x1, y1, x2, y2 = slot.bbox
                cx = (x1 + x2) // 2
                cy = (y1 + y2) // 2
                text_color = (0, 0, 255) if (slot.overstay_fired or slot.violation_fired) else (255, 255, 255)
                (tw, th), _ = cv2.getTextSize(dwell_str, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
                tx = cx - tw // 2
                ty = cy + th // 2
                cv2.putText(ann, dwell_str, (tx, ty),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3, cv2.LINE_AA)
                cv2.putText(ann, dwell_str, (tx, ty),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, text_color, 2, cv2.LINE_AA)


def draw_live_frame(
    frame: np.ndarray,
    profiles,
    jpeg_quality: int = 80,
    roi_config=None,      # RoiConfig | None
    roi_analytics=None,   # RoiAnalytics | None
) -> bytes:
    """Annotate *frame* with active vehicle tracks + ROI overlays; return JPEG bytes."""
    ann = frame.copy()
    draw_overlay(ann, profiles, roi_config=roi_config, roi_analytics=roi_analytics)
    ok, buf = cv2.imencode(".jpg", ann, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
    return buf.tobytes() if ok else b""


def _evict_old() -> None:
    files = sorted(_RESULTS_DIR.glob("*.jpg"))
    for old in files[: -config.RESULTS_MAX_FILES]:
        try:
            old.unlink()
        except OSError:
            pass
