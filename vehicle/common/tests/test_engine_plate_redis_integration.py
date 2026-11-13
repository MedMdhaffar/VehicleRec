"""Real owned Redis; Engine recognition/execution remains explicitly injected."""
from pathlib import Path
import unittest
from uuid import uuid4

from vehicle.common.tests import test_redis_plate_integration as native_plate
from vehicle.common.tests.test_redis_scalar_integration import eventually
from vehicle.common.tests.test_engine_plate import ManualScheduler,reference
from vehicle.common.engine_plate_injected import fixture
from vehicle.common.redis_scalar.journal import Journal
from vehicle.common.redis_scalar.reducer import reduce_message
from vehicle.common.redis_scalar.synthetic import ORG
from vehicle.common.db1_rows.core import Blocked


class EnginePlateRedisIntegrationTests(unittest.TestCase):
    setUpClass=classmethod(native_plate.PlateRedisIntegrationTests.setUpClass.__func__)
    tearDownClass=classmethod(native_plate.PlateRedisIntegrationTests.tearDownClass.__func__)
    setUp=native_plate.PlateRedisIntegrationTests.setUp
    tearDown=native_plate.PlateRedisIntegrationTests.tearDown
    journal=native_plate.PlateRedisIntegrationTests.journal
    start=native_plate.PlateRedisIntegrationTests.start
    send=native_plate.PlateRedisIntegrationTests.send
    count=native_plate.PlateRedisIntegrationTests.count
    state=native_plate.PlateRedisIntegrationTests.state
    wait_delivery=native_plate.PlateRedisIntegrationTests.wait_delivery
    exact=native_plate.PlateRedisIntegrationTests.exact
    seed_join=native_plate.PlateRedisIntegrationTests.seed_join
    worker=native_plate.PlateRedisIntegrationTests.worker
    stop_owned=native_plate.PlateRedisIntegrationTests.stop_owned

    def adapter(self,scenario='whole',scheduler=None):
        values=fixture(self.pc,scenario=scenario,scheduler=scheduler)
        a=values[0]
        def dispose():
            a.stop()
            if isinstance(a._pool,ManualScheduler):
                while a._pool.jobs:a._pool.run()
            if a._active:a._active.context.retired() # injected fakes have no remaining native use at teardown
            a.poll();a.cleanup(discard=True)
        self.addCleanup(dispose)
        return values

    def complete(self,a):
        a.mark_detection_complete();a.seal_admission();self.assertTrue(a.drain())

    def publisher(self):
        return Journal(None,namespace=self.ns,trusted_org=ORG,role='plate',consumer='engine-host')

    def services(self,closed,colour):
        self.start('colour');self.start('plate');self.start('aggregator_plate')
        self.send('engine.lifecycle.plate',closed);self.send('engine.evidence.colour',colour)

    def acked(self,stream,group,entry):
        key=self.journal().key(stream)
        def ready():
            groups=self.client.xinfo_groups(key)
            for row in groups:
                if row['name']==group.encode():
                    cursor=row['last-delivered-id']
                    return tuple(map(int,cursor.split(b'-')))>=tuple(map(int,entry.split(b'-'))) and self.client.xpending(key,group)['pending']==0
            return False
        eventually(ready)

    def test_adapter_whole_and_both_regions_exact_parity_ack_and_no_reexecution(self):
        for scenario in ('whole','region-left-first','region-right-first'):
            with self.subTest(scenario=scenario):
                self.ns='ea'+uuid4().hex
                a,crop,plan,calls,closed,colour,batch=self.adapter(scenario)
                self.complete(a);expected=reference(self.pc,a,closed,colour)
                self.services(closed,colour)
                publication=a.publish(self.client,self.publisher(),self.pc,batch_id=batch,stream_id='1-0')
                self.assertEqual(a.cleanup(),());self.assertEqual(crop.release_calls,1)
                self.wait_delivery();self.exact(expected)
                self.acked('engine.evidence.plate','plate-v1',b'1-0')
                duplicate=self.send('engine.evidence.plate',publication.payload)
                self.acked('engine.evidence.plate','plate-v1',duplicate)
                self.assertEqual(self.count('results.plate'),1);self.assertEqual(self.count('db1.delivery.v1'),1)
                a.publish(self.client,self.publisher(),self.pc,batch_id=batch,stream_id='1-0');a.drain()
                self.assertTrue(all(call.calls==1 for call in calls.values()))
                self.assertEqual(a.snapshot().attempts[0].attempt_id,plan.attempt_id)
                self.stop_owned()

    def test_publication_failure_lost_reply_and_release_retry_preserve_original_bytes(self):
        from redis.exceptions import ConnectionError,TimeoutError
        for mode in ('before','after'):
            with self.subTest(mode=mode):
                self.ns='ef'+uuid4().hex
                a,crop,_,calls,closed,colour,batch=self.adapter('region-right-first')
                self.complete(a);expected=reference(self.pc,a,closed,colour);self.services(closed,colour)
                actual=self.client
                class Interrupted:
                    def __init__(self):self.calls=[]
                    def eval(self,*args):
                        self.calls.append(args)
                        if len(self.calls)==1 and mode=='before':raise ConnectionError('injected before send')
                        reply=actual.eval(*args)
                        if len(self.calls)==1:raise TimeoutError('injected lost publish reply')
                        return reply
                client=Interrupted()
                with self.assertRaises((ConnectionError,TimeoutError)):
                    a.publish(client,self.publisher(),self.pc,batch_id=batch,stream_id='1-0')
                self.assertFalse(a.confirmed);self.assertFalse(crop.released)
                with self.assertRaises(Blocked):a.cleanup()
                p=a.publish(client,self.publisher(),self.pc,batch_id=batch,stream_id='1-0')
                self.assertEqual(client.calls[0],client.calls[1])
                self.assertEqual(self.client.xrange(self.publisher().key('engine.evidence.plate'))[0][1][b'json'],p.payload)
                crop.fail_release=1
                with self.assertRaises(RuntimeError):a.cleanup()
                self.assertEqual(a.cleanup(),());self.assertEqual(crop.release_calls,2)
                self.wait_delivery();self.exact(expected)
                self.assertEqual(self.count('engine.evidence.plate'),1)
                self.assertTrue(all(call.calls==1 for call in calls.values()))
                self.stop_owned()

    def test_closed_pending_stop_uncertainty_and_late_retirement(self):
        pool=ManualScheduler();a,crop,_,calls,closed,colour,batch=self.adapter(scheduler=pool)
        a.mark_detection_complete();a.seal_admission();a.close_lifetime(a.lifetime);a.step()
        self.services(closed,colour)
        with self.assertRaises(Blocked):a.prepare_publication(self.pc,batch_id=batch,stream_id='1-0')
        self.assertEqual(self.count('engine.evidence.plate'),0);self.assertFalse(crop.released)
        a.stop();a.settle_uncertain()
        expected=reference(self.pc,a,closed,colour)
        p=a.publish(self.client,self.publisher(),self.pc,batch_id=batch,stream_id='1-0')
        self.assertTrue(a.cleanup());self.assertFalse(crop.released)
        pool.run();a.poll();self.assertEqual(a.cleanup(),())
        self.wait_delivery();self.exact(expected)
        self.assertEqual(a.prepare_publication(self.pc,batch_id=batch,stream_id='1-0'),p)
        self.assertEqual(calls['whole'].calls,1);self.assertEqual(calls['marker'].calls,0)
        self.assertEqual(a.late_records()[0].model,calls['whole'].model)
        payload=expected.payload.value()
        self.assertIn(dict(domain='plate',reason='unsettled_operation'),payload['omissions'])

    def test_known_invocation_decode_failure_and_dependency_not_started(self):
        a,crop,_,calls,closed,colour,batch=self.adapter('region-left-first')
        calls['whole'].output={'invalid_native_output':'executed but not decodable'}
        self.complete(a);expected=reference(self.pc,a,closed,colour);self.services(closed,colour)
        a.publish(self.client,self.publisher(),self.pc,batch_id=batch,stream_id='1-0');a.cleanup()
        self.wait_delivery();self.exact(expected)
        self.assertEqual([calls[k].calls for k in ('whole','marker','left','right')],[1,1,0,0])
        plate=expected.payload.value()['facts'][-1]['delivery']
        self.assertEqual(plate['outcome'],'unreadable')
        self.assertEqual(plate['selected_attempt']['model'],calls['whole'].model.policy())
        self.assertEqual(crop.release_calls,1)

    def test_adapter_evidence_survives_worker_crash_at_all_handoff_boundaries(self):
        for role in ('plate','aggregator_plate'):
            for phase in ('prepared','published','before_ack','acknowledged'):
                with self.subTest(role=role,phase=phase):
                    self.ns='ec'+uuid4().hex
                    a,crop,_,calls,closed,colour,batch=self.adapter('region-right-first')
                    self.complete(a);expected=reference(self.pc,a,closed,colour)
                    _,out=reduce_message(self.codec,'colour',self.codec.decode(colour),None)
                    self.seed_join(closed,out[0][1])
                    if role=='plate':
                        self.start('aggregator_plate');child,marker=self.start('plate',phase)
                    else:
                        child,marker=self.start('aggregator_plate',phase);self.start('plate')
                    a.publish(self.client,self.publisher(),self.pc,batch_id=batch,stream_id='1-0');a.cleanup()
                    eventually(marker.exists);child.kill();child.wait(timeout=3);self.start(role)
                    self.wait_delivery();self.exact(expected)
                    eventually(lambda:not self.client.hexists(self.journal(role).state,'pending'))
                    self.acked('engine.evidence.plate','plate-v1',b'1-0')
                    self.assertEqual(self.count('db1.delivery.v1'),1)
                    self.assertTrue(all(call.calls==1 for call in calls.values()))
                    self.assertEqual(crop.release_calls,1)
                    self.stop_owned()


if __name__=='__main__':unittest.main()
