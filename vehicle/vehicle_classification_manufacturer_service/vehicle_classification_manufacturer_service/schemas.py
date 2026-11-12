"""Standalone profile and result schemas; intentionally contain no removed attributes."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from .manufacturer.tracking import TrackRecognition


@dataclass
class VehicleProfile:
    track_id: int
    bbox: list[int]
    first_seen: float = 0.0
    last_seen: float = 0.0
    first_frame: int = 0
    last_frame: int = 0
    frames_seen: int = 0
    status: str = "active"
    recognition: TrackRecognition = field(default_factory=TrackRecognition)

    @property
    def type(self):
        """The VLM body category exposed in API results."""
        return self.recognition.body_type

    def recognition_reason(self):
        results = [a["result"] for a in self.recognition.attempts if "result" in a]
        reasons = [a.get("error", "") for a in self.recognition.attempts]
        reasons += [a.get("reason", "") for a in results]
        for key in ("manufacturer", "vehicle_type", "color"):
            if len({a[key] for a in results if a[key] != "unknown"}) > 1:
                reasons.append(key + "_conflict")
        if self.recognition.status == "unknown" and not self.recognition.attempts:
            reasons.append("no_usable_crop")
        return ";".join(dict.fromkeys(r for r in reasons if r))

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.recognition.to_dict(),
            "detector_vehicle_type": None,
            "recognition_reason": self.recognition_reason(),
            "track_id": self.track_id,
            "bbox": list(self.bbox),
            "vehicle_type": self.type,
            "type_confidence": None,
            "vehicle_type_source": "local_lfm" if self.recognition.status == "recognized" else "none",
            "result_schema_version": 2,
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
    events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
