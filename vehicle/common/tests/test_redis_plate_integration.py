"""Owned native Redis only; scalar evidence, separate Plate/v1 worker processes."""
from hashlib import sha256
import json
from pathlib import Path
import unittest
from uuid import uuid4

from contracts.db1.reference import canonical_json_bytes
from vehicle.common.tests import test_redis_scalar_integration as native
from vehicle.common.tests.test_redis_plate import altered,large_batch
from vehicle.common.redis_scalar.worker import Worker,load_plate_codec
from vehicle.common.redis_scalar.synthetic import ORG,IDENTITY_NAMESPACE
from vehicle.common.redis_scalar.plate_synthetic import fixture,SCENARIOS
from vehicle.common.redis_scalar.codec import vehicle_key
from vehicle.common.redis_scalar.plate_codec import STATE_LIMIT,INTENT_LIMIT,MAX_WIRE
from vehicle.common.redis_scalar.reducer import reduce_message
from vehicle.common.redis_scalar.journal import PREPARE,PUBLISH,FINISH
from vehicle.common.db1_rows.core import Blocked


class PlateRedisIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        native.RedisIntegrationTests.setUpClass.__func__(cls)
        cls.pc=load_plate_codec(ORG,IDENTITY_NAMESPACE)

    tearDownClass=classmethod(native.RedisIntegrationTests.tearDownClass.__func__)
    setUp=native.RedisIntegrationTests.setUp
    tearDown=native.RedisIntegrationTests.tearDown
    journal=native.RedisIntegrationTests.journal
    start=native.RedisIntegrationTests.start
    send=native.RedisIntegrationTests.send
    count=native.RedisIntegrationTests.count
    state=native.RedisIntegrationTests.state

    def stop_owned(self):
        self.tearDown();self.children=[];self.handles=[]

    def worker(self,role,**options):
        worker=Worker(self.client,codec=self.pc,namespace=self.ns,role=role,
            consumer='direct-'+uuid4().hex[:8],lease_ms=60000,**options)
        worker.setup();return worker

    def components(self,scenario='whole',revision=0,track=1):
        closed,colour,batch,expected=fixture(self.pc,scenario=scenario,revision=revision,track=track)
        _,co=reduce_message(self.codec,'colour',self.codec.decode(colour),None)
        ps,po=reduce_message(self.pc,'plate',self.pc.decode(batch),None) if revision==0 else (None,[])
        return closed,co[0][1],batch,po,expected

    def wait_delivery(self,count=1):native.eventually(lambda:self.count('db1.delivery.v1')==count)

    def exact(self,expected,index=-1):
        raw=self.client.xrange(self.journal().key('db1.delivery.v1'))[index][1][b'json']
        m=self.pc.decode(raw)
        self.assertEqual(canonical_json_bytes(m.body['bundle']),expected.payload.canonical)
        self.assertEqual(m.body['bundle_sha256'],expected.digest)

    def seed_join(self,closed,colour):
        w=self.worker('aggregator_plate')
        self.send('engine.lifecycle.plate',closed);w.step()
        self.send('results.colour',colour);w.step();w.journal.release()

    def test_separate_processes_all_outcomes_orders_and_exact_v1(self):
        orders=[('closed','colour','plate'),('closed','plate','colour'),('colour','closed','plate'),
                ('colour','plate','closed'),('plate','closed','colour'),('plate','colour','closed')]
        scenarios=[s for s in SCENARIOS if s!='waiting']
        for i,scenario in enumerate(scenarios):
            with self.subTest(scenario=scenario):
                self.ns='p'+uuid4().hex
                closed,colour_batch,batch,expected=fixture(self.pc,scenario=scenario)
                life=self.pc.decode(closed).life
                self.start('colour');self.start('plate');self.start('aggregator_plate')
                for slot in orders[i%len(orders)]:
                    stream,raw={'closed':('engine.lifecycle.plate',closed),
                                'colour':('engine.evidence.colour',colour_batch),
                                'plate':('engine.evidence.plate',batch)}[slot]
                    self.send(stream,raw)
                    native.eventually(lambda:(self.state('aggregator_plate',life) or {}).get('inputs',{}).get(slot))
                self.wait_delivery();self.exact(expected)
                self.assertEqual(self.count('results.plate'),1)
                self.stop_owned()

    def test_retained_wait_restart_both_workers_then_single_completion(self):
        closed,colour,initial,_,_=self.components('waiting')
        life=self.pc.decode(closed).life
        plate,_=self.start('plate');aggregator,_=self.start('aggregator_plate')
        self.send('engine.lifecycle.plate',closed);self.send('results.colour',colour);self.send('engine.evidence.plate',initial)
        native.eventually(lambda:(self.state('plate',life) or {}).get('status')=='waiting')
        native.eventually(lambda:len((self.state('aggregator_plate',life) or {}).get('inputs',{}))==2)
        self.assertEqual(self.count('db1.delivery.v1'),0);self.assertEqual(self.count('results.plate'),0)
        plate.kill();plate.wait(timeout=3);aggregator.kill();aggregator.wait(timeout=3)
        self.start('plate');self.start('aggregator_plate')
        ready,colour2,batch2,_,expected2=self.components(track=2)
        self.send('engine.lifecycle.plate',ready);self.send('results.colour',colour2);self.send('engine.evidence.plate',batch2)
        self.wait_delivery();self.exact(expected2)
        _,_,completion,expected=fixture(self.pc,scenario='waiting',revision=1)
        invalid=altered(completion,lambda v:v['body']['transcript']['candidates'][0].update(quality=.7))
        self.send('engine.evidence.plate',invalid)
        native.eventually(lambda:self.count('diagnostics.plate')==1)
        self.assertEqual(self.state('plate',life)['status'],'waiting')
        self.send('engine.evidence.plate',completion);self.wait_delivery(2);self.exact(expected)
        self.send('engine.evidence.plate',initial);self.send('engine.evidence.plate',completion)
        native.eventually(lambda:self.client.xpending(self.journal('plate').key('engine.evidence.plate'),'plate-v1')['pending']==0)
        self.assertEqual(self.count('db1.delivery.v1'),2);self.assertEqual(self.count('results.plate'),2)
        # A completion can overtake an initial revision still in the PEL. It
        # must remain unacknowledged, then complete when initial evidence arrives.
        closed3,colour3,initial3,_,_=self.components('waiting',track=3)
        _,_,completion3,expected3=fixture(self.pc,scenario='waiting',revision=1,track=3)
        life3=self.pc.decode(closed3).life
        self.send('engine.lifecycle.plate',closed3);self.send('results.colour',colour3)
        self.send('engine.evidence.plate',completion3)
        native.eventually(lambda:self.client.xpending(self.journal('plate').key('engine.evidence.plate'),'plate-v1')['pending']>0)
        self.assertIsNone(self.state('plate',life3))
        self.assertEqual(self.count('diagnostics.plate'),1)
        self.send('engine.evidence.plate',initial3)
        self.wait_delivery(3);self.exact(expected3)
        native.eventually(lambda:self.client.xpending(self.journal('plate').key('engine.evidence.plate'),'plate-v1')['pending']==0)

    def test_crash_reclaim_all_four_boundaries_for_plate_and_v1_join(self):
        for role in ('plate','aggregator_plate'):
            for phase in ('prepared','published','before_ack','acknowledged'):
                with self.subTest(role=role,phase=phase):
                    self.ns='c'+uuid4().hex
                    closed,colour,initial,_,_=self.components('waiting')
                    life=self.pc.decode(closed).life
                    self.seed_join(closed,colour)
                    waiting=self.worker('plate');self.send('engine.evidence.plate',initial)
                    waiting.step();waiting.journal.release()
                    _,_,completion,expected=fixture(self.pc,scenario='waiting',revision=1)
                    if role=='plate':
                        self.start('aggregator_plate');child,marker=self.start('plate',phase)
                        self.send('engine.evidence.plate',completion)
                    else:
                        self.start('plate');self.send('engine.evidence.plate',completion)
                        native.eventually(lambda:self.count('results.plate')==1)
                        child,marker=self.start('aggregator_plate',phase)
                    native.eventually(marker.exists)
                    child.kill();child.wait(timeout=3);self.start(role)
                    self.wait_delivery();self.exact(expected)
                    native.eventually(lambda:not self.client.hexists(self.journal(role).state,'pending'))
                    self.assertEqual(self.count('results.plate'),1)
                    self.assertEqual(self.count('db1.delivery.v1'),1)
                    self.stop_owned()

    def prepared_input(self,role):
        closed,colour,batch,plate,expected=self.components()
        if role=='aggregator_plate':
            self.seed_join(closed,colour)
            stream,raw='results.plate',plate[0][1]
        else:stream,raw='engine.evidence.plate',batch
        w=self.worker(role);self.send(stream,raw)
        return w,stream,expected

    def test_partial_lua_writes_and_lost_replies_both_roles(self):
        from redis.exceptions import ResponseError,TimeoutError
        for role in ('plate','aggregator_plate'):
            for target in ('partial',PREPARE,PUBLISH,FINISH):
                with self.subTest(role=role,target=str(target)[:12]):
                    self.ns='r'+uuid4().hex
                    w,stream,_=self.prepared_input(role)
                    original=w.journal.call;lost=False
                    def interrupted(script,keys,*args):
                        nonlocal lost
                        if target=='partial' and script==PUBLISH:
                            script=script.replace("return 'published'","return redis.error_reply('INJECTED_AFTER_WRITE')")
                        reply=original(script,keys,*args)
                        if script==target and not lost:
                            lost=True;raise TimeoutError('injected lost reply')
                        return reply
                    w.journal.call=interrupted
                    with self.assertRaises((ResponseError,TimeoutError)):w.step()
                    w.journal.call=original;w.journal.release()
                    replacement=self.worker(role);replacement.step()
                    output='results.plate' if role=='plate' else 'db1.delivery.v1'
                    self.assertEqual(self.count(output),1)
                    self.assertEqual(self.client.xpending(replacement.journal.key(stream),replacement.journal.group)['pending'],0)
                    replacement.journal.release()

    def test_all_three_streams_continuously_ready_pending_new_are_fair(self):
        w=self.worker('aggregator_plate')
        for track in range(1,10):
            closed,colour,_,plate,_=self.components(track=track)
            for stream,raw in [('engine.lifecycle.plate',closed),('results.colour',colour),('results.plate',plate[0][1])]:self.send(stream,raw)
        pending={}
        for source in w.sources:
            rows=self.client.xreadgroup(w.journal.group,'previous-reader',{w.journal.key(source):'>'},count=3)[0][1]
            pending[source]={r[0] for r in rows}
        served=[];original=w.process
        def record(source,entry,fields):served.append((source,entry));return original(source,entry,fields)
        w.process=record
        for _ in range(18):self.assertTrue(w.step())
        self.assertEqual([s for s,_ in served],w.sources*6)
        for source in w.sources:
            ids=[i for s,i in served if s==source]
            self.assertEqual(sum(i in pending[source] for i in ids),3)
        self.assertEqual(self.count('db1.delivery.v1'),6);w.journal.release()

    def test_byte_and_stream_capacity_preserve_input_and_no_partial_prepare(self):
        from redis.exceptions import ResponseError
        for role in ('plate','aggregator_plate'):
            for option in ('max_state_bytes','max_intent_bytes'):
                self.ns='b'+uuid4().hex
                w,stream,_=self.prepared_input(role)
                before=self.client.hgetall(w.journal.state)
                state_bytes=max((len(v) for k,v in before.items() if k.startswith(b'vehicle:')),default=0)
                setattr(w.journal,option,state_bytes+1 if option=='max_state_bytes' else 10)
                with self.assertRaises(Blocked):w.step()
                self.assertEqual(self.client.hgetall(w.journal.state),before)
                output='results.plate' if role=='plate' else 'db1.delivery.v1'
                self.assertEqual(self.count(output),0)
                self.assertEqual(self.client.xpending(w.journal.key(stream),w.journal.group)['pending'],1)
                setattr(w.journal,option,STATE_LIMIT if option=='max_state_bytes' else INTENT_LIMIT)
                w.step();self.assertEqual(self.count(output),1);w.journal.release()
        _,_,batch,_,_=self.components()
        self.ns='s'+uuid4().hex;w=self.worker('plate',max_stream=1)
        self.client.xadd(w.journal.key('results.plate'),{'json':b'{}'},id='0-1')
        self.send('engine.evidence.plate',batch)
        with self.assertRaisesRegex(ResponseError,'CAPACITY_EXCEEDED'):w.step()
        self.assertTrue(self.client.hexists(w.journal.state,'pending'))
        self.assertEqual(self.client.xpending(w.journal.key('engine.evidence.plate'),'plate-v1')['pending'],1)
        w.journal.max_stream=2;w.step();self.assertEqual(self.count('results.plate'),2);w.journal.release()

    def test_large_envelope_above_v0_and_wire_rejection_without_vehicle_acceptance(self):
        closed,colour,_,_,_=self.components()
        raw=large_batch(self.pc)
        self.assertGreater(len(raw),65536)
        w=self.worker('plate');self.send('engine.evidence.plate',raw);w.step();w.journal.release()
        terminal=self.client.xrange(self.journal().key('results.plate'))[0][1][b'json']
        self.assertGreater(len(terminal),65536)
        self.seed_join(closed,colour)
        w=self.worker('aggregator_plate');w.step();w.journal.release();self.wait_delivery()
        delivery=self.client.xrange(self.journal().key('db1.delivery.v1'))[0][1][b'json']
        self.assertGreater(len(delivery),65536);self.pc.decode(delivery)
        self.ns='o'+uuid4().hex;w=self.worker('plate')
        self.send('engine.evidence.plate',b' '*(MAX_WIRE+1));w.step()
        self.assertEqual(self.count('diagnostics.plate'),1)
        self.assertEqual(int(self.client.hget(w.journal.state,'vehicles') or 0),0)
        self.assertEqual(self.count('results.plate'),0);w.journal.release()

    def test_conflicts_tenant_and_invalid_closure_do_not_poison_valid_join(self):
        closed,colour,batch,plate,expected=self.components()
        invalid=altered(closed,lambda v:v['body']['classes'].append(dict(v['body']['classes'][0])))
        self.start('plate');self.start('aggregator_plate')
        self.send('engine.lifecycle.plate',invalid)
        native.eventually(lambda:self.count('diagnostics.aggregator_plate')==1)
        life=self.pc.decode(closed).life
        self.assertIsNone(self.state('aggregator_plate',life))
        self.send('engine.lifecycle.plate',closed);self.send('results.colour',colour);self.send('engine.evidence.plate',batch)
        self.wait_delivery();self.exact(expected)
        body=dict(self.pc.decode(batch).body);body['batch_id']=str(uuid4())
        changed_id=self.pc.make('plate.batch',body)
        changed_payload=altered(batch,lambda v:v['body']['transcript']['attempts'][0]['operations'][0].update(raw_text='124 تونس 4567'))
        wrong=altered(batch,lambda v:v.update(org_id=str(uuid4())))
        for raw in (changed_id,changed_payload,wrong,batch):self.send('engine.evidence.plate',raw)
        native.eventually(lambda:self.count('diagnostics.plate')==3)
        self.assertEqual(self.count('results.plate'),1);self.assertEqual(self.count('db1.delivery.v1'),1)


    def test_explicit_publisher_process_and_identical_stream_id_retry(self):
        import os,subprocess,sys
        self.start('colour');self.start('plate');self.start('aggregator_plate')
        _,_,_,expected=fixture(self.pc,scenario='region-right-first')
        for attempt in range(2):
            log=open(Path(self.directory.name)/f'{self.ns}-publisher-{attempt}.log','wb')
            self.handles.append(log)
            command=[sys.executable,'-m','vehicle.common.redis_scalar.plate_synthetic',
                '--redis',self.url,'--namespace',self.ns,'--org',str(ORG),
                '--identity-namespace',str(IDENTITY_NAMESPACE),'--consumer','engine-publisher',
                '--scenario','region-right-first','--stream-id','1-0']
            child=subprocess.Popen(command,cwd=native.ROOT,stdout=log,stderr=subprocess.STDOUT,
                env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
            self.children.append(child)
            self.assertEqual(child.wait(timeout=15),0)
            self.wait_delivery();self.exact(expected)
        for stream in ('engine.lifecycle.plate','engine.evidence.colour','engine.evidence.plate',
                       'results.plate','db1.delivery.v1'):
            self.assertEqual(self.count(stream),1)


if __name__=='__main__':unittest.main()
