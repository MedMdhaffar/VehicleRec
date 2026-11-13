"""Versioned scalar review join using the existing fenced Redis journal.

No model or crop can be invoked by replay. No manufacturer DB1 publication is
enabled: the current VLM/brand registrations have no approved DB1 binding.
"""
from hashlib import sha256
import json

from contracts.db1.reference import canonical_json_bytes
from ..aggregator import Aggregator, Branch
from ..colour_metadata import ColourMetadata
from ..db1_rows.core import require
from ..redis_scalar.codec import (bounded_json, pack_life, pack_receipt, pack_attempt,
                                  unpack_life, unpack_class_samples, unpack_attempt)
from ..redis_scalar.journal import Journal, PRODUCE
from ..redis_scalar.worker import Worker
from ..vehicle_frame import VehicleBoxObservation
from .policy import evaluate

SOURCE, RESULT = 'engine.evidence.manufacturer.v1', 'results.manufacturer.v1'
VERSION = 'vehicle-manufacturer-review-v1'
DB1_GAP = 'missing_approved_vlm_registration_and_brand_catalogue_mapping'


class ReviewCodec:
    def __init__(self, base):
        self.base, self.org = base, base.org

    def encode(self, closed):
        require(closed.blocker is None and closed.manufacturer_json is not None,
                'BLOCKED_ENGINE_EVIDENCE', 'original manufacturer closure required')
        life = closed.owner.lifetime
        body = dict(life=pack_life(life),
                    classes=[dict(receipt=pack_receipt(s.receipt), label=s.label, confidence=s.confidence) for s in closed.classes],
                    measurements=[pack_attempt(m) for m in closed.measurements],
                    evidence=json.loads(closed.manufacturer_json))
        raw = canonical_json_bytes(dict(schema_version=VERSION, org_id=str(self.org),
            event_id=self.base.event_id(life, 'manufacturer.review'),
            body_sha256=sha256(canonical_json_bytes(body)).hexdigest(), body=body))
        self.decode(raw)
        return raw

    def decode(self, raw):
        value = bounded_json(raw)
        require(set(value) == {'schema_version','org_id','event_id','body_sha256','body'}
                and value['schema_version'] == VERSION and value['org_id'] == str(self.org),
                'INVALID_MANUFACTURER', 'closed version and trusted org required')
        body = value['body']
        require(type(body) is dict and set(body) == {'life','classes','measurements','evidence'}
                and sha256(canonical_json_bytes(body)).hexdigest() == value['body_sha256'],
                'HASH_MISMATCH', 'closed manufacturer body/hash required')
        # Reuse canonical v1 life/receipt/class/batch validation, without adding
        # unversioned fields to existing T1 v2 or Plate v3 messages.
        life = unpack_life(body['life'])
        require(life.context.org_id == self.org and value['event_id'] == self.base.event_id(life, 'manufacturer.review'),
                'PARENT_MISMATCH', 'manufacturer event binding differs')
        self.base.encode('vehicle.closed', self.base.event_id(life, 'vehicle.closed'), dict(
            life=body['life'], classes=body['classes'], plate='disabled', manufacturer='unsupported',
            colour_batch_id=self.base.event_id(life,'colour.batch')))
        self.base.encode('colour.batch', self.base.event_id(life,'colour.batch'), dict(
            life=body['life'], complete=True, expected_attempt_ids=[m['attempt_id'] for m in body['measurements']],
            measurements=body['measurements']))
        decision = evaluate(life, body['evidence'])
        return value, life, decision

    def join(self, raw, *, plate=None):
        value, life, decision = self.decode(raw)
        body = value['body']
        colour = ColourMetadata().finalize(life, tuple(unpack_attempt(m,life) for m in body['measurements']))
        branches = (Branch('colour','enabled',colour),
                    Branch('plate','disabled') if plate is None else Branch('plate','enabled',plate),
                    Branch('manufacturer','enabled',decision))
        aggregator = Aggregator(identity_policy=self.base.identity_policy, validation=self.base.validation)
        method = aggregator.join if plate is None else aggregator.join_with_plate
        return method(VehicleBoxObservation(life,(),'unknown',0.0),
                      unpack_class_samples(body['classes'],life), branches)


class ReviewJournal(Journal):
    def __init__(self, client, *, codec, namespace, consumer, **options):
        super().__init__(client, namespace=namespace, trusted_org=codec.org,
                         role='aggregator', consumer=consumer, **options)
        self.prefix = f'{namespace}:{{manufacturer-v1.{self.org_text(codec)}}}:'
        self.group = 'manufacturer-review-v1'
        self.lease, self.fence, self.state = (self.prefix + name for name in ('lease','fence','journal'))

    @staticmethod
    def org_text(codec):
        return str(codec.org)

    def key(self, suffix):
        require(suffix in {SOURCE, RESULT}, 'WRONG_STREAM', 'manufacturer review streams only')
        return self.prefix + suffix


class ReviewWorker(Worker):
    def __init__(self, client, *, codec, namespace, consumer='manufacturer-review', **options):
        self.client, self.codec = client, codec
        self.journal = ReviewJournal(client, codec=codec, namespace=namespace, consumer=consumer, **options)
        self.sources = [SOURCE]
        self.cursor, self.prefer_pending, self.next_source = {SOURCE:'0-0'}, {SOURCE:True}, 0

    def process(self, source, entry_id, fields):
        require(set(fields) == {b'json'}, 'INVALID_MANUFACTURER', 'one JSON scalar message required')
        raw = fields[b'json']
        value, life, decision = self.codec.decode(raw)
        marker, digest = 'seen:'+value['event_id'], sha256(raw).hexdigest()
        old = self.journal.seen(marker)
        require(old is None or old.decode() == digest, 'PAYLOAD_CONFLICT', 'manufacturer event changed')
        if old is not None:
            self.journal.handoff(source=source, entry_id=entry_id, marker=marker, digest=digest)
            return
        joined = self.codec.join(raw)
        evidence = value['body']['evidence']
        output = dict(schema_version=VERSION, event_id=value['event_id'], request_id=evidence['request_id'],
                      vehicle=evidence['binding']['vehicle'], receipt=evidence['binding']['receipt'],
                      crop_sha256=evidence['binding']['crop_sha256'], provenance=evidence['binding']['provenance'],
                      status=decision.status, make=decision.make, vehicle_model=None, confidence=None,
                      reason=decision.reason, db1_status='review_only', db1_gap=DB1_GAP,
                      parent_colour_sha256=joined.delivery.digest, source_sha256=digest)
        self.journal.handoff(source=source, entry_id=entry_id, marker=marker, digest=digest,
            vehicle=evidence['request_id'], state=dict(status='review_only', original_json=raw.decode(), result=output),
            outputs=[(RESULT, canonical_json_bytes(output))])


def publish(client, journal, raw, stream_id):
    return client.eval(PRODUCE, 1, journal.key(SOURCE), stream_id, raw, journal.max_stream)
