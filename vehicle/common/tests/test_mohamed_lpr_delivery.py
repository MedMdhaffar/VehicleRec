"""Synthetic LPR; owned native Redis; opt-in disposable PG16 transactions."""
from dataclasses import replace, asdict
from hashlib import sha256
import json, os, unittest
from types import SimpleNamespace
from uuid import uuid4

from . import test_mohamed_lpr_engine as injected
from .test_colour_aggregator import fixture as colour_fixture
from .test_db1_consumer import delivery_fixture
from ..aggregator import ClassSample
from ..db1_rows.core import Blocked
from ..db1_adapter import DB1Adapter, DatabaseDeliveryError, DeliveryConflict, ReferenceRejected
from ..db1_consumer import RedisDB1Consumer
from ..observation_evidence import RecognitionIdentity
from ..real_video.engine import ClosedScalars
from ..real_video.test_database import fixture_module
from ..redis_scalar.worker import load_codec, load_request_codec
from ..redis_scalar.synthetic import IDENTITY_NAMESPACE
from ..mohamed_lpr_delivery import TunisDeliveryCodec, OCRBinding, ProjectionGap
from ..mohamed_lpr_delivery_stream import TunisDeliveryWorker, SOURCE
from ..mohamed_lpr_stream_codec import Scope, STREAMS
from ..mohamed_lpr_streams import LiveEngine, PlateWorker, EnginePeer, pump
from ..mohamed_lpr_redis_runtime import OwnedRedis
from ..mohamed_lpr_test_database import OwnedDeliveryDatabase

SERVER=os.environ.get('VEHICLE_REDIS_SERVER','/home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server')


class Fixture:
    def __init__(self, client, category='tunis', text='001T0007', mode='legacy_whole'):
        self.f=injected.EngineAdapterTests('runTest');self.f.setUp()
        bound=self.f.bound()  # Real v3 path does not set the separate v2 admission flag.
        c=bound.scalar
        self.scope=Scope(c.org_id,c.run_id,c.camera_id,uuid4().hex)
        self.ns='tunis5-'+uuid4().hex[:20]
        self.runtime=injected.Runtime(category,text)
        self.live=LiveEngine(client,namespace=self.ns,scope=self.scope,runtime=self.runtime)
        self.crop=self.live.add(bound,tunis_mode=mode)
        self.plate=PlateWorker(client,codec=self.live.codec,namespace=self.ns,consumer='plate')
        self.engine=EnginePeer(client,live=self.live,namespace=self.ns,consumer='engine')
        self.plate.setup();self.engine.setup();self.live.flush_offers();pump(self.plate,self.engine)
        model=self.runtime.expected('registration')
        self.binding=OCRBinding(model.model_ref,RecognitionIdentity('injected-ocr-'+model.model_ref,1,'ocr'))
        self.codec=TunisDeliveryCodec(base=load_codec(self.f.life.context.org_id,IDENTITY_NAMESPACE),scope=self.scope,bindings=(self.binding,))
        owner,measurements,classes=colour_fixture()
        self.closed=ClosedScalars(owner,classes,measurements)
        self.context=dict(bound=self.live.slots[self.crop.session_id].adapters[1].bound,closed=self.closed,
                          expected_sessions=(self.crop.session_id,))
        self.worker=TunisDeliveryWorker(client,codec=self.codec,namespace=self.ns,contexts={self.crop.session_id:self.context},
                                       proof_reader=self.plate.journal.read_state)
        self.worker.setup()
        self.client=client

    def project(self):
        self.worker.step()
        rows=self.client.xrange(self.worker.journal.key('db1.delivery.v1'))
        return rows[-1][1][b'json'] if rows else None

    def close(self):
        for w in (self.plate,self.engine,self.worker):
            w.journal.release()
        self.f.doCleanups()


class ProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.owned=OwnedRedis(SERVER).__enter__();cls.client=cls.owned.client
    @classmethod
    def tearDownClass(cls):
        cls.owned.__exit__(None,None,None)
        assert cls.owned.cleaned
    def fixture(self, **kw):
        f=Fixture(self.client,**kw);self.addCleanup(f.close);return f

    def test_canonical_leading_zeros_model_route_frame_and_full_v3_evidence(self):
        f=self.fixture();calls=dict(f.runtime.calls);raw=f.project();m=f.codec.decode(raw)
        plate=next(x['delivery'] for x in m.body['bundle']['facts'] if x['row_kind']=='fact_plate_read')
        self.assertEqual(plate['selected_attempt']['plate_text'],'001 تونس 0007')
        self.assertEqual(plate['selected_attempt']['model'],f.binding.identity.policy())
        self.assertEqual((plate['selected_attempt']['route'],plate['attempts']),('local_private',1))
        self.assertEqual(plate['ts'],plate['selected_attempt']['ts'])
        evidence=m.body['bundle']['plate_provenance']['evidence']
        self.assertEqual(evidence['proof_json'],m.body['plate']['proof_json'])
        self.assertIn('reference_index',evidence['proof_json'])
        self.assertEqual(dict(f.runtime.calls),calls)
        source=self.client.xrange(f.worker.journal.key(SOURCE))[-1][1][b'json']
        self.client.xadd(f.worker.journal.key(SOURCE),{'json':source})
        f.worker.step()
        self.assertEqual(self.client.xlen(f.worker.journal.key('db1.delivery.v1')),1)
        self.assertEqual(dict(f.runtime.calls),calls)

    def test_unresolved_and_unmapped_categories_do_not_produce_delivery(self):
        for category,text in [('tunis','001T00077'),('idara','001 - 002'),('nt','0007'),('shorta','0007')]:
            with self.subTest(category=category):
                f=self.fixture(category=category,text=text)
                self.assertIsNone(f.project())
                self.assertEqual(f.worker.journal.read_state(f.crop.session_id)['status'],'review_only')
                self.assertEqual(self.client.xpending(f.worker.journal.key(SOURCE),f.worker.journal.group)['pending'],0)

    def test_marker_first_is_explicit_projection_gap(self):
        f=self.fixture(mode='marker_first')
        self.assertIsNone(f.project())
        self.assertEqual(f.worker.journal.read_state(f.crop.session_id)['reason'],'mode_or_recheck_requires_delivery_contract')

    def test_v3_admission_preserves_original_unset_v2_flag(self):
        f=self.fixture();body=f.codec.decode(f.project()).body
        self.assertIs(body['plate']['candidate']['crop_accepted'],False)
        selected=f.codec.plate_reader(body['plate'],f.f.life).selected
        self.assertEqual(selected.model.policy(),f.binding.identity.policy())
        self.assertEqual(len(f.live.captured(f.crop.session_id)),4)

    def test_wrong_frame_vehicle_model_and_proof_rejected(self):
        f=self.fixture();raw=f.project();body=f.codec.decode(raw).body
        import copy
        for change in ('frame','vehicle','model','proof'):
            altered=copy.deepcopy(body)
            if change=='frame':altered['plate']['candidate']['receipt']['frame_index']+=1
            if change=='vehicle':altered['life']['service_track_id']+=1
            if change=='model':altered['plate']['model_binding']['model_ref']='0'*64
            if change=='proof':altered['plate']['proof_json']='[]'
            with self.assertRaises((Blocked,ValueError)):
                f.codec.make(altered)

    def test_projection_publication_failure_repairs_before_ack_without_recognition(self):
        f=self.fixture();calls=dict(f.runtime.calls)
        def crash(phase):
            if phase=='published':raise RuntimeError('injected worker failure')
        f.worker.journal.hook=crash
        with self.assertRaises(RuntimeError):f.project()
        before=self.client.xrange(f.worker.journal.key('db1.delivery.v1'))
        self.assertEqual(self.client.xpending(f.worker.journal.key(SOURCE),f.worker.journal.group)['pending'],1)
        f.worker.journal.hook=lambda p:None
        f.worker.step()
        self.assertEqual(self.client.xrange(f.worker.journal.key('db1.delivery.v1')),before)
        self.assertEqual(dict(f.runtime.calls),calls)

    def test_conflicting_sealed_retry_stays_pending_without_second_delivery(self):
        from ..mohamed_lpr_stream_codec import hashed
        f=self.fixture();f.project();calls=dict(f.runtime.calls)
        raw=self.client.xrange(f.worker.journal.key(SOURCE))[-1][1][b'json']
        body=f.live.codec.read(raw)['body']
        body['decision']['normalized_registration']='different synthetic value'
        body['decision_sha256']=hashed(body['decision'])
        changed=f.live.codec.make('plate_result',body)
        self.client.xadd(f.worker.journal.key(SOURCE),{'json':changed})
        with self.assertRaises(Blocked):f.worker.step()
        self.assertEqual(self.client.xlen(f.worker.journal.key('db1.delivery.v1')),1)
        self.assertEqual(dict(f.runtime.calls),calls)


