"""OpenCV annotation adapted from common.annotate for standalone profiles."""
from __future__ import annotations

import time
import cv2
import numpy as np

_TYPE_COLORS = {"bus": (0, 80, 200), "car": (0, 180, 255), "truck": (0, 120, 255), "van": (60, 220, 220), "motorcycle": (180, 255, 0), "bicycle": (0, 220, 0), "unknown": (120, 120, 120)}
_ROI_COLORS = {"restricted": (0, 0, 220), "parking": (0, 200, 0), "read_zone": (0, 200, 255), "occupancy": (0, 180, 180)}


def draw_overlay(frame: np.ndarray, profiles, roi_config=None, roi_analytics=None) -> np.ndarray:
    annotated = frame.copy()
    for profile in profiles:
        x1, y1, x2, y2 = profile.bbox
        color = _TYPE_COLORS.get(profile.recognition.body_type, (80, 80, 200))
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
        label = f"#{profile.track_id} recognition disabled"
        if profile.recognition.status != "disabled":
            label = f"#{profile.track_id} {profile.recognition.manufacturer} / {profile.recognition.body_type} / {profile.recognition.color} [{profile.recognition.status}]"
        cv2.putText(annotated, label, (x1, max(18, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, .5, color, 2, cv2.LINE_AA)
    if roi_config is None:
        return annotated
    for polygon in roi_config.polygons:
        points = np.array(polygon.points, dtype=np.int32).reshape((-1, 1, 2))
        color = next((_ROI_COLORS[name] for name in ("restricted", "parking", "read_zone", "occupancy") if name in polygon.functions), (120, 120, 120))
        overlay = annotated.copy()
        cv2.fillPoly(overlay, [points], color)
        cv2.addWeighted(overlay, .2, annotated, .8, 0, annotated)
        cv2.polylines(annotated, [points], True, color, 2)
    for line in roi_config.lines:
        cv2.arrowedLine(annotated, tuple(line.a), tuple(line.b), (255, 255, 255), 2, tipLength=.05)
    if roi_analytics:
        now = time.monotonic()
        for slots in roi_analytics._slots.values():
            for slot in slots:
                if slot.confirmed:
                    x1, y1, x2, y2 = slot.bbox
                    cv2.putText(annotated, f"{now - slot.occupied_since:.0f}s", ((x1+x2)//2, (y1+y2)//2), cv2.FONT_HERSHEY_SIMPLEX, .5, (255,255,255), 2)
    return annotated
