"""Closed scalar transport around the unchanged local v3 contract.

Redis is private evidence transport. Reports must never export message bodies.
Partition binding is configured out of band; hashes are not authentication.
"""
from dataclasses import asdict, dataclass, fields, replace
from hashlib import sha256
import json
from uuid import UUID

from .mohamed_lpr_contract import Crop, VERSION as LOCAL_VERSION, canonical, decode, digest, name, require, sha
from .mohamed_plate_policy import Decision, Thresholds, TUNIS_MODES
from .redis_scalar.codec import bounded_json
from .db1_rows.core import Blocked

VERSION = "vehicle-mohamed-lpr-streams-v3"
STREAMS = {kind: "lpr." + suffix + ".v3" for kind, suffix in {
    "offer": "engine.crops", "requests": "plate.requests", "executions": "engine.executions",
    "seal": "plate.seals", "settled": "engine.settled", "plate_result": "plate.results",
    "diagnostic": "plate.diagnostics", "engine_diagnostic": "engine.diagnostics"}.items()}
MAX_WIRE = 262144


def scalar_json(raw, *, max_bytes=MAX_WIRE, max_depth=32, max_nodes=50000, max_string=4096):
    # P1's bounded reader checks resources/strict JSON but mathematically folds
    # 1.0 into 1. Local v3 IDs hash Python JSON and distinguish those encodings.
    # Retain original numeric types after bounded validation.
    try:
        bounded_json(raw, max_bytes=max_bytes, max_depth=max_depth, max_nodes=max_nodes, max_string=max_string)
        return json.loads(raw)
    except (Blocked, ValueError, TypeError, UnicodeError):
        raise ValueError("invalid_bounded_v3_json") from None


def hashed(value):
    return sha256(canonical(value)).hexdigest()


def closed(value, keys):
    require(type(value) is dict and set(value) == set(keys), "closed_stream_shape_required")


@dataclass(frozen=True)
class Scope:
    org_id: str
    run_id: str
    camera_id: str
    engine_id: str

    def __post_init__(self):
        for value in asdict(self).values():
            name(value)
        require(str(UUID(self.org_id)) == self.org_id, "canonical_org_required")


def crop(value):
    closed(value, (f.name for f in fields(Crop)))
    return Crop(**value)


def local(kind, value):
    return decode(canonical(dict(version=LOCAL_VERSION, kind=kind, body=value)))


def public_bound(bound, *, generation=None):
    """Opaque scalar IDs, independent of resource.handle_id and image filenames.

    Association is the stable T1 vehicle/plate segment; the original per-frame
    association and receipt remain Engine-owned. Frame identity/hash still bind
    every observation separately. No Phase 3 identity/default is changed.
    """
    c = bound.scalar
    segment = [c.org_id, c.run_id, c.camera_id, c.session_id, c.vehicle_lifetime_id,
               c.plate_track_id, c.plate_track_epoch]
    generation = c.generation if generation is None else generation
    c = replace(c, association_id="association-v3-" + hashed(segment),
                candidate_id="candidate-v3-" + hashed([segment, c.frame_id, c.candidate_id]),
                crop_id="crop-v3-" + hashed([segment, c.frame_id, c.crop_sha256, generation]),
                generation=generation)
    return replace(bound, scalar=c)


def seal_body(seal):
    return dict(crop=asdict(seal.crop), operation_results=[list(p) for p in seal.request_results],
                decision_sha256=hashed(asdict(seal.decision)))


def validate_seal(body):
    closed(body, ("crop", "operation_results", "decision_sha256"))
    c = crop(body["crop"])
    sha(body["decision_sha256"])
    pairs = body["operation_results"]
    require(type(pairs) is list and 0 < len(pairs) <= 6, "invalid_seal_set")
    for pair in pairs:
        require(type(pair) is list and len(pair) == 2, "invalid_seal_pair")
        sha(pair[0]); sha(pair[1])
    require(len({p[0] for p in pairs}) == len(pairs), "duplicate_seal_operation")
    return c


