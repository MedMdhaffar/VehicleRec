"""Database completion regressions: injected transactions, owned native Redis.

No PostgreSQL connection, real model execution or fixture provisioning occurs.
"""
from contextlib import nullcontext
from hashlib import sha256
import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from redis.exceptions import TimeoutError
from vehicle.common.db1_consumer import NaturalConsumer,RedisDB1Consumer
from vehicle.common.db1_adapter import TrustedTenant
from vehicle.common.real_video.test_database import registrations
from vehicle.common.redis_scalar.synthetic import ORG,IDENTITY_NAMESPACE
from vehicle.common.tests.test_db1_consumer import TransactionAdapter


class FaultClient:
    def __init__(self,actual,mode):self.actual=actual;self.mode=mode;self.failed=False;self.ack_executed=[]
    def __getattr__(self,name):return getattr(self.actual,name)
    def xack(self,*args):
        if not self.failed and self.mode=='before_ack':
            self.failed=True;raise TimeoutError('injected before XACK')
        result=self.actual.xack(*args);self.ack_executed.append(args[-1])
        if not self.failed and self.mode=='after_ack':
            self.failed=True;raise TimeoutError('injected lost XACK reply')
        return result
    def xreadgroup(self,*args,**kwargs):
        if not self.failed and self.mode=='before_next_read' and self.ack_executed:
            self.failed=True;raise TimeoutError('injected before next delivery read')
        return self.actual.xreadgroup(*args,**kwargs)


class RunnerAckTests(unittest.TestCase):
    def setUp(self):
        self.binary=os.environ.get('VEHICLE_REDIS_SERVER')
        if not self.binary or not Path(self.binary).is_file():self.skipTest('explicit existing native Redis binary required')

    def test_runner_finishes_all_targets_when_xack_succeeds_but_reply_is_lost(self):
        from scripts import vehicle_bounded_run as runner
        observed={}
        class InjectedDatabase:
            def __init__(self,video):self.video=video;self.settings=dict(database='injected');self.cleanup={}
            def __enter__(self):return self
            def __exit__(self,*_):self.cleanup=dict(injected_database_closed=True)
            def consumer(self,codec,video):
                adapter=TransactionAdapter(codec.validation);c=video.context
                consumer=NaturalConsumer(codec=codec.v1,tenant=TrustedTenant(c.org_id,'explicit injected database test'),
                    trusted_run=c.run_id,trusted_camera=c.camera.camera_id,resolutions=registrations(video),
                    connection_factory=lambda:nullcontext(object()),environment='test',adapter=adapter)
                observed['adapter']=adapter;return consumer
            def receipts(self):return [sha256(raw).hexdigest() for raw in observed['adapter'].history.values()]
        def database_consumer(client,journal,consumer,**kwargs):
            db=RedisDB1Consumer(client,journal,consumer,**kwargs);db.client=FaultClient(client,'after_ack')
            observed['db']=db;return db
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'result';ns='ack'+uuid4().hex[:16]
            args=['--mode','injected','--transport','plate-requests-v2','--redis-profile','ephemeral','--database','isolated-fixture',
                '--redis-server',self.binary,'--output',str(out),'--org',str(ORG),'--identity-namespace',str(IDENTITY_NAMESPACE),
                '--run',ns,'--camera','owned','--namespace',ns,'--timezone','Africa/Tunis','--frames','1','--settlement-seconds','30']
            with patch('vehicle.common.real_video.test_database.require_test_target'),patch('vehicle.common.real_video.test_database.OwnedDatabase',InjectedDatabase),\
                 patch('vehicle.common.db1_consumer.RedisDB1Consumer',side_effect=database_consumer):
                code=runner.main(args)
            report=json.loads((out/'report.json').read_bytes())
            self.assertIn('adapter',observed,report.get('code'))
            self.assertEqual(len(observed['adapter'].history),2)
            self.assertEqual(len(observed['db'].client.ack_executed),2)
            self.assertEqual(code,0,report.get('code'))
            self.assertEqual(report['outcome'],'passed')
            status=report['database']
            self.assertEqual((status['targets'],status['completed'],status['waiting']),(2,2,0))
            self.assertEqual((status['new'],status['identical'],status['transactions_confirmed']),(2,0,2))
            self.assertEqual((status['ack_callbacks_completed'],status['acknowledgements_reconciled']),(1,1))
            self.assertEqual(status['retry_attempts'],0)
            self.assertEqual(report['reference_model_calls'],0)
            self.assertEqual(report['status']['recognition']['native_starts']['whole_ocr'],1)
            self.assertEqual(report['status']['recognition']['native_starts']['marker'],1)
            self.assertFalse(report['cleanup']['live_pids'])


