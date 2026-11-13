"""Explicit supplied registration claims. These records perform no lookups.

Keys are validated service identities, not authorization or object-existence proof.
Media SHA256 describes registered encoded object bytes; crop_sha256 separately
binds retained raw crop bytes. Neither implies an upload occurred.
"""
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID
import re

from ..db1_context import CameraRegistration, ModelRegistration, aware_utc, nonempty, positive_bigint
from ..observation_evidence import RecognitionIdentity


from .core import integer


@dataclass(frozen=True, slots=True)
class Dimension:
    name: str
    key: int
    approved: bool
    group: str | None = None

    def __post_init__(self):
        nonempty(self.name, 'dimension name')
        integer(self.key, 32767, 'SMALLINT dimension key')
        if type(self.approved) is not bool:
            raise ValueError('dimension approval must be explicit')
        if self.group is not None:
            nonempty(self.group, 'dimension group')


@dataclass(frozen=True, slots=True)
class ResolvedModel:
    identity: RecognitionIdentity
    key: int

    def __post_init__(self):
        positive_bigint(self.key, 'model_key')
        if not isinstance(self.identity, RecognitionIdentity):
            raise ValueError('explicit recognition identity required')


@dataclass(frozen=True, slots=True)
class RunResolution:
    org_id: UUID
    run_id: str
    run_key: int
    camera: CameraRegistration
    lane_key: int
    lane_name: str
    detector: ModelRegistration
    source_kind: str
    source_media_key: int | None
    started_at: datetime
    service_version: str
    date_keys: tuple[int, ...]
    provenance: str
    synthetic: bool

    def __post_init__(self):
        if not isinstance(self.org_id, UUID) or not isinstance(self.camera, CameraRegistration):
            raise ValueError('supplied organization/camera required')
        if self.camera.org_id != self.org_id or self.camera.camera_key is None:
            raise ValueError('resolved camera organization/key required')
        if not isinstance(self.detector, ModelRegistration) or self.detector.model_key is None:
            raise ValueError('resolved run detector required')
        integer(self.detector.version, 2147483647, 'detector version')
        positive_bigint(self.run_key, 'run_key')
        integer(self.lane_key, 32767, 'lane_key')
        if self.source_media_key is not None:
            positive_bigint(self.source_media_key, 'source_media_key')
        if self.lane_name != 'vehicle' or self.source_kind not in {'file', 'stream'}:
            raise ValueError('resolved vehicle lane/source required')
        if self.source_kind == 'file' and self.source_media_key is None:
            raise ValueError('resolved file run requires source media')
        for value, name in ((self.run_id, 'run_id'), (self.service_version, 'service_version'),
                            (self.provenance, 'provenance')):
            nonempty(value, name)
        object.__setattr__(self, 'started_at', aware_utc(self.started_at))
        if type(self.synthetic) is not bool or type(self.date_keys) is not tuple:
            raise ValueError('explicit synthetic flag and immutable date catalogue required')
        if len(set(self.date_keys)) != len(self.date_keys):
            raise ValueError('duplicate date registration')
        for key in self.date_keys:
            integer(key, 99991231, 'date_key', 10101)
            datetime(key // 10000, key // 100 % 100, key % 100)


@dataclass(frozen=True, slots=True)
class MediaResolution:
    media_key: int
    org_id: UUID
    run_key: int | None
    camera_key: int
    run_id: str
    camera_id: str
    ts: datetime
    kind: str
    bucket: str
    object_key: str
    content_type: str
    bytes: int
    width: int | None
    height: int | None
    sha256: str
    # Supporting evidence binding; absent only for the input clip.
    service_track_id: int | None
    frame_index: int | None
    crop_sha256: str | None

    def __post_init__(self):
        for name in ('media_key', 'camera_key'):
            positive_bigint(getattr(self, name), name)
        for name in ('run_key', 'service_track_id'):
            if getattr(self, name) is not None:
                positive_bigint(getattr(self, name), name)
        if not isinstance(self.org_id, UUID) or self.kind not in {'vehicle_crop', 'plate_crop', 'clip'}:
            raise ValueError('media organization/kind required')
        for name in ('run_id', 'camera_id', 'bucket', 'object_key', 'content_type'):
            nonempty(getattr(self, name), name)
        if ('://' in self.bucket or '://' in self.object_key or self.object_key.startswith('/')
                or '..' in self.object_key.split('/') or '\\' in self.object_key):
            raise ValueError('registered media must use object-store identity, not URL/path')
        integer(self.bytes, 2**63 - 1, 'media bytes', 0)
        for name in ('width', 'height'):
            if getattr(self, name) is not None:
                integer(getattr(self, name), 2147483647, name)
        for name in ('sha256', 'crop_sha256'):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value)):
                raise ValueError('media fingerprint must be SHA256 hex')
        if self.sha256 is None:
            raise ValueError('registered object fingerprint required')
        if self.kind != 'clip' and any(getattr(self, f) is None for f in ('run_key', 'service_track_id', 'frame_index', 'crop_sha256', 'width', 'height')):
            raise ValueError('registered crop requires explicit supporting evidence metadata')
        if self.frame_index is not None:
            integer(self.frame_index, 2**63 - 1, 'frame_index', 0)
        object.__setattr__(self, 'ts', aware_utc(self.ts))

    def natural(self):
        return dict(org_id=str(self.org_id), run_id=self.run_id, camera_id=self.camera_id,
                    ts=self.ts.isoformat(), kind=self.kind, bucket=self.bucket,
                    object_key=self.object_key, content_type=self.content_type, bytes=self.bytes,
                    width=self.width, height=self.height, sha256=self.sha256)


@dataclass(frozen=True, slots=True)
class Resolutions:
    run: RunResolution
    classes: tuple[Dimension, ...]
    colours: tuple[Dimension, ...]
    plate_categories: tuple[Dimension, ...]
    models: tuple[ResolvedModel, ...]
    media: tuple[MediaResolution, ...]

    def __post_init__(self):
        if not isinstance(self.run, RunResolution):
            raise ValueError('RunResolution required')
        for field, cls in (('classes', Dimension), ('colours', Dimension), ('plate_categories', Dimension),
                           ('models', ResolvedModel), ('media', MediaResolution)):
            values = getattr(self, field)
            if type(values) is not tuple or any(not isinstance(v, cls) for v in values):
                raise ValueError(f'{field} requires immutable resolution records')
            ids = [(v.identity.model_name, v.identity.version) if field == 'models'
                   else v.media_key if field == 'media' else v.name for v in values]
            keys = [v.key for v in values] if field != 'media' else ids
            if field == 'media' and len({(v.bucket, v.object_key) for v in values}) != len(values):
                raise ValueError('duplicate registered media object-store identity')
            if field == 'models' and self.run.detector.model_key in keys:
                raise ValueError('recognition model_key collides with run detector identity')
            if len(set(ids)) != len(ids) or len(set(keys)) != len(keys):
                raise ValueError(f'duplicate {field} resolution')