class Codec:
    def __init__(self, scope):
        require(type(scope) is Scope, "trusted_scope_required")
        self.scope = scope
        self.org = scope.org_id

    def make(self, kind, body):
        # Normalize tuples to JSON arrays, and validate the same representation
        # a separately hosted worker will receive.
        body = scalar_json(canonical(body))
        self.validate(kind, body)
        event_id = self.event_id(kind, body)
        raw = canonical(dict(version=VERSION, scope=asdict(self.scope), kind=kind,
                             event_id=event_id, body_sha256=hashed(body), body=body))
        require(len(raw) <= MAX_WIRE, "stream_message_too_large")
        return raw

    def event_id(self, kind, body):
        if kind == "diagnostic":
            key = body
        else:
            c = self.binding(kind, body)
            key = [c.session_id, c.generation]
            if kind in {"requests", "executions"}:
                key += [body["batch_id"]]
        return hashed([VERSION, asdict(self.scope), kind, key])

    def binding(self, kind, body):
        if kind in {"requests", "executions"}:
            row = body["items"][0]
            return crop(row["crop"] if kind == "requests" else row["request"]["crop"])
        return crop(body["crop"])

    def validate(self, kind, body):
        require(kind in set(STREAMS) - {"engine_diagnostic"}, "invalid_stream_kind")
        if kind == "diagnostic":
            closed(body, ("code", "source", "input_sha256"))
            name(body["code"])
            require(body["source"] in STREAMS.values(), "invalid_diagnostic_source")
            sha(body["input_sha256"])
            return
        if kind == "offer":
            closed(body, ("crop", "tunis_mode", "thresholds"))
            require(type(body["tunis_mode"]) is str and body["tunis_mode"] in TUNIS_MODES, "explicit_mode_required")
            closed(body["thresholds"], (f.name for f in fields(Thresholds)))
            Thresholds(**body["thresholds"])
        elif kind in {"requests", "executions"}:
            closed(body, ("batch_id", "items"))
            sha(body["batch_id"])
            require(type(body["items"]) is list and 0 < len(body["items"]) <= 2, "bounded_batch_required")
            rows = [local("request" if kind == "requests" else "result", v) for v in body["items"]]
            requests = rows if kind == "requests" else [r.request for r in rows]
            require(all(r.crop == requests[0].crop for r in requests), "mixed_batch_crops")
            require(len({r.operation_id for r in requests}) == len(requests), "duplicate_batch_operation")
            require(body["batch_id"] == hashed(sorted(r.operation_id for r in requests)), "batch_identity_mismatch")
        elif kind in {"seal", "settled"}:
            validate_seal(body)
        else:
            closed(body, ("crop", "operation_results", "decision_sha256", "decision"))
            validate_seal({k: v for k, v in body.items() if k != "decision"})
            closed(body["decision"], (f.name for f in fields(Decision)))
            d = body["decision"]
            require(d["status"] in {"accepted", "unresolved"}
                    and d["category"] in {None, "tunis", "idara", "nt", "shorta"}, "invalid_review_decision")
            require(hashed(d) == body["decision_sha256"], "decision_identity_mismatch")
        c = self.binding(kind, body)
        require((c.org_id, c.run_id, c.camera_id) ==
                (self.scope.org_id, self.scope.run_id, self.scope.camera_id), "stream_partition_mismatch")

    def read(self, raw):
        try:
            v = scalar_json(raw)
            closed(v, ("version", "scope", "kind", "event_id", "body_sha256", "body"))
            require(v["version"] == VERSION and v["scope"] == asdict(self.scope), "stream_scope_mismatch")
            self.validate(v["kind"], v["body"])
            require(v["event_id"] == self.event_id(v["kind"], v["body"])
                    and v["body_sha256"] == hashed(v["body"]), "stream_identity_mismatch")
            return v
        except (TypeError, KeyError, OverflowError, UnicodeError, ValueError):
            raise ValueError("invalid_v3_stream_message") from None
