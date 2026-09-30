"""ROI geometry and analytics adapted from live_feed.roi without removed metadata."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from . import config as cfg
from .schemas import VehicleProfile


@dataclass
class PolygonROI:
    id: str
    name: str
    points: list[list[int]]
    functions: list[str]


@dataclass
class LineROI:
    id: str
    name: str
    a: list[int]
    b: list[int]
    count: bool = True


@dataclass
class RoiConfig:
    camera: str = "cli"
    frame_size: list[int] = field(default_factory=lambda: [1920, 1080])
    polygons: list[PolygonROI] = field(default_factory=list)
    lines: list[LineROI] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "RoiConfig":
        source = Path(path).expanduser().resolve()
        data = json.loads(source.read_text(encoding="utf-8"))
        return cls(
            camera=str(data.get("camera", "cli")),
            frame_size=data.get("frame_size", [1920, 1080]),
            polygons=[PolygonROI(p["id"], p.get("name", p["id"]), p["points"], p.get("functions", [])) for p in data.get("polygons", [])],
            lines=[LineROI(x["id"], x.get("name", x["id"]), x["a"], x["b"], x.get("count", True)) for x in data.get("lines", [])],
        )


@dataclass
class OccupancySlot:
    bbox: list[int]
    occupied_since: float
    last_seen_frame: int
    consecutive_matches: int = 0
    confirmed: bool = False
    present_at_startup: bool = False
    overstay_fired: bool = False
    violation_fired: bool = False
    vehicle_type: str | None = None


def foot_point(bbox: list[int]) -> tuple[int, int]:
    x1, _y1, x2, y2 = bbox
    return ((x1 + x2) // 2, y2)


def point_in_polygon(point: tuple[int, int], points: list[list[int]]) -> bool:
    if len(points) < 3:
        return False
    contour = np.array(points, dtype=np.float32).reshape((-1, 1, 2))
    return cv2.pointPolygonTest(contour, (float(point[0]), float(point[1])), False) >= 0


def iou(a: list[int], b: list[int]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1, ix2, iy2 = max(ax1, bx1), max(ay1, by1), min(ax2, bx2), min(ay2, by2)
    intersection = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if not intersection:
        return 0.0
    union = max(0, ax2 - ax1) * max(0, ay2 - ay1) + max(0, bx2 - bx1) * max(0, by2 - by1) - intersection
    return intersection / union if union else 0.0


def line_side(point: tuple[int, int], a: list[int], b: list[int]) -> float:
    return float((b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0]))


def segment_crosses(previous: tuple[int, int], current: tuple[int, int], a: list[int], b: list[int]) -> bool:
    before, after = line_side(previous, a, b), line_side(current, a, b)
    if before == 0.0 or after == 0.0 or (before > 0) == (after > 0):
        return False
    return not (
        max(previous[0], current[0]) < min(a[0], b[0])
        or min(previous[0], current[0]) > max(a[0], b[0])
        or max(previous[1], current[1]) < min(a[1], b[1])
        or min(previous[1], current[1]) > max(a[1], b[1])
    )


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RoiAnalytics:
    def __init__(self, config: RoiConfig, started_at: float) -> None:
        self.config = config
        self._started_at = started_at
        self._slots = {p.id: [] for p in config.polygons if {"parking", "restricted"}.intersection(p.functions)}
        self._line_counts = {line.id: {"left_to_right": Counter(), "right_to_left": Counter()} for line in config.lines if line.count}
        self._previous: dict[int, tuple[int, int]] = {}
        self._crossed: dict[int, set[str]] = {}
        self._occupancy_over: dict[str, bool] = {}

    def process_frame(self, active: dict[int, VehicleProfile], frame_idx: int, now: float) -> list[dict]:
        events: list[dict] = []
        for track_id in self._previous.keys() - active.keys():
            self._previous.pop(track_id, None)
            self._crossed.pop(track_id, None)
        for polygon in self.config.polygons:
            if {"parking", "restricted"}.intersection(polygon.functions):
                events.extend(self._update_occupancy(polygon, active, frame_idx, now))
            if "occupancy" in polygon.functions:
                events.extend(self._check_count(polygon, active))
        for line in self.config.lines:
            if line.count:
                events.extend(self._check_line(line, active))
        self._previous.update({track_id: foot_point(profile.bbox) for track_id, profile in active.items()})
        return events

    def _update_occupancy(self, polygon: PolygonROI, active: dict[int, VehicleProfile], frame_idx: int, now: float) -> list[dict]:
        events: list[dict] = []
        slots: list[OccupancySlot] = self._slots.setdefault(polygon.id, [])
        candidates = [p for p in active.values() if point_in_polygon(foot_point(p.bbox), polygon.points)]
        matched_slots: set[int] = set()
        matched_candidates: set[int] = set()
        for slot_index, slot in enumerate(slots):
            best_score, best_index = cfg.IOU_MATCH - 1e-9, -1
            for candidate_index, candidate in enumerate(candidates):
                if candidate_index not in matched_candidates and iou(slot.bbox, candidate.bbox) > best_score:
                    best_score, best_index = iou(slot.bbox, candidate.bbox), candidate_index
            if best_index >= 0:
                candidate = candidates[best_index]
                matched_slots.add(slot_index)
                matched_candidates.add(best_index)
                slot.bbox, slot.last_seen_frame = candidate.bbox, frame_idx
                slot.consecutive_matches += 1
                slot.confirmed = slot.confirmed or slot.consecutive_matches >= cfg.MIN_OCCUPY_FRAMES
                if candidate.type != "unknown":
                    slot.vehicle_type = candidate.type
        for index, candidate in enumerate(candidates):
            if index not in matched_candidates:
                slots.append(OccupancySlot(candidate.bbox, now, frame_idx, 1, present_at_startup=(frame_idx == 0), vehicle_type=candidate.type if candidate.type != "unknown" else None))
        remove: list[int] = []
        for index, slot in enumerate(slots):
            if index in matched_slots:
                continue
            slot.consecutive_matches = 0
            if frame_idx - slot.last_seen_frame >= cfg.FREE_FRAMES:
                remove.append(index)
                if slot.confirmed and not slot.present_at_startup:
                    events.append({"event": "parking_ended", "zone": polygon.id, "vehicle_type": slot.vehicle_type, "duration_s": round(now - slot.occupied_since, 1), "ts": _iso_now()})
        for index in reversed(remove):
            slots.pop(index)
        restricted = "restricted" in polygon.functions
        for slot in slots:
            if not slot.confirmed or slot.present_at_startup:
                continue
            dwell = now - slot.occupied_since
            if restricted and not slot.violation_fired and dwell > cfg.RESTRICTED_MIN_SEC:
                slot.violation_fired = True
                events.append({"event": "violation", "zone": polygon.id, "vehicle_type": slot.vehicle_type, "duration_s": round(dwell, 1), "ts": _iso_now()})
            elif not restricted and not slot.overstay_fired and dwell > cfg.OVERSTAY_SEC:
                slot.overstay_fired = True
                events.append({"event": "overstay", "zone": polygon.id, "vehicle_type": slot.vehicle_type, "duration_s": round(dwell, 1), "ts": _iso_now()})
        return events

    def _check_count(self, polygon: PolygonROI, active: dict[int, VehicleProfile]) -> list[dict]:
        count = sum(point_in_polygon(foot_point(p.bbox), polygon.points) for p in active.values())
        was_over, is_over = self._occupancy_over.get(polygon.id, False), count > cfg.OCCUPANCY_MAX
        self._occupancy_over[polygon.id] = is_over
        return [{"event": "overcrowding", "zone": polygon.id, "count": count, "ts": _iso_now()}] if is_over and not was_over else []

    def _check_line(self, line: LineROI, active: dict[int, VehicleProfile]) -> list[dict]:
        events: list[dict] = []
        counts = self._line_counts[line.id]
        for track_id, profile in active.items():
            previous, current = self._previous.get(track_id), foot_point(profile.bbox)
            already = self._crossed.setdefault(track_id, set())
            if previous is None or line.id in already or not segment_crosses(previous, current, line.a, line.b):
                continue
            direction = "left_to_right" if line_side(previous, line.a, line.b) < 0 else "right_to_left"
            counts[direction][profile.type] += 1
            already.add(line.id)
            events.append({"event": "crossing", "zone": line.id, "track_id": track_id, "vehicle_type": profile.type, "direction": direction, "count": counts[direction][profile.type], "ts": _iso_now()})
        return events
