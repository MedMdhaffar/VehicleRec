"""ROI config, geometry helpers, and analytics state for Sprint C.

All computation is pure geometry on existing detection boxes — no new models.
"""
from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np

if TYPE_CHECKING:
    from live_feed.schemas import VehicleProfile

# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class PolygonROI:
    id: str
    name: str
    points: list[list[int]]     # [[x,y], ...] native pixels
    functions: list[str]        # subset of {read_zone, parking, restricted, occupancy}


@dataclass
class LineROI:
    id: str
    name: str
    a: list[int]                # [x, y]
    b: list[int]                # [x, y]
    count: bool = True


@dataclass
class RoiConfig:
    camera: int = 0
    frame_size: list[int] = field(default_factory=lambda: [1920, 1080])
    polygons: list[PolygonROI] = field(default_factory=list)
    lines: list[LineROI] = field(default_factory=list)

    @classmethod
    def load(cls, cam_id: str) -> "RoiConfig":
        """Load from live_feed/rois/ch{cam_id}.json; return empty config if missing."""
        from live_feed.config import ROI_DIR
        path = Path(ROI_DIR) / f"ch{cam_id}.json"
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
            polygons = [
                PolygonROI(
                    id=p["id"],
                    name=p.get("name", p["id"]),
                    points=p["points"],
                    functions=p.get("functions", []),
                )
                for p in data.get("polygons", [])
            ]
            lines = [
                LineROI(
                    id=l["id"],
                    name=l.get("name", l["id"]),
                    a=l["a"],
                    b=l["b"],
                    count=l.get("count", True),
                )
                for l in data.get("lines", [])
            ]
            return cls(
                camera=data.get("camera", 0),
                frame_size=data.get("frame_size", [1920, 1080]),
                polygons=polygons,
                lines=lines,
            )
        except Exception:
            return cls()

    @staticmethod
    def save(cam_id: str, data: dict) -> None:
        """Write raw dict as JSON to live_feed/rois/ch{cam_id}.json."""
        from live_feed.config import ROI_DIR
        path = Path(ROI_DIR) / f"ch{cam_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))

    def polygons_with_function(self, fn: str) -> list[PolygonROI]:
        return [p for p in self.polygons if fn in p.functions]


@dataclass
class OccupancySlot:
    bbox: list[int]
    occupied_since: float       # time.monotonic()
    last_seen_frame: int
    consecutive_matches: int = 0
    confirmed: bool = False
    present_at_startup: bool = False
    overstay_fired: bool = False
    violation_fired: bool = False
    plate: str | None = None
    vehicle_type: str | None = None


# ── Geometry helpers ──────────────────────────────────────────────────────────

def foot_point(bbox: list[int]) -> tuple[int, int]:
    """Bottom-center of bounding box."""
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) // 2, y2)


def point_in_polygon(pt: tuple[int, int], poly_points: list[list[int]]) -> bool:
    """Return True if pt is inside or on the boundary of the polygon."""
    if len(poly_points) < 3:
        return False
    contour = np.array(poly_points, dtype=np.float32).reshape((-1, 1, 2))
    result = cv2.pointPolygonTest(contour, (float(pt[0]), float(pt[1])), False)
    return result >= 0


def iou(box_a: list[int], box_b: list[int]) -> float:
    """Intersection-over-union of two [x1,y1,x2,y2] boxes."""
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if inter == 0:
        return 0.0
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def line_side(pt: tuple[int, int], a: list[int], b: list[int]) -> float:
    """Signed cross product (b-a)×(pt-a). Positive on one side, negative on the other."""
    return float((b[0] - a[0]) * (pt[1] - a[1]) - (b[1] - a[1]) * (pt[0] - a[0]))


def segment_crosses(
    p_prev: tuple[int, int],
    p_cur: tuple[int, int],
    a: list[int],
    b: list[int],
) -> bool:
    """Return True if the path step p_prev→p_cur crosses segment a→b.

    Requires a sign flip of line_side AND that the foot-point path crosses
    within the segment's bounding box (AABB guard).
    """
    s_prev = line_side(p_prev, a, b)
    s_cur  = line_side(p_cur, a, b)
    if s_prev == 0.0 or s_cur == 0.0:
        return False
    if (s_prev > 0) == (s_cur > 0):
        return False
    # AABB guard: does the foot path's bounding box overlap the segment?
    px_min = min(p_prev[0], p_cur[0])
    px_max = max(p_prev[0], p_cur[0])
    py_min = min(p_prev[1], p_cur[1])
    py_max = max(p_prev[1], p_cur[1])
    sx_min = min(a[0], b[0])
    sx_max = max(a[0], b[0])
    sy_min = min(a[1], b[1])
    sy_max = max(a[1], b[1])
    return not (px_max < sx_min or px_min > sx_max or py_max < sy_min or py_min > sy_max)


# ── RoiAnalytics ──────────────────────────────────────────────────────────────

_DIRECTION = {True: "left_to_right", False: "right_to_left"}


