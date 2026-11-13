"""Immutable observation interface shared by classification and future consumers."""
from dataclasses import dataclass

from .frame_receipt import FrameReceipt
from .vehicle_observation import VehicleLifetime


@dataclass(frozen=True, slots=True)
class VehicleBoxObservation:
    lifetime: VehicleLifetime
    bbox: tuple[int, int, int, int]
    vehicle_type: str
    type_confidence: float

    @property
    def service_track_id(self) -> int:
        return self.lifetime.service_track_id

    @property
    def raw_track_id(self) -> int:
        return self.lifetime.raw_track_id


@dataclass(frozen=True, slots=True)
class VehicleFrame:
    receipt: FrameReceipt
    vehicles: tuple[VehicleBoxObservation, ...]
