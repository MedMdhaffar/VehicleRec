"""Shared pure P1 geometry policy; no fixture harness or runtime dependencies."""
import math
from typing import Any

def _box(box: list[Any]) -> tuple[float, float, float, float]:
    if not isinstance(box, list) or len(box) != 4:
        raise ValueError("bbox must contain four numbers")
    values = tuple(float(item) for item in box)
    if not all(math.isfinite(item) for item in values):
        raise ValueError("bbox contains a nonfinite value")
    return values  # type: ignore[return-value]


def associate_plate(
    plate_bbox: list[Any],
    vehicles: list[dict[str, Any]],
    *,
    minimum_containment: float = 0.8,
    minimum_margin: float = 0.1,
    containment_weight: float = 0.7,
    distance_weight: float = 0.3,
) -> dict[str, Any]:
    """Reference vehicle/plate geometry profile v0; thresholds are provisional."""
    px1, py1, px2, py2 = _box(plate_bbox)
    plate_width, plate_height = px2 - px1, py2 - py1
    if plate_width <= 0 or plate_height <= 0:
        return {"status": "unassociated", "selected_track_id": None, "scores": []}
    plate_area = plate_width * plate_height
    pcx, pcy = (px1 + px2) / 2, (py1 + py2) / 2
    scores: list[dict[str, Any]] = []
    for vehicle in vehicles:
        vx1, vy1, vx2, vy2 = _box(vehicle["bbox"])
        width, height = vx2 - vx1, vy2 - vy1
        if width <= 0 or height <= 0:
            continue
        centre_inside = vx1 <= pcx <= vx2 and vy1 <= pcy <= vy2
        intersection = max(0.0, min(px2, vx2) - max(px1, vx1)) * max(0.0, min(py2, vy2) - max(py1, vy1))
        containment = intersection / plate_area
        if not centre_inside or containment < minimum_containment:
            continue
        target_x, target_y = (vx1 + vx2) / 2, vy1 + 0.75 * height
        diagonal = math.hypot(width, height)
        distance = min(1.0, math.hypot(pcx - target_x, pcy - target_y) / diagonal)
        score = containment_weight * containment + distance_weight * (1.0 - distance)
        scores.append({
            "track_id": int(vehicle["track_id"]),
            "containment": round(containment, 12),
            "distance": round(distance, 12),
            "score": round(score, 12),
        })
    scores.sort(key=lambda item: (-item["score"], item["track_id"]))
    if not scores:
        return {"status": "unassociated", "selected_track_id": None, "scores": []}
    if len(scores) == 1:
        return {"status": "associated", "selected_track_id": scores[0]["track_id"], "scores": scores}
    margin = scores[0]["score"] - scores[1]["score"]
    if margin + 1e-12 < minimum_margin:
        return {"status": "ambiguous", "selected_track_id": None, "scores": scores}
    return {"status": "associated", "selected_track_id": scores[0]["track_id"], "scores": scores}


