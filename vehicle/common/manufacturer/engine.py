"""Engine-owned first eligible vehicle crop, with durable at-most-once admission.

The SQLite journal is a single host execution fence, not a distributed queue.
An uncertain start stays uncertain. Pixel bytes never enter it or Redis.
"""
from hashlib import sha256
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import time

from contracts.db1.reference import canonical_json_bytes
from ..db1_rows.core import require
from ..redis_scalar.codec import pack_life, pack_receipt

OUTCOMES = {'completed', 'timed_out', 'failed', 'execution_uncertain', 'disabled', 'unknown'}


def hashed(value):
    return sha256(canonical_json_bytes(value)).hexdigest()


def vehicle_binding(life):
    c = life.context
    require(not c.database_keys_resolved and c.environment == 'development',
            'RESOLVED_CONTEXT', 'manufacturer review requires explicit unresolved context')
    return dict(context=dict(org_id=str(c.org_id), camera_id=c.camera.camera_id,
        camera_timezone=c.camera.timezone, valid_from=c.camera.valid_from.isoformat(),
        valid_to=c.camera.valid_to.isoformat() if c.camera.valid_to else None,
        run_id=c.run_id, source_kind=c.source_kind, started_at=c.started_at.isoformat(),
        service_version=c.service_version, detector=dict(model_name=c.resolution.detector.model_name,
            version=c.resolution.detector.version, task='detect'), resolution_provenance=c.resolution.provenance),
        service_track_id=life.service_track_id, tracker_epoch=life.tracker_epoch,
        raw_track_id=life.raw_track_id, lifetime_sequence=life.lifetime_sequence)


def validate_evidence(value):
    require(type(value) is dict and set(value) == {'binding', 'request_id', 'outcome', 'text', 'seconds'},
            'INVALID_MANUFACTURER', 'closed manufacturer evidence required')
    b = value['binding']
    require(type(b) is dict and set(b) == {'vehicle', 'receipt', 'bbox', 'shape', 'crop_sha256', 'frame_sha256', 'provenance'},
            'INVALID_MANUFACTURER', 'closed crop binding required')
    require(value['request_id'] == hashed(b) and value['outcome'] in OUTCOMES,
            'MANUFACTURER_BINDING_MISMATCH', 'request digest/outcome differs')
    require(type(value['text']) is str and len(value['text'].encode()) <= 2048
            and (value['outcome'] == 'completed' or value['text'] == ''),
            'INVALID_MANUFACTURER', 'bounded model text only on completed execution')
    import math
    require(type(value['seconds']) in (int, float, Decimal) and math.isfinite(value['seconds'])
            and 0 <= value['seconds'] <= 65, 'INVALID_MANUFACTURER', 'bounded latency required')
    if b['receipt'] is not None:
        require(type(b['bbox']) is list and len(b['bbox']) == 4
                and all(type(x) in (int,float,Decimal) and math.isfinite(x) and x >= 0 and x == int(x) for x in b['bbox'])
                and b['bbox'][2] > b['bbox'][0] and b['bbox'][3] > b['bbox'][1],
                'INVALID_MANUFACTURER', 'original crop geometry required')
        require(b['shape'] == [b['bbox'][3]-b['bbox'][1], b['bbox'][2]-b['bbox'][0], 3],
                'INVALID_MANUFACTURER', 'crop shape differs from bbox')
        import re
        require(all(type(b[k]) is str and re.fullmatch('[a-f0-9]{64}', b[k]) for k in ('crop_sha256','frame_sha256','provenance')),
                'INVALID_MANUFACTURER', 'artifact/crop/frame fingerprints required')
    else:
        require(value['outcome'] in {'disabled','unknown'} and b['bbox'] == [] and b['shape'] == []
                and b['crop_sha256'] is None and b['frame_sha256'] is None,
                'INVALID_MANUFACTURER', 'nonattempt cannot claim crop execution')