class RoiAnalytics:
    """Stateful ROI analytics engine: occupancy tracking + line counting.

    Lives inside CameraPipeline. No models — pure geometry on existing boxes.
    """

    def __init__(self, config: RoiConfig, started_at: float) -> None:
        self.config = config
        self._started_at = started_at
        # parking/restricted slots keyed by polygon.id
        self._slots: dict[str, list[OccupancySlot]] = {}
        # line counts: {line_id: {"left_to_right": Counter, "right_to_left": Counter}}
        self._line_counts: dict[str, dict[str, Counter]] = {}
        # previous foot-point per track_id (for line crossing)
        self._prev_foot: dict[int, tuple[int, int]] = {}
        # set of line_ids already crossed per track (de-dupe)
        self._crossed: dict[int, set[str]] = {}
        # current overcrowding edge-trigger per polygon id
        self._occupancy_over: dict[str, bool] = {}
        self._init_slots()
        self._init_line_counts()

    def _init_slots(self) -> None:
        from live_feed.config import FREE_FRAMES  # noqa: F401
        for poly in self.config.polygons:
            if "parking" in poly.functions or "restricted" in poly.functions:
                self._slots[poly.id] = []

    def _init_line_counts(self) -> None:
        for line in self.config.lines:
            if line.count:
                self._line_counts[line.id] = {
                    "left_to_right": Counter(),
                    "right_to_left": Counter(),
                }

    def reload(self, new_config: RoiConfig, started_at: float) -> None:
        """Replace config; reset all state (caller is /reload-rois)."""
        self.config = new_config
        self._started_at = started_at
        self._slots = {}
        self._line_counts = {}
        self._prev_foot = {}
        self._crossed = {}
        self._occupancy_over = {}
        self._init_slots()
        self._init_line_counts()

    def process_frame(
        self,
        active_profiles: dict[int, "VehicleProfile"],
        frame_idx: int,
        now: float,
    ) -> list[dict]:
        """Process one frame. Returns list of zone_event dicts (§10 format)."""
        events: list[dict] = []

        # Clean up state for tracks that have disappeared
        gone = self._prev_foot.keys() - active_profiles.keys()
        for tid in gone:
            self._prev_foot.pop(tid, None)
            self._crossed.pop(tid, None)

        # Occupancy: parking + restricted polygons
        for poly in self.config.polygons:
            if "parking" in poly.functions or "restricted" in poly.functions:
                events.extend(self._update_occupancy(poly, active_profiles, frame_idx, now))

        # Occupancy count: occupancy polygons
        for poly in self.config.polygons:
            if "occupancy" in poly.functions:
                events.extend(self._check_occupancy_count(poly, active_profiles, now))

        # Line counting
        for line in self.config.lines:
            if line.count:
                events.extend(self._check_lines(line, active_profiles, frame_idx, now))

        # Update prev foot-points for ALL active profiles (must be after line check)
        for tid, profile in active_profiles.items():
            self._prev_foot[tid] = foot_point(profile.bbox)

        return events

    def _update_occupancy(
        self,
        poly: PolygonROI,
        active_profiles: dict[int, "VehicleProfile"],
        frame_idx: int,
        now: float,
    ) -> list[dict]:
        from live_feed.config import FREE_FRAMES, IOU_MATCH, MIN_OCCUPY_FRAMES, OVERSTAY_SEC, RESTRICTED_MIN_SEC

        events: list[dict] = []
        slots = self._slots.setdefault(poly.id, [])
        is_restricted = "restricted" in poly.functions

        # Candidates: vehicles whose foot_point is inside this polygon
        candidates = [
            p for p in active_profiles.values()
            if point_in_polygon(foot_point(p.bbox), poly.points)
        ]

        matched_slots: set[int] = set()
        matched_candidates: set[int] = set()

        # Greedy IoU match: slots → candidates
        for si, slot in enumerate(slots):
            best_iou = IOU_MATCH - 1e-9
            best_ci = -1
            for ci, cand in enumerate(candidates):
                if ci in matched_candidates:
                    continue
                score = iou(slot.bbox, cand.bbox)
                if score > best_iou:
                    best_iou = score
                    best_ci = ci
            if best_ci >= 0:
                matched_slots.add(si)
                matched_candidates.add(best_ci)
                cand = candidates[best_ci]
                slot.bbox = cand.bbox
                slot.last_seen_frame = frame_idx
                slot.consecutive_matches += 1
                if not slot.confirmed and slot.consecutive_matches >= MIN_OCCUPY_FRAMES:
                    slot.confirmed = True
                # snapshot plate/type if available
                if cand.plate.locked and cand.plate.number:
                    slot.plate = cand.plate.number
                if cand.type != "unknown":
                    slot.vehicle_type = cand.type

        # New unmatched candidates → create slots
        for ci, cand in enumerate(candidates):
            if ci not in matched_candidates:
                new_slot = OccupancySlot(
                    bbox=cand.bbox,
                    occupied_since=now,
                    last_seen_frame=frame_idx,
                    consecutive_matches=1,
                    present_at_startup=(frame_idx == 0),
                    plate=cand.plate.number if cand.plate.locked else None,
                    vehicle_type=cand.type if cand.type != "unknown" else None,
                )
                slots.append(new_slot)

        # Handle unmatched slots: reset consecutive count; finalize if too long absent
        to_remove: list[int] = []
        for si, slot in enumerate(slots):
            if si in matched_slots:
                continue
            slot.consecutive_matches = 0
            if (frame_idx - slot.last_seen_frame) >= FREE_FRAMES:
                to_remove.append(si)
                if slot.confirmed and not slot.present_at_startup:
                    duration = now - slot.occupied_since
                    events.append({
                        "event": "parking_ended",
                        "zone": poly.id,
                        "vehicle_type": slot.vehicle_type,
                        "plate": slot.plate,
                        "duration_s": round(duration, 1),
                        "ts": _iso_now(),
                    })

        for si in reversed(to_remove):
            slots.pop(si)

        # Fire events on confirmed, non-startup slots
        for slot in slots:
            if not slot.confirmed or slot.present_at_startup:
                continue
            dwell = now - slot.occupied_since
            if is_restricted:
                if not slot.violation_fired and dwell > RESTRICTED_MIN_SEC:
                    slot.violation_fired = True
                    events.append({
                        "event": "violation",
                        "zone": poly.id,
                        "vehicle_type": slot.vehicle_type,
                        "plate": slot.plate,
                        "duration_s": round(dwell, 1),
                        "ts": _iso_now(),
                    })
            else:
                if not slot.overstay_fired and dwell > OVERSTAY_SEC:
                    slot.overstay_fired = True
                    events.append({
                        "event": "overstay",
                        "zone": poly.id,
                        "vehicle_type": slot.vehicle_type,
                        "plate": slot.plate,
                        "duration_s": round(dwell, 1),
                        "ts": _iso_now(),
                    })

        return events

    def _check_occupancy_count(
        self,
        poly: PolygonROI,
        active_profiles: dict[int, "VehicleProfile"],
        now: float,
    ) -> list[dict]:
        from live_feed.config import OCCUPANCY_MAX

        count = sum(
            1 for p in active_profiles.values()
            if point_in_polygon(foot_point(p.bbox), poly.points)
        )
        was_over = self._occupancy_over.get(poly.id, False)
        is_over = count > OCCUPANCY_MAX
        self._occupancy_over[poly.id] = is_over

        if is_over and not was_over:
            return [{
                "event": "overcrowding",
                "zone": poly.id,
                "count": count,
                "ts": _iso_now(),
            }]
        return []

    def _check_lines(
        self,
        line: LineROI,
        active_profiles: dict[int, "VehicleProfile"],
        frame_idx: int,
        now: float,
    ) -> list[dict]:
        events: list[dict] = []
        counts = self._line_counts.setdefault(line.id, {
            "left_to_right": Counter(),
            "right_to_left": Counter(),
        })

        for tid, profile in active_profiles.items():
            prev = self._prev_foot.get(tid)
            if prev is None:
                continue
            cur = foot_point(profile.bbox)

            # De-dupe: one crossing per track per line
            already = self._crossed.setdefault(tid, set())
            if line.id in already:
                continue

            if segment_crosses(prev, cur, line.a, line.b):
                # Direction: sign of line_side for the current position
                side_prev = line_side(prev, line.a, line.b)
                direction = _DIRECTION[side_prev < 0]
                vtype = profile.type if profile.type != "unknown" else "unknown"
                counts[direction][vtype] += 1
                already.add(line.id)
                events.append({
                    "event": "crossing",
                    "zone": line.id,
                    "track_id": tid,
                    "vehicle_type": vtype,
                    "plate": profile.plate.number if profile.plate.locked else None,
                    "direction": direction,
                    "count": counts[direction][vtype],
                    "ts": _iso_now(),
                })

        return events


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ── Self-test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # point_in_polygon
    square = [[0, 0], [100, 0], [100, 100], [0, 100]]
    assert point_in_polygon((50, 50), square), "center should be inside"
    assert point_in_polygon((0, 0), square), "corner should be on boundary"
    assert not point_in_polygon((150, 50), square), "outside should be False"
    print("point_in_polygon: OK")

    # iou
    assert iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1.0, "identical boxes = 1.0"
    assert iou([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0, "no overlap = 0.0"
    score = iou([0, 0, 10, 10], [5, 5, 15, 15])
    assert abs(score - 25 / 175) < 1e-6, f"partial overlap mismatch: {score}"
    print("iou: OK")

    # foot_point
    assert foot_point([10, 20, 30, 40]) == (20, 40)
    print("foot_point: OK")

    # line_side — in screen coords (y down), (5,5) is below y=0 line → positive cross product
    assert line_side((5, 5), [0, 0], [10, 0]) > 0
    assert line_side((5, -5), [0, 0], [10, 0]) < 0
    print("line_side: OK")

    # segment_crosses
    a, b = [0, 50], [100, 50]   # horizontal line at y=50
    assert segment_crosses((50, 30), (50, 70), a, b), "vertical path crossing horizontal line"
    assert not segment_crosses((50, 10), (50, 40), a, b), "path above line, no cross"
    assert not segment_crosses((110, 30), (110, 70), a, b), "path outside x-range"
    print("segment_crosses: OK")

    print("\nAll self-tests passed.")
