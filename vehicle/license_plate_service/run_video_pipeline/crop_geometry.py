"""Shared Engine pixel/tracker geometry extracted without provider imports.

Legacy prototype re-exports these exact functions/classes; no read policy here.
"""
import cv2
import numpy as np

DEFAULT_ASSOCIATION_PADDING = 0.18


class TrackerDetections:
    """Small adapter from canonical detector tuples to Ultralytics BYTETracker."""

    def __init__(self, xyxy: np.ndarray, confidence: np.ndarray):
        self.xyxy = np.asarray(xyxy, dtype=np.float32).reshape(-1, 4)
        self.conf = np.asarray(confidence, dtype=np.float32).reshape(-1)
        self.cls = np.zeros(len(self.conf), dtype=np.float32)
        if len(self.xyxy):
            widths = self.xyxy[:, 2] - self.xyxy[:, 0]
            heights = self.xyxy[:, 3] - self.xyxy[:, 1]
            centers_x = self.xyxy[:, 0] + widths / 2
            centers_y = self.xyxy[:, 1] + heights / 2
            self.xywh = np.column_stack((centers_x, centers_y, widths, heights))
        else:
            self.xywh = np.empty((0, 4), dtype=np.float32)

    @classmethod
    def from_tuples(
        cls, detections, *, association_padding: float = 0.0
    ) -> "TrackerDetections":
        if association_padding < 0:
            raise ValueError("association padding must be non-negative")
        rows = list(detections)
        if not rows:
            return cls(np.empty((0, 4)), np.empty(0))
        values = np.asarray(rows, dtype=np.float32)
        boxes = values[:, :4].copy()
        if association_padding:
            widths = boxes[:, 2] - boxes[:, 0]
            heights = boxes[:, 3] - boxes[:, 1]
            boxes[:, 0] -= widths * association_padding
            boxes[:, 2] += widths * association_padding
            boxes[:, 1] -= heights * association_padding
            boxes[:, 3] += heights * association_padding
        return cls(boxes, values[:, 4])

    def __len__(self) -> int:
        return len(self.conf)

    def __getitem__(self, item) -> "TrackerDetections":
        return TrackerDetections(self.xyxy[item], self.conf[item])


def _detection_for_track(
    tracked_box: np.ndarray,
    detections: list[tuple[int, int, int, int, float]],
    association_padding: float,
    used: set[int],
) -> tuple[int, int, int, int, float] | None:
    best_index = None
    best_iou = -1.0
    tx1, ty1, tx2, ty2 = (float(value) for value in tracked_box[:4])
    for index, detection in enumerate(detections):
        if index in used:
            continue
        x1, y1, x2, y2, _ = detection
        width, height = x2 - x1, y2 - y1
        candidate = (
            x1 - width * association_padding,
            y1 - height * association_padding,
            x2 + width * association_padding,
            y2 + height * association_padding,
        )
        intersection = max(0.0, min(tx2, candidate[2]) - max(tx1, candidate[0])) * max(
            0.0, min(ty2, candidate[3]) - max(ty1, candidate[1])
        )
        tracked_area = max(0.0, tx2 - tx1) * max(0.0, ty2 - ty1)
        candidate_area = max(0.0, candidate[2] - candidate[0]) * max(
            0.0, candidate[3] - candidate[1]
        )
        union = tracked_area + candidate_area - intersection
        iou = intersection / union if union else 0.0
        if iou > best_iou:
            best_index, best_iou = index, iou
    if best_index is None:
        return None
    used.add(best_index)
    return detections[best_index]


def crop_quality_evidence(
    crop: np.ndarray, detector_confidence: float, *, border_contact: bool = False
) -> tuple[float, int, float]:
    """Rank crops within one track using only cheap geometry/image statistics."""
    if crop is None or not isinstance(crop, np.ndarray) or crop.size == 0:
        return float("-inf"), 0, 0.0
    height, width = crop.shape[:2]
    if width < 1 or height < 1:
        return float("-inf"), 0, 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    contrast = float(gray.std())
    brightness = float(gray.mean())
    exposure = max(0.0, 1.0 - abs(brightness - 127.5) / 127.5)
    sharpness_factor = 1.0 + 0.25 * min(sharpness / 400.0, 1.0)
    contrast_factor = 0.9 + 0.1 * min(contrast / 64.0, 1.0)
    exposure_factor = 0.9 + 0.1 * exposure
    confidence_factor = 0.5 + max(0.0, min(float(detector_confidence), 1.0))
    border_factor = 0.60 if border_contact else 1.0
    score = width * height * confidence_factor * sharpness_factor * contrast_factor
    score *= exposure_factor * border_factor
    return float(score), width * height, sharpness


def crop_quality(crop: np.ndarray, detector_confidence: float) -> float:
    return crop_quality_evidence(crop, detector_confidence)[0]

