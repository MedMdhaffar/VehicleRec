"""Owned native Redis, injected native executors, real metadata processes."""
from pathlib import Path
from uuid import uuid4
import time, unittest
from vehicle.common.tests import test_engine_plate_redis_integration as native
from vehicle.common.tests.test_redis_scalar_integration import eventually
from vehicle.common.tests import test_mp4_colour_plate as injected
from vehicle.common.redis_scalar.worker import load_request_codec
from vehicle.common.redis_scalar.synthetic import ORG, IDENTITY_NAMESPACE
from vehicle.common.redis_scalar.journal import Journal
from vehicle.common.plate_requests.host import V2Publisher
from vehicle.common.plate_requests.codec import STREAMS
from vehicle.common.plate_requests.worker import RequestWorker
from vehicle.common.real_video.plate_host import drive_video
from contracts.db1.reference import canonical_json_bytes


class RequestRedisTests(unittest.TestCase):
    setUpClass=classmethod(native.EnginePlateRedisIntegrationTests.setUpClass.__func__)
    @classmethod
    def tearDownClass(cls):
        directory=Path(cls.directory.name)
        native.EnginePlateRedisIntegrationTests.tearDownClass.__func__(cls)
        if directory.exists() or cls.server.poll() is None:
            raise AssertionError('owned Redis cleanup incomplete')
        print('V2_REDIS_CLEANUP {"server_exited":true,"temporary_directory_removed":true}',flush=True)
    setUp=native.EnginePlateRedisIntegrationTests.setUp
    tearDown=native.EnginePlateRedisIntegrationTests.tearDown
    journal=native.EnginePlateRedisIntegrationTests.journal
    start=native.EnginePlateRedisIntegrationTests.start
    count=native.EnginePlateRedisIntegrationTests.count

    def make(self,*,start_plate=True):
        self.rc=load_request_codec(ORG,IDENTITY_NAMESPACE)
        owner=injected.CombinedVideoTests('runTest');owner.setUpClass();owner.setUp();self.addCleanup(owner.doCleanups)
        engine=owner.engine()
        self.start('colour');self.start('aggregator_plate')
        if start_plate:self.start('plate_requests')
        journal=Journal(None,namespace=self.ns,trusted_org=ORG,role='plate_requests',consumer='request-host')
        return owner,engine,V2Publisher(self.client,journal,self.rc,engine)

    def settled(self,publisher):
        def ready():
            for closed in tuple(publisher.engine.pending.values()):
                if publisher.publish(closed):publisher.engine.acknowledge(closed.owner.service_track_id)
            return not publisher.engine.pending
        eventually(ready,seconds=20)

    def parity(self,p):
        eventually(lambda:self.count('db1.delivery.v1')==len(p.plans),seconds=20)
        for _,fields in self.client.xrange(p.journal.key('db1.delivery.v1')):
            m=self.rc.v1.decode(fields[b'json']);plan=p.plans[m.life.service_track_id]
            self.assertEqual(canonical_json_bytes(m.body['bundle']),plan.reference.payload.canonical)
        self.assertEqual(self.count('diagnostics.plate_requests'),0)
        self.assertEqual(self.count('diagnostics.aggregator_plate'),0)

    def test_mp4_single_decode_progressive_loop_parity_and_request_redelivery(self):
        owner,engine,p=self.make();result=drive_video(owner.source(3),engine,p)
        self.parity(p)
        self.assertEqual(result['decoded_frames'],3);self.assertEqual(owner.open_count,1)
        self.assertEqual((owner.vehicle.calls,owner.plate.calls),(3,3));self.assertEqual(engine.audit.max_active,1)
        session=p.plans[1].session
        before=dict(engine.audit.calls)
        for r,_ in session.ledger.values():
            self.client.xadd(p.journal.key(STREAMS['request']),{'json':self.rc.make('request',r)})
        for _ in range(len(session.ledger)*3):p.pump()
        self.assertEqual(engine.audit.calls,before)
        self.assertEqual(self.count(STREAMS['admission']),len(session.accepted))
        self.assertEqual(self.count(STREAMS['evidence']),len(session.accepted))
        self.assertFalse(engine.plate.buffers)
        print('V2_PARITY '+str([(sid,plan.reference.digest,len(plan.session.accepted)) for sid,plan in p.plans.items()]),flush=True)

    def test_lost_crop_admission_evidence_and_seal_replies_fifo_exact_bytes(self):
        for stream in [STREAMS[k] for k in ('crop','admission','evidence','sealed')]:
            owner,engine,p=self.make();closed=owner.one_frame(engine)
            actual=self.client
            class Interrupted:
                fail=True
                def eval(proxy,*args):
                    reply=actual.eval(*args)
                    if proxy.fail and args[2].endswith(stream):proxy.fail=False;raise ConnectionError('injected lost publication reply')
                    return reply
            p.writer.client=Interrupted()
            failures=[]
            def run():
                try:return p.publish(closed)
                except ConnectionError:failures.append(stream);return False
            eventually(run,seconds=20)
            self.assertEqual(failures,[stream]);self.parity(p)
            for key,(sid,suffix,raw) in p.writer.entries.items():
                rows=actual.xrange(p.journal.key(suffix),sid,sid)
                self.assertEqual(rows,[(sid.encode(),{b'json':raw})])
            before=dict(engine.audit.calls)
            self.assertTrue(p.publish(closed));self.assertEqual(engine.audit.calls,before)
            engine.acknowledge(closed.owner.service_track_id);engine.close()
            self.ns='v2'+uuid4().hex

    def test_metadata_worker_death_prepared_published_before_ack_and_repair(self):
        for phase in ('prepared','published','before_ack','acknowledged'):
            owner,engine,p=self.make(start_plate=False)
            child,marker=self.start('plate_requests',phase)
            closed=owner.one_frame(engine);p.prepare(closed)
            while p.writer.pending:p.writer.flush()
            eventually(marker.exists,seconds=20)
            before=self.client.xrange(p.journal.key(STREAMS['request']))
            child.kill();child.wait(timeout=3)
            self.start('plate_requests') # existing harness expires only confirmed dead owner's exact token
            self.settled(p);self.parity(p)
            after=self.client.xrange(p.journal.key(STREAMS['request']))
            if before:self.assertEqual(before[0],after[0])
            self.assertEqual(len(after),len(p.plans[1].session.accepted))
            self.assertIsNone(self.client.hget(p.journal.prefix+'plate_requests:journal','pending'))
            engine.close();self.ns='v2'+uuid4().hex

    def test_multiple_lifetimes_fair_progress_closure_order_and_fifo(self):
        from vehicle.common.tests.test_mp4_publication_order import two_lives
        from threading import Event
        owner,engine,p=self.make();closed=two_lives(owner,engine,order=(2,1))
        owner.runtimes['ocr'].wait=Event()
        # Prepare in creation order, offer in a different closure order. FIFO
        # publication has a single monotonic owner independent of either order.
        p.prepare(closed[1]);p.prepare(closed[2])
        def second_ready():
            for sid in (2,1):
                if p.publish(closed[sid]) and sid in engine.pending:engine.acknowledge(sid)
            return 2 in p.confirmed
        eventually(second_ready,seconds=20)
        self.assertNotIn(1,p.confirmed)
        eventually(lambda:self.count('db1.delivery.v1')==1,seconds=20)
        self.assertTrue(engine.plate.buffers)
        owner.runtimes['ocr'].wait.set();self.settled(p);self.parity(p)
        for stream in ('engine.lifecycle.plate','engine.evidence.colour',STREAMS['crop']):
            rows=self.client.xrange(p.journal.key(stream))
            self.assertEqual(len(rows),2)
            self.assertLess(int(rows[0][0].split(b'-')[0]),int(rows[1][0].split(b'-')[0]))
        self.assertEqual(len(p.plans),2);self.assertFalse(engine.plate.buffers);engine.close()

    def test_request_lost_reply_repairs_before_next_vehicle_and_changed_retry_blocks(self):
        from vehicle.common.tests.test_plate_requests import session_fixture
        from vehicle.common.plate_requests.codec import digest
        from vehicle.common.redis_scalar.codec import vehicle_key
        codec=load_request_codec(ORG,IDENTITY_NAMESPACE)
        session,crop,*_=session_fixture(codec)
        self.addCleanup(lambda:(session.adapter.stop(),session.adapter.cleanup(discard=True)))
        client=self.client
        class Interrupted:
            fail=True
            def __getattr__(proxy,k):return getattr(client,k)
            def eval(proxy,*args):
                reply=client.eval(*args)
                if proxy.fail and 'XADD' in args[0] and any(isinstance(a,str) and a.endswith(STREAMS['request']) for a in args[2:5]):
                    proxy.fail=False;raise ConnectionError('injected lost request publication reply')
                return reply
        w=RequestWorker(Interrupted(),codec=codec,namespace=self.ns,consumer='fault-worker');w.setup()
        self.client.xadd(w.journal.key(STREAMS['crop']),{'json':session.raw})
        with self.assertRaises(ConnectionError):w.step()
        original=self.client.xrange(w.journal.key(STREAMS['request']))
        self.assertEqual(len(original),1);self.assertIsNotNone(self.client.hget(w.journal.state,'pending'))
        self.assertTrue(w.step());self.assertIsNone(self.client.hget(w.journal.state,'pending'))
        self.assertEqual(original,self.client.xrange(w.journal.key(STREAMS['request'])))
        # A valid changed payload under the accepted crop identity must freeze
        # semantic progress, rather than being dropped as a harmless diagnostic.
        changed=dict(session.crop,operation_budget=31)
        self.client.xadd(w.journal.key(STREAMS['crop']),{'json':codec.make('crop',changed)})
        self.assertTrue(w.step())
        state=w.journal.read_state(vehicle_key(session.adapter.lifetime))
        self.assertEqual(state['status'],'blocked');self.assertEqual(self.count('diagnostics.plate_requests'),1)
        self.assertEqual(self.count('results.plate'),0);w.journal.release()

    def test_full_loop_db_consumer_injected_commit_then_death_and_redis_ack(self):
        from contextlib import nullcontext
        from vehicle.common.db1_consumer import NaturalConsumer,RedisDB1Consumer
        from vehicle.common.db1_adapter import TrustedTenant
        from vehicle.common.tests.test_db1_consumer import supplied_test_registrations,TransactionAdapter
        owner,engine,p=self.make();drive_video(owner.source(3),engine,p);self.parity(p)
        raw=self.client.xrange(p.journal.key('db1.delivery.v1'))[0][1][b'json']
        registrations=supplied_test_registrations(self.rc,raw)
        life=self.rc.v1.decode(raw).life;adapter=TransactionAdapter(self.rc.validation)
        consumer=NaturalConsumer(codec=self.rc.v1,tenant=TrustedTenant(ORG,'explicit injected database test host'),
            trusted_run=life.context.run_id,trusted_camera=life.context.camera.camera_id,resolutions=registrations,
            environment='test',connection_factory=lambda:nullcontext(object()),adapter=adapter)
        db=RedisDB1Consumer(self.client,p.journal,consumer,name='injected-committer')
        def death(phase):
            if phase=='committed':raise RuntimeError('injected death after committed receipt')
        consumer.hook=death
        before=dict(engine.audit.calls)
        with self.assertRaises(RuntimeError):db.step()
        self.assertEqual(self.client.xpending(p.journal.key('db1.delivery.v1'),db.group)['pending'],1)
        consumer.hook=lambda phase:None
        self.assertEqual(db.step().disposition,'identical')
        self.assertEqual(self.client.xpending(p.journal.key('db1.delivery.v1'),db.group)['pending'],0)
        self.assertEqual(len(adapter.history),1);self.assertEqual(engine.audit.calls,before)

    def test_scalar_evidence_capacity_failure_retains_intent_for_retry(self):
        from vehicle.common.db1_rows.core import Blocked
        owner,engine,p=self.make();closed=owner.one_frame(engine)
        add=p.writer.add;failures=[]
        def interrupted(identity,stream,raw):
            if identity.startswith('evidence-') and not failures:
                failures.append(raw);raise Blocked('CAPACITY_EXCEEDED','injected publication reservation pressure')
            return add(identity,stream,raw)
        p.writer.add=interrupted
        def ready():
            try:return p.publish(closed)
            except Blocked as error:
                if error.code!='CAPACITY_EXCEEDED':raise
                return False
        eventually(ready,seconds=20)
        self.assertEqual(len(failures),1);self.parity(p)
        evidence=self.client.xrange(p.journal.key(STREAMS['evidence']))
        self.assertIn(failures[0],[f[b'json'] for _,f in evidence])
        engine.acknowledge(closed.owner.service_track_id);engine.close()

    def test_canonical_regional_followups_both_orders_through_native_redis(self):
        for order in [('left_ocr','right_ocr'),('right_ocr','left_ocr')]:
            owner,engine,p=self.make(start_plate=False)
            values=iter(['123456',*(['123','456'] if order[0]=='left_ocr' else ['456','123'])])
            runtime=owner.runtimes['ocr']
            def predict(image):
                runtime.images.append(image.copy())
                return [dict(rec_text=next(values),rec_score=.96)]
            runtime.predict=predict
            worker=RequestWorker(self.client,codec=self.rc,namespace=self.ns,consumer='regional-policy',regional_order=order)
            worker.setup();closed=owner.one_frame(engine)
            def ready():
                worker.step()
                return p.publish(closed)
            eventually(ready,seconds=20);self.parity(p)
            requests=[r['operation'] for r,_ in p.plans[1].session.ledger.values()]
            self.assertEqual(requests,['whole_ocr','marker',*order])
            self.assertEqual(p.plans[1].reference.payload.value()['facts'][-1]['delivery']['outcome'],'read')
            self.assertEqual([engine.audit.calls[k] for k in ['whole_ocr','marker','left_ocr','right_ocr']],[1,1,1,1])
            worker.journal.release();engine.acknowledge(1);engine.close();self.ns='v2'+uuid4().hex

    def test_semantic_seal_conflict_sticky_blocks_unfinished_lifetime(self):
        from copy import deepcopy
        from hashlib import sha256
        from vehicle.common.tests.test_plate_requests import session_fixture,complete_loop
        from vehicle.common.redis_scalar.codec import vehicle_key
        codec=load_request_codec(ORG,IDENTITY_NAMESPACE)
        session,crop,*_=session_fixture(codec)
        self.addCleanup(lambda:(session.adapter.stop(),session.adapter.cleanup(discard=True)))
        state,_,terminal=complete_loop(codec,session)
        state=deepcopy(state);state['status']='sealing';state.pop('sealed');state.pop('terminal_sha256')
        worker=RequestWorker(self.client,codec=codec,namespace=self.ns,consumer='seal-conflict');worker.setup()
        journal=worker.journal;self.assertTrue(journal.acquire())
        entry=self.client.xadd(journal.key(STREAMS['crop']),{'json':session.raw})
        journal.handoff(source=STREAMS['crop'],entry_id=entry,marker='fixture-prepared-state',digest=sha256(session.raw).hexdigest(),
            vehicle=vehicle_key(session.adapter.lifetime),state=state)
        bad=deepcopy(codec.decode(session.sealed).body)
        bad['seal']['request_ids']=sorted(str(uuid4()) for _ in bad['seal']['request_ids'])
        self.client.xadd(journal.key(STREAMS['sealed']),{'json':codec.make('sealed',bad)})
        def rejected():
            worker.step()
            return self.count('diagnostics.plate_requests')==1
        eventually(rejected,seconds=20)
        self.assertEqual(journal.read_state(vehicle_key(session.adapter.lifetime))['status'],'blocked')
        self.client.xadd(journal.key(STREAMS['sealed']),{'json':session.sealed})
        for _ in range(4):worker.step()
        self.assertEqual(self.count('results.plate'),0)
        journal.release()
