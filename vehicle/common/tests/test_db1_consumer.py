"""Canonical natural consumer and injected transaction/ACK failure semantics."""
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from threading import Lock
import unittest

from contracts.db1.reference import canonical_json_bytes,derive_date_key
from vehicle.common.db1_consumer import NaturalConsumer
from vehicle.common.db1_adapter import DB1Adapter, TrustedTenant, DeliveryResult, DeliveryConflict, DatabaseDeliveryError
from vehicle.common.db1_rows import Dimension, ResolvedModel
from vehicle.common.db1_context import ModelRegistration
from vehicle.common.observation_evidence import RecognitionIdentity
from vehicle.common.redis_scalar.worker import load_request_codec
from vehicle.common.redis_scalar.reducer import reduce_message
from vehicle.common.redis_scalar.plate_codec import join_bodies
from vehicle.common.redis_scalar.synthetic import ORG,IDENTITY_NAMESPACE
from vehicle.common.tests.test_plate_requests import session_fixture,complete_loop
from vehicle.common.tests.test_db1_rows import fixture as resolved_fixture
from vehicle.common.db1_rows.core import Blocked


def delivery_fixture(codec):
    session,crop,calls,closed,colour=session_fixture(codec)
    try:
        _,_,terminal=complete_loop(codec,session)
        _,out=reduce_message(codec.v1.v0,'colour',codec.v1.v0.decode(colour),None)
        state=None
        for raw in (closed,out[0][1],terminal):state,outputs=reduce_message(codec.v1,'aggregator_plate',codec.v1.decode(raw),state)
        return outputs[0][1]
    finally:
        session.adapter.stop();session.adapter.cleanup(discard=True)


def supplied_test_registrations(codec,raw):
    """Explicit labelled fixture claims, never a production registration source."""
    m=codec.v1.decode(raw);c=m.life.context;_,template=resolved_fixture()
    camera=replace(template.run.camera,org_id=c.org_id,camera_id=c.camera.camera_id,timezone=c.camera.timezone,
        valid_from=c.camera.valid_from,valid_to=c.camera.valid_to)
    detector=ModelRegistration(91,c.resolution.detector.model_name,c.resolution.detector.version,'detect')
    dates=tuple(sorted({derive_date_key(f['delivery']['ts'],camera.timezone) for f in m.body['bundle']['facts']}))
    run=replace(template.run,org_id=c.org_id,run_id=c.run_id,camera=camera,detector=detector,
        source_kind='file' if c.source_kind=='file' else 'stream',started_at=c.started_at,service_version=c.service_version,
        date_keys=dates,provenance='explicit injected consumer test registration',synthetic=True)
    models=[];classes=[];colours=[];categories=[]
    for fact in m.body['bundle']['facts']:
        b=fact['delivery'];kind=fact['row_kind']
        if kind=='fact_vehicle':classes.append(Dimension(b['class']['name'],11,True,'vehicle'))
        else:
            model=b['model'] if kind=='fact_vehicle_colour' else b['selected_attempt']['model']
            models.append(ResolvedModel(RecognitionIdentity(**model),81+len(models)))
            if kind=='fact_vehicle_colour' and b['colour']:colours.append(Dimension(b['colour'],21,True))
            if kind=='fact_plate_read' and b['selected_attempt']['plate_category']:categories.append(Dimension(b['selected_attempt']['plate_category'],31,True))
    clip=replace(template.media[0],org_id=c.org_id,run_id=c.run_id,camera_id=c.camera.camera_id,camera_key=camera.camera_key)
    return replace(template,run=run,classes=tuple(classes),colours=tuple(colours),plate_categories=tuple(categories),models=tuple(models),media=(clip,))


class TransactionAdapter(DB1Adapter):
    """Injected durable-history stand-in; no native PostgreSQL claim."""
    def __init__(self,validation):
        super().__init__(validation=validation);self.history={};self.facts={};self.lock=Lock();self.failure=None;self.calls=0
    def deliver(self,connection,entry,*,tenant,resolutions):
        payload=self.prepare(entry,tenant);identity=entry.logical_identity.canonical
        with self.lock:
            self.calls+=1
            if self.failure=='rollback':self.failure=None;raise DatabaseDeliveryError(None,commit_outcome='not_committed')
            old=self.history.get(identity)
            if old is not None and old!=payload:raise DeliveryConflict('injected changed accepted payload')
            disposition='identical' if old else 'new'
            proposed={f.sql.value()['uid']:f.sql.canonical for f in entry.construction.ready_rows}
            self.history[identity]=payload;self.facts.update(proposed)
            if self.failure=='lost':self.failure=None;raise DatabaseDeliveryError(None,commit_outcome='unknown')
            return DeliveryResult(disposition,sha256(payload).hexdigest(),len(proposed),len(entry.construction.facts)-len(proposed))


class ConsumerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rc=load_request_codec(ORG,IDENTITY_NAMESPACE)
        cls.raw=delivery_fixture(cls.rc)
    def setUp(self):
        self.res=supplied_test_registrations(self.rc,self.raw);self.adapter=TransactionAdapter(self.rc.validation)
        self.acks=[];self.connections=[]
        def connection():self.connections.append('app');return nullcontext(object())
        c=self.rc.v1.decode(self.raw).life.context
        self.consumer=NaturalConsumer(codec=self.rc.v1,tenant=TrustedTenant(ORG,'explicit isolated test host'),
            trusted_run=c.run_id,trusted_camera=c.camera.camera_id,resolutions=self.res,
            connection_factory=connection,environment='test',adapter=self.adapter)
    def ack(self):
        self.assertTrue(self.adapter.history);self.assertEqual(len(self.adapter.facts),3);self.acks.append('after-commit')

    def test_canonical_projection_models_timestamps_natural_identity_and_no_sqlite(self):
        e=self.consumer.prepare(self.raw)
        for fact in e.construction.ready_rows:
            self.assertEqual(fact.delivery.value()['ts'],fact.sql.value()['ts'])
            self.assertEqual(fact.delivery.value()['org_id'],fact.sql.value()['org_id'])
        self.assertIn('natural_delivery',e.construction.facts[0].metadata.value())
        self.assertEqual(e.construction.facts[2].delivery.value()['selected_attempt']['model']['model_name'],'synthetic-whole-ocr')
        self.assertEqual(self.consumer.process(self.raw,self.ack).disposition,'new')
        self.assertEqual(self.consumer.process(self.raw,self.ack).disposition,'identical')
        self.assertEqual(len(self.adapter.facts),3)

    def test_commit_lost_reply_reconcile_history_before_ack(self):
        self.adapter.failure='lost'
        with self.assertRaises(DatabaseDeliveryError) as error:self.consumer.process(self.raw,self.ack)
        self.assertEqual(error.exception.commit_outcome,'unknown');self.assertEqual(self.acks,[])
        self.assertEqual(len(self.adapter.facts),3)
        self.assertEqual(self.consumer.process(self.raw,self.ack).disposition,'identical');self.assertEqual(self.acks,['after-commit'])

    def test_death_after_commit_before_ack_and_ack_lost_reply(self):
        def death(phase):
            if phase=='committed':raise RuntimeError('injected worker death')
        self.consumer.hook=death
        with self.assertRaises(RuntimeError):self.consumer.process(self.raw,self.ack)
        self.assertEqual(self.acks,[]);self.consumer.hook=lambda p:None
        def lost_ack():self.ack();raise ConnectionError('injected lost XACK reply')
        with self.assertRaises(ConnectionError):self.consumer.process(self.raw,lost_ack)
        self.assertEqual(self.consumer.process(self.raw,self.ack).disposition,'identical');self.assertEqual(len(self.adapter.facts),3)

    def test_rollback_parent_child_atomic_and_no_ack(self):
        self.adapter.failure='rollback'
        with self.assertRaises(DatabaseDeliveryError):self.consumer.process(self.raw,self.ack)
        self.assertFalse(self.adapter.history);self.assertFalse(self.adapter.facts);self.assertFalse(self.acks)
        self.consumer.process(self.raw,self.ack)

    def test_concurrent_identical_retries(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes=list(pool.map(lambda _:self.consumer.process(self.raw,self.ack).disposition,range(2)))
        self.assertEqual(sorted(outcomes),['identical','new']);self.assertEqual(len(self.adapter.facts),3)

    def changed(self):
        body=deepcopy(self.rc.v1.decode(self.raw).body)
        body['closed']['classes'][0]['confidence']=.81
        bundle=join_bodies(self.rc.v1,body['closed'],body['colour'],body['plate'])
        body.update(bundle=bundle.payload.value(),bundle_sha256=bundle.digest)
        return self.rc.v1.make('db1.delivery.v1',body)

    def test_changed_accepted_payload_conflict_no_ack(self):
        self.consumer.process(self.raw,self.ack)
        with self.assertRaises(DeliveryConflict):self.consumer.process(self.changed(),self.ack)
        self.assertEqual(len(self.acks),1)

    def test_missing_registration_trusted_binding_and_production_synthetic_rejected_before_connect(self):
        for field,value in [('models',()),('classes',()),('media',())]:
            self.consumer.resolutions=replace(self.res,**{field:value})
            with self.assertRaises(Blocked):self.consumer.process(self.raw,self.ack)
            self.assertFalse(self.connections)
        self.consumer.resolutions=self.res;self.consumer.run='other-run'
        with self.assertRaises(Blocked):self.consumer.process(self.raw,self.ack)
        self.consumer.run=self.res.run.run_id;self.consumer.environment='production'
        with self.assertRaises(ValueError):self.consumer.process(self.raw,self.ack)
        self.assertFalse(self.connections)

    def test_complete_delivery_strict_decode_and_uncertain_commit_no_early_ack(self):
        for raw in (b'{}',self.raw+b' not-json'):
            with self.assertRaises(Blocked):self.consumer.process(raw,self.ack)
        self.assertFalse(self.connections);self.assertFalse(self.acks)

    def test_changed_provenance_with_identical_facts_conflicts(self):
        from vehicle.common.redis_scalar.plate_codec import unpack_transcript,claims
        from vehicle.common.plate_metadata import PlateMetadata
        self.consumer.process(self.raw,self.ack)
        message=self.rc.v1.decode(self.raw);body=deepcopy(message.body)
        body['plate']['transcript']['candidates'][0]['detector_confidence']=.85
        t=unpack_transcript(body['plate']['transcript'],message.life)
        result=PlateMetadata().evaluate(message.life,t).result
        body['plate'].update(claims=claims(result),provenance_sha256=result.provenance.digest)
        bundle=join_bodies(self.rc.v1,body['closed'],body['colour'],body['plate'])
        self.assertEqual(bundle.payload.value()['facts'],message.body['bundle']['facts'])
        body.update(bundle=bundle.payload.value(),bundle_sha256=bundle.digest)
        changed=self.rc.v1.make('db1.delivery.v1',body)
        with self.assertRaises(DeliveryConflict):self.consumer.process(changed,self.ack)
        self.assertEqual(len(self.acks),1)
