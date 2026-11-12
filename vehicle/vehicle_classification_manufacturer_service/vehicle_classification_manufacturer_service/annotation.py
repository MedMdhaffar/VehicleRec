"""OpenCV annotation adapted from common.annotate for standalone profiles."""
from __future__ import annotations

import cv2
import numpy as np

_TYPE_COLORS = {"bus": (0, 80, 200), "car": (0, 180, 255), "truck": (0, 120, 255), "van": (60, 220, 220), "motorcycle": (180, 255, 0), "bicycle": (0, 220, 0), "unknown": (120, 120, 120)}


def draw_overlay(frame: np.ndarray, profiles) -> np.ndarray:
    annotated = frame.copy()
    for profile in profiles:
        x1, y1, x2, y2 = profile.bbox
        color = _TYPE_COLORS.get(profile.recognition.body_type, (80, 80, 200))
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
        label = f"#{profile.track_id} recognition disabled"
        if profile.recognition.status != "disabled":
            label = f"#{profile.track_id} {profile.recognition.manufacturer} / {profile.recognition.body_type} / {profile.recognition.color} [{profile.recognition.status}]"
        cv2.putText(annotated, label, (x1, max(18, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, .5, color, 2, cv2.LINE_AA)
    return annotated
