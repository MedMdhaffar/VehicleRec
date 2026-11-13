"""Opt-in scalar LPR v3 contract. No transport, model selectors or DB1 records.

The JSON representation is closed, bounded and round-trippable. Identities are
claims from a future trusted Engine host; hashes do not authenticate a producer.
"""
from dataclasses import asdict, dataclass, fields
import hashlib
import json
import re

from .mohamed_plate_policy import (
    DinoScores, Marker, OCR, ReferenceScore, Thresholds, TUNIS_MODES,
)

VERSION = "vehicle-mohamed-lpr-v3"
POLICY_VERSION = "mohamed-lpr-plate-policy-phase1-v1"
ROLES = ("category_ocr", "visual", "registration", "marker", "left", "right")
OUTCOMES = ("succeeded", "failed", "unknown", "missing")
MAX_BYTES = 65536


def require(condition, code):
    if not condition:
        raise ValueError(code)  # Never include OCR or caller values in diagnostics.


def name(value):
    require(type(value) is str and 0 < len(value.encode()) <= 256, "invalid_identity")


def sha(value):
    require(type(value) is str and re.fullmatch("[0-9a-f]{64}", value) is not None, "invalid_sha256")


def positive(value, limit=100000):
    require(type(value) is int and 0 < value <= limit, "invalid_positive_integer")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(asdict(value))).hexdigest()


@dataclass(frozen=True)
class Crop:
    org_id: str
    run_id: str
    camera_id: str
    session_id: str
    vehicle_lifetime_id: str
    plate_track_id: str
    plate_track_epoch: str
    association_id: str
    candidate_id: str
    frame_id: str
    frame_sha256: str
    crop_id: str
    crop_sha256: str
    width: int
    height: int
    generation: int

    def __post_init__(self):
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name.endswith("sha256"):
                sha(value)
            elif f.name in {"width", "height", "generation"}:
                positive(value)
            else:
                name(value)

    @property
    def parent(self):
        return tuple(getattr(self, k) for k in (
            "org_id", "run_id", "camera_id", "session_id", "vehicle_lifetime_id",
            "plate_track_id", "plate_track_epoch", "association_id"))


@dataclass(frozen=True)
class Dependency:
    operation_id: str
    result_sha256: str

    def __post_init__(self):
        sha(self.operation_id)
        sha(self.result_sha256)


@dataclass(frozen=True)
class Provenance:
    """Actual execution identity, including OCR dictionary/export in config hash.

    config_sha256 fingerprints a retained effective manifest, not a config path.
    artifact_sha256 fingerprints a retained manifest for multi-file model exports.
    """
    model_id: str
    artifact_sha256: str
    config_sha256: str
    preprocessing_sha256: str
    runtime_id: str
    device: str

    def __post_init__(self):
        for f in fields(self):
            (sha if f.name.endswith("sha256") else name)(getattr(self, f.name))

    @property
    def model_ref(self):
        return digest(self)


@dataclass(frozen=True)
class ReferenceIndex:
    index_id: str
    index_sha256: str
    entries: tuple[tuple[str, str], ...]

    def __post_init__(self):
        name(self.index_id)
        sha(self.index_sha256)
        require(type(self.entries) is tuple and 0 < len(self.entries) <= 64, "invalid_index")
        for entry in self.entries:
            require(type(entry) is tuple and len(entry) == 2, "invalid_index_entry")
            ReferenceScore(*entry, 0.0)
        require(len({e[0] for e in self.entries}) == len(self.entries), "duplicate_reference")


@dataclass(frozen=True)
class Request:
    version: str
    policy_version: str
    operation_id: str
    sequence: int
    crop: Crop
    role: str
    tunis_mode: str
    thresholds: Thresholds
    dependencies: tuple[Dependency, ...]
    region_xyxy: tuple[int, int, int, int]

    def __post_init__(self):
        require(self.version == VERSION and self.policy_version == POLICY_VERSION, "wrong_version")
        sha(self.operation_id)
        positive(self.sequence, 6)
        require(type(self.crop) is Crop and type(self.thresholds) is Thresholds, "invalid_request_binding")
        require(type(self.role) is str and self.role in ROLES, "invalid_role")
        require(type(self.tunis_mode) is str and self.tunis_mode in TUNIS_MODES, "explicit_tunis_mode_required")
        require(type(self.dependencies) is tuple and len(self.dependencies) <= 4
                and all(type(d) is Dependency for d in self.dependencies), "invalid_dependencies")
        ids = tuple(d.operation_id for d in self.dependencies)
        require(ids == tuple(sorted(set(ids))), "noncanonical_dependencies")
        box = self.region_xyxy
        require(type(box) is tuple and len(box) == 4 and all(type(n) is int for n in box)
                and 0 <= box[0] < box[2] <= self.crop.width
                and 0 <= box[1] < box[3] <= self.crop.height, "invalid_region")
        body = asdict(self)
        del body["operation_id"]
        require(self.operation_id == hashlib.sha256(canonical(body)).hexdigest(), "operation_identity_mismatch")


def request(crop, role, *, sequence, tunis_mode, thresholds, dependencies=(), region_xyxy=None):
    body = dict(version=VERSION, policy_version=POLICY_VERSION, sequence=sequence,
                crop=asdict(crop), role=role, tunis_mode=tunis_mode, thresholds=asdict(thresholds),
                dependencies=[asdict(d) for d in sorted(dependencies, key=lambda d: d.operation_id)],
                region_xyxy=region_xyxy or (0, 0, crop.width, crop.height))
    operation_id = hashlib.sha256(canonical(body)).hexdigest()
    return Request(VERSION, POLICY_VERSION, operation_id, sequence, crop, role, tunis_mode,
                   thresholds, tuple(sorted(dependencies, key=lambda d: d.operation_id)), tuple(body["region_xyxy"]))