from vehicle.common.tests import test_redis_scalar_integration as native
from vehicle.common.tests.test_db1_consumer import delivery_fixture,supplied_test_registrations
from vehicle.common.redis_scalar.worker import load_request_codec
from vehicle.common.redis_scalar.journal import Journal
from vehicle.common.db1_consumer import DeliveryProgress
from vehicle.common.db1_adapter import DeliveryConflict
from vehicle.common.real_video.db_delivery import TargetDeliveries,deliver_targets,OBSERVE_ACK
from vehicle.common.db1_rows.core import Blocked
import time


class AckProgressRedisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        native.RedisIntegrationTests.setUpClass.__func__(cls)
        cls.rc=load_request_codec(ORG,IDENTITY_NAMESPACE);cls.raw=delivery_fixture(cls.rc)
    tearDownClass=classmethod(native.RedisIntegrationTests.tearDownClass.__func__)
    setUp=native.RedisIntegrationTests.setUp
    tearDown=native.RedisIntegrationTests.tearDown

    def seed(self,*,raws=None,mode='none'):
        self.ns='dback'+uuid4().hex[:20]
        journal=Journal(None,namespace=self.ns,trusted_org=ORG,role='plate_requests',consumer='db-targets')
        self.adapter=TransactionAdapter(self.rc.validation);c=self.rc.v1.decode(self.raw).life.context
        consumer=NaturalConsumer(codec=self.rc.v1,tenant=TrustedTenant(ORG,'explicit injected transaction test'),trusted_run=c.run_id,
            trusted_camera=c.camera.camera_id,resolutions=supplied_test_registrations(self.rc,self.raw),environment='test',
            connection_factory=lambda:nullcontext(object()),adapter=self.adapter)
        key=journal.key('db1.delivery.v1')
        for raw in raws if raws is not None else (self.raw,self.raw):self.client.xadd(key,{'json':raw})
        db=RedisDB1Consumer(self.client,journal,consumer,name='owned-test')
        db.client=FaultClient(self.client,mode)
        self.rows=self.client.xrange(key);self.db=db;self.key=key
        return TargetDeliveries(db,self.rows)

    def test_ack_ambiguities_finish_exact_targets_and_count_transactions_separately(self):
        audits=[]
        for mode in ('after_ack','before_ack','before_next_read'):
            with self.subTest(mode=mode):
                self.seed(mode=mode)
                report=deliver_targets(self.db,self.rows,deadline=time.monotonic()+2)
                self.assertEqual((report['targets'],report['completed'],report['waiting']),(2,2,0))
                self.assertEqual(report['new'],1)
                self.assertEqual(report['identical'],2 if mode=='before_ack' else 1)
                self.assertEqual(report['retry_attempts'],1 if mode=='before_ack' else 0)
                self.assertEqual(report['transactions_confirmed'],self.adapter.calls)
                self.assertEqual(report['acknowledgements_reconciled'],1 if mode=='after_ack' else 0)
                self.assertEqual(report['ack_callbacks_completed'],1 if mode=='after_ack' else 2)
                self.assertEqual(report['redis_errors'],1)
                self.assertEqual(len(self.adapter.facts),3)
                self.assertEqual(self.client.xpending(self.key,self.db.group)['pending'],0)
                self.assertEqual([f[b'json'] for _,f in self.client.xrange(self.key)],[self.raw,self.raw])
                audits.append(dict(mode=mode,injected_database=True,native_postgres=False,**report))
        print('DB_ACK_AMBIGUITY '+json.dumps(audits,sort_keys=True),flush=True)

    def test_read_failure_resets_progress_and_cannot_reuse_stale_commit(self):
        ledger=self.seed(mode='before_next_read');ledger.turn()
        self.assertEqual(ledger.status()['completed'],1)
        self.assertIsNotNone(self.db.last_delivery.commit)
        ledger.turn() # before the unread second physical input; first result is stale
        self.assertIsNone(self.db.last_delivery);self.assertIsNone(self.db.consumer.last_commit)
        self.assertEqual(ledger.status()['transactions_confirmed'],1)
        self.assertEqual(ledger.status()['waiting'],1)
        ledger.turn();self.assertTrue(ledger.done)
        self.assertEqual((ledger.status()['new'],ledger.status()['identical']),(1,1))

    def test_empty_pel_with_confirmed_database_history_does_not_complete_unread_input(self):
        ledger=self.seed(raws=(self.raw,));entry,fields=self.rows[0]
        result=self.db.consumer.process(self.raw,lambda:None) # known DB history, no Redis delivery/ACK
        self.db.last_delivery=DeliveryProgress(entry,self.raw,result)
        ledger.capture();ledger.reconcile()
        self.assertEqual(self.client.xpending(self.key,self.db.group)['pending'],0)
        self.assertEqual(ledger.observed[entry],'unread');self.assertFalse(ledger.done)
        self.assertEqual(ledger.status()['completed'],0)
        ledger.turn();self.assertTrue(ledger.done)
        self.assertEqual(ledger.status()['identical'],1)

    def test_genuinely_pending_committed_input_waits_and_times_out_without_success(self):
        ledger=self.seed(raws=(self.raw,),mode='before_ack');ledger.turn()
        self.assertFalse(ledger.done);self.assertEqual(ledger.status()['completed'],0)
        self.assertEqual(ledger.observed[self.rows[0][0]],'pending')
        self.assertEqual(self.client.xpending(self.key,self.db.group)['pending'],1)
        self.assertEqual(self.db.client.ack_executed,[])
        states=[]
        with patch.object(self.db.client,'xack',side_effect=TimeoutError('injected persistent pre-ACK failure')):
            with self.assertRaises(Blocked) as error:
                deliver_targets(self.db,self.rows,deadline=time.monotonic()+.15,on_status=states.append)
        self.assertEqual(error.exception.code,'DB_WAITING')
        self.assertEqual(states[-1]['completed'],0);self.assertEqual(states[-1]['waiting'],1)
        self.assertEqual(self.client.xpending(self.key,self.db.group)['pending'],1)

    def test_unknown_commit_and_rollback_reconcile_history_without_early_ack(self):
        for mode in ('lost','rollback'):
            with self.subTest(mode=mode):
                ledger=self.seed(raws=(self.raw,));self.adapter.failure=mode
                ledger.turn()
                self.assertFalse(ledger.done);self.assertFalse(ledger.commits)
                self.assertEqual(self.db.client.ack_executed,[])
                self.assertEqual(ledger.status()['unknown_commits' if mode=='lost' else 'not_committed'],1)
                ledger.turn();self.assertTrue(ledger.done)
                self.assertEqual(len(self.adapter.facts),3)
                self.assertEqual(ledger.status()['identical' if mode=='lost' else 'new'],1)

    def changed(self):
        from copy import deepcopy
        from vehicle.common.redis_scalar.plate_codec import join_bodies
        body=deepcopy(self.rc.v1.decode(self.raw).body);body['closed']['classes'][0]['confidence']=.81
        bundle=join_bodies(self.rc.v1,body['closed'],body['colour'],body['plate'])
        body.update(bundle=bundle.payload.value(),bundle_sha256=bundle.digest)
        return self.rc.v1.make('db1.delivery.v1',body)

    def test_duplicate_deliveries_then_changed_accepted_payload_conflict(self):
        ledger=self.seed(raws=(self.raw,self.raw,self.changed()))
        ledger.turn();ledger.turn()
        self.assertEqual(ledger.status()['completed'],2)
        accepted=dict(self.adapter.history)
        with self.assertRaises(DeliveryConflict):ledger.turn()
        self.assertEqual(ledger.status()['conflicts'],1)
        self.assertEqual(ledger.status()['transactions_confirmed'],2)
        self.assertEqual(ledger.status()['waiting'],1)
        self.assertEqual(len(self.db.client.ack_executed),2)
        self.assertEqual(self.client.xpending(self.key,self.db.group)['pending'],1)
        self.assertEqual(self.adapter.history,accepted)

    def test_target_byte_mismatch_blocks_before_database_and_retains_pending(self):
        self.seed(raws=(self.raw,));entry,fields=self.rows[0]
        wrong=TargetDeliveries(self.db,[(entry,{b'json':self.raw+b' '})])
        with self.assertRaises(Blocked) as error:wrong.turn()
        self.assertEqual(error.exception.code,'DELIVERY_BINDING')
        self.assertFalse(self.adapter.history);self.assertIsNone(self.db.last_delivery)
        self.assertEqual(self.db.client.ack_executed,[])
        self.assertEqual(self.client.xpending(self.key,self.db.group)['pending'],1)

    def test_lost_observation_reply_retains_exact_commit_without_an_extra_transaction(self):
        ledger=self.seed(raws=(self.raw,));actual=self.db.client.eval;lost=[]
        def observe(*args):
            result=actual(*args)
            if args[0]==OBSERVE_ACK and not lost:
                lost.append(True);raise TimeoutError('injected lost observation reply')
            return result
        with patch.object(self.db.client,'eval',side_effect=observe):
            ledger.turn();self.assertFalse(ledger.done)
            self.assertEqual(self.adapter.calls,1)
            ledger.turn();self.assertTrue(ledger.done)
        self.assertEqual(self.adapter.calls,1);self.assertEqual(ledger.status()['transactions_confirmed'],1)

    def test_missing_original_stream_entry_cannot_confirm_ambiguous_ack(self):
        ledger=self.seed(raws=(self.raw,),mode='after_ack')
        with self.assertRaises(TimeoutError):self.db.step(before_process=ledger.before_process)
        ledger.capture();self.client.xdel(self.key,self.rows[0][0])
        with self.assertRaises(Blocked):ledger.reconcile()
        self.assertFalse(ledger.done)


class CommitCallbackTests(unittest.TestCase):
    def test_correlated_callback_requires_confirmed_result_and_completed_connection_scope(self):
        from vehicle.common.tests.test_db1_consumer import ConsumerTests
        fixture=ConsumerTests('runTest');fixture.setUpClass();fixture.setUp()
        c=fixture.consumer;events=[]
        c.process(fixture.raw,lambda:events.append('ack'),on_commit=lambda result:events.append(result.disposition))
        self.assertEqual(events,['new','ack'])
        events.clear();fixture.adapter.failure='rollback'
        from vehicle.common.db1_adapter import DatabaseDeliveryError
        with self.assertRaises(DatabaseDeliveryError):
            c.process(fixture.raw,lambda:events.append('ack'),on_commit=lambda result:events.append(result.disposition))
        self.assertEqual(events,[])
        class ClosingFailure:
            def __enter__(self):return object()
            def __exit__(self,*_):raise RuntimeError('injected connection scope failure')
        c.connection_factory=ClosingFailure
        with self.assertRaises(RuntimeError):
            c.process(fixture.raw,lambda:events.append('ack'),on_commit=lambda result:events.append(result.disposition))
        self.assertEqual(events,[])