class PostgreSQLTests(ProjectionTests):
    # PG methods use their own disposable database, not injected transactions.
    @classmethod
    def setUpClass(cls):
        fixture_module().provisioning_params(os.environ)
        super().setUpClass()

    def consumer(self,f,raw):
        db=OwnedDeliveryDatabase(f.codec,raw).__enter__();self.addCleanup(db.close)
        natural=db.consumer()
        reader=RedisDB1Consumer(self.client,f.worker.journal,natural,name='db1-test')
        return db,natural,reader

    def test_pg_commit_visible_from_separate_connection_before_real_xack(self):
        f=self.fixture();raw=f.project();db,natural,reader=self.consumer(f,raw)
        entry=natural.prepare(raw);calls=dict(f.runtime.calls);checks=[]
        def boundary(phase):
            if phase=='committed':
                snapshot=db.inspect(entry)
                self.assertEqual(snapshot['rows']['fact_vehicle'],1)
                self.assertEqual(snapshot['rows']['fact_plate_read'],1)
                self.assertEqual((snapshot['acceptances'],snapshot['row_receipts']),(1,3))
                self.assertEqual(self.client.xpending(f.worker.journal.key('db1.delivery.v1'),reader.group)['pending'],1)
                checks.append(snapshot)
        natural.hook=boundary
        self.assertEqual(reader.step().disposition,'new')
        self.assertEqual(len(checks),1)
        self.assertEqual(self.client.xpending(f.worker.journal.key('db1.delivery.v1'),reader.group)['pending'],0)
        self.assertEqual(dict(f.runtime.calls),calls)

    def test_pg_identical_redelivery_has_one_plate_fact_and_receipt(self):
        f=self.fixture();raw=f.project();db,natural,reader=self.consumer(f,raw)
        calls=dict(f.runtime.calls)
        self.assertEqual(reader.step().disposition,'new')
        self.client.xadd(f.worker.journal.key('db1.delivery.v1'),{'json':raw})
        self.assertEqual(reader.step().disposition,'identical')
        state=db.inspect(natural.prepare(raw))
        self.assertEqual((state['rows']['fact_plate_read'],state['acceptances'],state['row_receipts']),(1,1,3))
        self.assertEqual(dict(f.runtime.calls),calls)

    def test_pg_real_rollback_retains_pending_then_retry_commits_once(self):
        f=self.fixture();raw=f.project();db,natural,reader=self.consumer(f,raw)
        class Rollback(DB1Adapter):
            def _apply(self,cursor,entry,payload):
                super()._apply(cursor,entry,payload)
                cursor.execute('SELECT 1 / 0')
        natural.adapter=Rollback(validation=f.codec.validation)
        with self.assertRaises(DatabaseDeliveryError):reader.step()
        state=db.inspect(natural.prepare(raw))
        self.assertEqual((sum(state['rows'].values()),state['acceptances'],state['row_receipts']),(0,0,0))
        self.assertEqual(self.client.xpending(f.worker.journal.key('db1.delivery.v1'),reader.group)['pending'],1)
        natural.adapter=DB1Adapter(validation=f.codec.validation)
        self.assertEqual(reader.step().disposition,'new')
        self.assertEqual(db.inspect(natural.prepare(raw))['rows']['fact_plate_read'],1)

    def test_pg_changed_logical_identity_payload_keeps_original_and_no_ack(self):
        f=self.fixture();raw=f.project();db,natural,reader=self.consumer(f,raw)
        reader.step();before=db.inspect(natural.prepare(raw));body=f.codec.decode(raw).body
        body['classes'][0]['confidence']=.71
        changed=f.codec.make(body)
        self.client.xadd(f.worker.journal.key('db1.delivery.v1'),{'json':changed})
        with self.assertRaises(DeliveryConflict):reader.step()
        after=db.inspect(natural.prepare(raw))
        self.assertEqual(after['row_uids'],before['row_uids'])
        self.assertEqual(after['acceptance_digests'],before['acceptance_digests'])
        self.assertEqual(self.client.xpending(f.worker.journal.key('db1.delivery.v1'),reader.group)['pending'],1)

    def test_pg_missing_actual_category_or_model_registration_cannot_ack(self):
        from psycopg import sql
        for table,key,column,value in [('dim_plate_category',31,'plate_category_key','private'),
                                       ('dim_model',82,'model_key',None)]:
            f=self.fixture();raw=f.project();db,natural,reader=self.consumer(f,raw)
            with db.fixture.setup.cursor() as cur:
                cur.execute(sql.SQL('DELETE FROM public.{} WHERE {}=%s').format(sql.Identifier(table),sql.Identifier(column)),(key,))
            with self.assertRaises(ReferenceRejected):reader.step()
            self.assertEqual(db.inspect(natural.prepare(raw))['acceptances'],0)
            self.assertEqual(self.client.xpending(f.worker.journal.key('db1.delivery.v1'),reader.group)['pending'],1)

    def test_pg_missing_supplied_registration_stops_before_commit(self):
        f=self.fixture();raw=f.project();db,natural,reader=self.consumer(f,raw)
        for field in ('models','plate_categories'):
            saved=natural.resolutions
            natural.resolutions=replace(saved,**{field:()})
            with self.assertRaises(Blocked):reader.step()
            natural.resolutions=saved
        self.assertEqual(db.inspect(natural.prepare(raw))['acceptances'],0)

    def test_pg_commit_then_worker_failure_retries_history_without_recognition(self):
        f=self.fixture();raw=f.project();db,natural,reader=self.consumer(f,raw);calls=dict(f.runtime.calls)
        def failure(phase):
            if phase=='committed':raise RuntimeError('injected postcommit worker failure')
        natural.hook=failure
        with self.assertRaises(RuntimeError):reader.step()
        self.assertEqual(db.inspect(natural.prepare(raw))['rows']['fact_plate_read'],1)
        self.assertEqual(self.client.xpending(f.worker.journal.key('db1.delivery.v1'),reader.group)['pending'],1)
        natural.hook=lambda phase:None
        self.assertEqual(reader.step().disposition,'identical')
        self.assertEqual(db.inspect(natural.prepare(raw))['row_receipts'],3)
        self.assertEqual(dict(f.runtime.calls),calls)

    def test_pg_v2_original_codec_and_consumer_defaults_still_commit(self):
        old=load_request_codec(__import__('uuid').UUID('12345678-1234-4234-8234-123456789012'),IDENTITY_NAMESPACE)
        raw=delivery_fixture(old)
        with OwnedDeliveryDatabase(old.v1,raw) as db:
            natural=db.consumer();entry=natural.prepare(raw);acks=[]
            result=natural.process(raw,lambda:acks.append(db.inspect(entry)))
            self.assertEqual(result.disposition,'new')
            self.assertEqual(acks[0]['rows']['fact_plate_read'],1)


if __name__=='__main__':unittest.main()