@dataclass(frozen=True)
class Result:
    request: Request
    execution_id: str
    executor_ref: str
    outcome: str
    retired: bool
    provenance: Provenance | None = None
    error_code: str | None = None
    raw_text: str | None = None
    confidence: float | None = None
    detected: bool | None = None
    bbox: tuple[int, int, int, int] | None = None
    scores: tuple[ReferenceScore, ...] = ()
    reference_index: ReferenceIndex | None = None

    def __post_init__(self):
        require(type(self.request) is Request, "invalid_result_request")
        name(self.execution_id)
        name(self.executor_ref)
        require(type(self.outcome) is str and self.outcome in OUTCOMES, "invalid_outcome")
        require(self.retired is True, "execution_not_retired")
        require(self.provenance is None or type(self.provenance) is Provenance, "invalid_provenance")
        require(type(self.scores) is tuple and all(type(s) is ReferenceScore for s in self.scores), "invalid_scores")
        require(self.reference_index is None or type(self.reference_index) is ReferenceIndex, "invalid_reference_index")
        if self.outcome == "succeeded":
            require(self.provenance is not None and self.error_code is None, "successful_provenance_required")
        else:
            name(self.error_code)
            require(self.raw_text is None and self.confidence is None and self.detected is None
                    and self.bbox is None and not self.scores and self.reference_index is None, "unsuccessful_payload")
        role = self.request.role
        if role == "visual":
            require(self.raw_text is None and self.confidence is None and self.detected is None
                    and self.bbox is None, "visual_payload_mismatch")
            if self.outcome == "succeeded":
                require(self.reference_index is not None, "reference_index_required")
                require(tuple((s.reference_id, s.category) for s in self.scores) == self.reference_index.entries,
                        "incomplete_or_mismatched_reference_scores")
        elif role == "marker":
            require(self.raw_text is None and not self.scores and self.reference_index is None, "marker_payload_mismatch")
        else:
            require(self.detected is None and self.bbox is None and not self.scores
                    and self.reference_index is None, "ocr_payload_mismatch")
        self.evidence()  # Reuse unchanged Phase 1 payload validation, including finite native scores.

    def evidence(self):
        op = self.request.operation_id
        model = self.provenance.model_ref if self.provenance else None
        if self.request.role == "visual":
            index = digest(self.reference_index) if self.reference_index else None
            return DinoScores(op, self.outcome, self.scores, model, index)
        if self.request.role == "marker":
            return Marker(op, self.outcome, self.detected, self.confidence, self.bbox, model)
        return OCR(op, self.outcome, self.raw_text, self.confidence, model)


def encode(value):
    require(type(value) in {Request, Result}, "unsupported_message")
    raw = canonical(dict(version=VERSION, kind="request" if type(value) is Request else "result", body=asdict(value)))
    require(len(raw) <= MAX_BYTES, "message_too_large")
    return raw


def _closed(cls, value):
    require(type(value) is dict and set(value) == {f.name for f in fields(cls)}, "closed_shape_required")
    return dict(value)


def _request(value):
    v = _closed(Request, value)
    v["crop"] = Crop(**_closed(Crop, v["crop"]))
    v["thresholds"] = Thresholds(**_closed(Thresholds, v["thresholds"]))
    require(type(v["dependencies"]) is list and len(v["dependencies"]) <= 4, "invalid_dependencies")
    v["dependencies"] = tuple(Dependency(**_closed(Dependency, d)) for d in v["dependencies"])
    require(type(v["region_xyxy"]) is list, "invalid_region")
    v["region_xyxy"] = tuple(v["region_xyxy"])
    return Request(**v)


def decode(raw):
    require(type(raw) is bytes and len(raw) <= MAX_BYTES, "bounded_bytes_required")

    def pairs(items):
        require(len(dict(items)) == len(items), "duplicate_json_key")
        return dict(items)

    try:
        v = json.loads(raw, object_pairs_hook=pairs,
                       parse_constant=lambda _: require(False, "nonfinite_json"))
        require(type(v) is dict and set(v) == {"version", "kind", "body"}, "closed_envelope_required")
        require(v["version"] == VERSION and v["kind"] in {"request", "result"}, "wrong_version_or_kind")
        if v["kind"] == "request":
            return _request(v["body"])
        b = _closed(Result, v["body"])
        b["request"] = _request(b["request"])
        if b["provenance"] is not None:
            b["provenance"] = Provenance(**_closed(Provenance, b["provenance"]))
        require(type(b["scores"]) is list and len(b["scores"]) <= 64, "invalid_scores")
        b["scores"] = tuple(ReferenceScore(**_closed(ReferenceScore, s)) for s in b["scores"])
        if b["bbox"] is not None:
            require(type(b["bbox"]) is list, "invalid_bbox")
            b["bbox"] = tuple(b["bbox"])
        if b["reference_index"] is not None:
            index = _closed(ReferenceIndex, b["reference_index"])
            require(type(index["entries"]) is list and len(index["entries"]) <= 64
                    and all(type(e) is list for e in index["entries"]), "invalid_index_entries")
            index["entries"] = tuple(tuple(e) for e in index["entries"])
            b["reference_index"] = ReferenceIndex(**index)
        return Result(**b)
    except (TypeError, KeyError, OverflowError, RecursionError, UnicodeError, ValueError):
        raise ValueError("invalid_lpr_contract") from None
