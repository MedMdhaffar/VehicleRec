"""Explicit supplied registration results; no lookups or caller authorization.

Production/test resolution is supplied by an upstream boundary. Development
contexts may explicitly remain unresolved, without fabricated numeric keys.
The synthetic marker is provenance, not a proof of production authorization.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

BIGINT_MAX = 2**63 - 1


def positive_bigint(value: int, name: str) -> None:
    if type(value) is not int or not 1 <= value <= BIGINT_MAX:
        raise ValueError(f"{name} must be a positive signed BIGINT")


def aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be aware")
    return value.astimezone(timezone.utc)


def nonempty(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be explicit nonempty text")


@dataclass(frozen=True, slots=True)
class CameraRegistration:
    org_id: UUID
    camera_id: str
    camera_key: int | None
    timezone: str
    valid_from: datetime
    valid_to: datetime | None

    def __post_init__(self):
        if not isinstance(self.org_id, UUID):
            raise ValueError("org_id must be a UUID")
        nonempty(self.camera_id, "camera_id")
        if self.camera_key is not None:
            positive_bigint(self.camera_key, "camera_key")
        nonempty(self.timezone, "camera timezone")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("unknown registered camera timezone") from exc
        object.__setattr__(self, "valid_from", aware_utc(self.valid_from))
        if self.valid_to is not None:
            object.__setattr__(self, "valid_to", aware_utc(self.valid_to))
            if self.valid_to <= self.valid_from:
                raise ValueError("camera validity interval is empty")


@dataclass(frozen=True, slots=True)
class ModelRegistration:
    model_key: int | None
    model_name: str
    version: int
    task: str

    def __post_init__(self):
        if self.model_key is not None:
            positive_bigint(self.model_key, "model_key")
        nonempty(self.model_name, "model_name")
        if type(self.version) is not int or self.version < 1 or self.task != "detect":
            raise ValueError("run detector requires positive version and detect task")


@dataclass(frozen=True, slots=True)
class ResolutionInfo:
    run_key: int | None
    lane_key: int | None
    lane_name: str
    detector: ModelRegistration
    source_media_key: int | None
    provenance: str
    synthetic: bool

    def __post_init__(self):
        for name in ("run_key", "lane_key"):
            if getattr(self, name) is not None:
                positive_bigint(getattr(self, name), name)
        if self.lane_name != "vehicle":
            raise ValueError("run lane must be vehicle")
        if not isinstance(self.detector, ModelRegistration):
            raise ValueError("detector registration must be supplied")
        if self.source_media_key is not None:
            positive_bigint(self.source_media_key, "source_media_key")
        nonempty(self.provenance, "resolution provenance")
        if type(self.synthetic) is not bool:
            raise ValueError("synthetic provenance must be explicit")


@dataclass(frozen=True, slots=True)
class RunContext:
    org_id: UUID
    camera: CameraRegistration
    run_id: str
    source_kind: str
    started_at: datetime
    service_version: str
    resolution: ResolutionInfo
    environment: str

    def __post_init__(self):
        if not isinstance(self.camera, CameraRegistration) or not isinstance(self.resolution, ResolutionInfo):
            raise ValueError("registered camera and resolution must be supplied")
        if not isinstance(self.org_id, UUID) or self.org_id != self.camera.org_id:
            raise ValueError("organization must match registered camera")
        nonempty(self.run_id, "run_id")
        nonempty(self.service_version, "service_version")
        object.__setattr__(self, "started_at", aware_utc(self.started_at))
        if not (self.camera.valid_from <= self.started_at and
                (self.camera.valid_to is None or self.started_at < self.camera.valid_to)):
            raise ValueError("run start is outside registered camera version")
        if self.source_kind not in {"file", "rtsp"}:
            raise ValueError("source_kind must be file or rtsp")
        if self.environment != "development" and self.source_kind == "file" and self.resolution.source_media_key is None:
            raise ValueError("file run requires supplied input media resolution")
        if self.environment not in {"production", "test", "development"}:
            raise ValueError("environment must be explicit production, test or development")
        if self.environment != "development" and not self.database_keys_resolved:
            raise ValueError("registered camera/model/run/lane keys must be resolved")
        if self.environment == "development" and any(key is not None for key in (
            self.camera.camera_key, self.resolution.detector.model_key,
            self.resolution.run_key, self.resolution.lane_key, self.resolution.source_media_key,
        )):
            raise ValueError("unprovisioned development context must not claim database resolution")
        if self.environment == "production" and self.resolution.synthetic:
            raise ValueError("synthetic resolution cannot enter production context")

    @property
    def database_keys_resolved(self) -> bool:
        return all(value is not None for value in (
            self.camera.camera_key, self.resolution.detector.model_key,
            self.resolution.run_key, self.resolution.lane_key,
        )) and (self.source_kind != 'file' or self.resolution.source_media_key is not None)

    @property
    def time_basis(self) -> str:
        return "host_replay_receipt" if self.source_kind == "file" else "host_receipt"
