"""Standalone profile and result schemas; intentionally contain no removed attributes."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class VehicleProfile:
    track_id: int
    bbox: list[int]
    type: str = "unknown"
    type_conf: float = 0.0
    first_seen: float = 0.0
    last_seen: float = 0.0
    first_frame: int = 0
    last_frame: int = 0
    frames_seen: int = 0
    status: str = "active"
    _type_confidence_sums: dict[str, float] = field(default_factory=dict, repr=False)
    _type_observation_counts: dict[str, int] = field(default_factory=dict, repr=False)
    _type_total_observations: int = field(default=0, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "track_id": self.track_id,
            "bbox": list(self.bbox),
            "vehicle_type": self.type,
            "type_confidence": self.type_conf,
            "first_frame": self.first_frame,
            "last_frame": self.last_frame,
            "frames_seen": self.frames_seen,
            "status": self.status,
        }


@dataclass
class FrameUpdate:
    frame_index: int
    active: list[dict[str, Any]]
    finalized: list[dict[str, Any]]
    events: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
