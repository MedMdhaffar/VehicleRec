"""Standalone profile and result schemas; intentionally contain no removed attributes."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING
from copy import deepcopy

if TYPE_CHECKING:
    from .observations import VehicleFrame
    from vehicle.common.vehicle_observation import VehicleLifetime


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
    _lifetime: VehicleLifetime | None = field(default=None, repr=False)
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

    observation: VehicleFrame | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        # Shared context/receipts are an in-process interface, never legacy JSON.
        return deepcopy({"frame_index": self.frame_index, "active": self.active,
                         "finalized": self.finalized, "events": self.events})