class ManufacturerLedger:
    def __init__(self, *, enabled=False, runtime=None, journal=None, max_lifetimes=16, stop=None):
        require(type(enabled) is bool and type(max_lifetimes) is int and 1 <= max_lifetimes <= 16,
                'INVALID_CAPACITY', 'explicit bounded manufacturer capability')
        require(not enabled or (runtime is not None and journal is not None),
                'MISSING_MANUFACTURER_RUNTIME', 'opt-in requires local runtime and durable journal')
        self.enabled, self.runtime = enabled, runtime
        self.stop = stop
        self.max_lifetimes, self.results, self.calls = max_lifetimes, {}, 0
        self.execute = self._execute
        self.db = None
        if enabled:
            path = Path(journal)
            require(path.parent.is_dir(), 'MISSING_JOURNAL', 'explicit existing journal directory required')
            self.db = sqlite3.connect(path)
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.execute('CREATE TABLE IF NOT EXISTS attempts (vehicle TEXT PRIMARY KEY, binding TEXT NOT NULL, result TEXT)')
            self.db.commit()

    def observe(self, life, receipt, bbox, crop, frame_sha256):
        if not self.enabled or (self.stop is not None and self.stop.is_set()):
            return
        key = hashed(vehicle_binding(life))
        if key in self.results:
            return
        previous = self.db.execute('SELECT binding,result FROM attempts WHERE vehicle=?', (key,)).fetchone()
        if previous is not None:
            b = json.loads(previous[0])
            self.results[key] = json.loads(previous[1]) if previous[1] else dict(
                binding=b, request_id=hashed(b), outcome='execution_uncertain', text='', seconds=0.0)
            return
        # Deterministic selection: first observed clipped vehicle bbox >=48px
        # on each axis and <=8MiB. Later crops cannot replace or repeat it.
        if min(crop.shape[:2]) < 48 or crop.nbytes > 8*1024*1024:
            return
        require(len(self.results) < self.max_lifetimes, 'CAPACITY_EXCEEDED', 'manufacturer lifetime limit')
        b = dict(vehicle=vehicle_binding(life), receipt=pack_receipt(receipt), bbox=list(bbox),
                 shape=list(crop.shape), crop_sha256=sha256(crop.tobytes()).hexdigest(),
                 frame_sha256=frame_sha256, provenance=self.runtime.provenance)
        # Exclusive transaction reserves the unique lifetime BEFORE model entry.
        # A second Engine/host sees the reservation and can never invoke it again.
        self.db.execute('BEGIN IMMEDIATE')
        try:
            count = self.db.execute('SELECT count(*) FROM attempts').fetchone()[0]
            require(count < self.max_lifetimes, 'CAPACITY_EXCEEDED', 'durable manufacturer lifetime bound')
            self.db.execute('INSERT INTO attempts VALUES (?,?,NULL)', (key, canonical_json_bytes(b).decode()))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        self.calls += 1
        started = time.monotonic()
        try:
            outcome, text = self.execute(lambda: self.runtime.infer(crop))
        except TimeoutError:
            outcome, text = 'timed_out', ''
        except Exception:
            outcome, text = 'failed', ''
        result = dict(binding=b, request_id=hashed(b), outcome=outcome, text=text,
                      seconds=min(65.0, time.monotonic()-started))
        validate_evidence(result)
        self.db.execute('UPDATE attempts SET result=? WHERE vehicle=?', (canonical_json_bytes(result).decode(), key))
        self.db.commit()
        self.results[key] = result

    @staticmethod
    def _execute(call):
        from ..engine_plate import native_access
        with native_access():
            return call()

    def terminal(self, life):
        key = hashed(vehicle_binding(life))
        if key in self.results:
            return json.loads(canonical_json_bytes(self.results[key]))
        b = dict(vehicle=vehicle_binding(life), receipt=None, bbox=[], shape=[],
                 crop_sha256=None, frame_sha256=None,
                 provenance=self.runtime.provenance if self.enabled else None)
        return dict(binding=b, request_id=hashed(b), outcome='unknown' if self.enabled else 'disabled', text='', seconds=0.0)

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None
