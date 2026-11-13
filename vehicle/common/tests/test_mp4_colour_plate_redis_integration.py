"""Real isolated Redis plus combined Engine using injected detector/native APIs."""
import unittest
from vehicle.common.tests import test_engine_plate_redis_integration as native
from vehicle.common.tests import test_mp4_colour_plate as injected
from vehicle.common.real_video.plate_host import V1Publisher,drive_video
from vehicle.common.redis_scalar.journal import Journal
from vehicle.common.redis_scalar.synthetic import ORG


class CombinedRedisTests(unittest.TestCase):
    setUpClass=classmethod(native.EnginePlateRedisIntegrationTests.setUpClass.__func__)
    tearDownClass=classmethod(native.EnginePlateRedisIntegrationTests.tearDownClass.__func__)
    setUp=native.EnginePlateRedisIntegrationTests.setUp
    tearDown=native.EnginePlateRedisIntegrationTests.tearDown
    journal=native.EnginePlateRedisIntegrationTests.journal
    start=native.EnginePlateRedisIntegrationTests.start
    count=native.EnginePlateRedisIntegrationTests.count
    exact=native.EnginePlateRedisIntegrationTests.exact
    wait_delivery=native.EnginePlateRedisIntegrationTests.wait_delivery

    def make(self):
        owner=injected.CombinedVideoTests('runTest');owner.setUp();self.addCleanup(owner.doCleanups)
        engine=owner.engine()
        for role in ('colour','plate','aggregator_plate'):self.start(role)
        journal=Journal(None,namespace=self.ns,trusted_org=ORG,role='plate',consumer='combined-engine')
        return owner,engine,journal

    def test_combined_same_evidence_byte_parity_and_no_repeat(self):
        owner,engine,journal=self.make();p=V1Publisher(self.client,journal,self.pc,engine)
        saved=[];publish=p.publish
        def capture(closed):saved.append(closed);return publish(closed)
        p.publish=capture
        drive_video(owner.source(3),engine,p)
        self.wait_delivery();self.exact(p.plans[1].reference)
        before=dict(engine.audit.calls);self.assertTrue(publish(saved[0]))
        self.assertEqual(engine.audit.calls,before);self.assertEqual(self.count('engine.evidence.plate'),1)
        self.assertEqual(self.count('db1.delivery.v1'),1)
        self.assertEqual(owner.open_count,1);self.assertEqual((owner.vehicle.calls,owner.plate.calls),(3,3))
        self.assertEqual(engine.audit.max_active,1)

    def test_lost_reply_retains_exact_completed_combined_plan(self):
        owner,engine,journal=self.make();closed=owner.one_frame(engine)
        from redis.exceptions import TimeoutError
        class Client:
            fail=True
            def eval(proxy,*args):
                reply=self.client.eval(*args)
                if proxy.fail and args[2].endswith('engine.evidence.colour'):
                    proxy.fail=False;raise TimeoutError('injected lost Colour publication reply')
                return reply
        p=V1Publisher(Client(),journal,self.pc,engine)
        with self.assertRaises(TimeoutError):p.publish(closed)
        self.assertEqual(self.count('db1.delivery.v1'),0)
        original=p.plans[1];calls=dict(engine.audit.calls)
        self.assertTrue(p.publish(closed));self.assertIs(p.plans[1],original)
        self.wait_delivery();self.exact(original.reference)
        self.assertEqual(self.count('engine.lifecycle.plate'),1);self.assertEqual(self.count('engine.evidence.colour'),1)
        self.assertEqual(self.count('db1.delivery.v1'),1);self.assertEqual(engine.audit.calls,calls)
        engine.acknowledge(1);engine.close();self.assertFalse(engine.pending)


from vehicle.common.tests.test_mp4_publication_order import OrderingCases,STREAMS,two_lives


class PublicationOrderRedisTests(OrderingCases,unittest.TestCase):
    """Run the same ordering/fault matrix against native Redis and real workers."""
    setUpClass=CombinedRedisTests.__dict__['setUpClass']
    tearDownClass=CombinedRedisTests.__dict__['tearDownClass']
    setUp=CombinedRedisTests.setUp
    tearDown=CombinedRedisTests.tearDown
    journal=CombinedRedisTests.journal
    start=CombinedRedisTests.start
    count=CombinedRedisTests.count
    exact=CombinedRedisTests.exact
    wait_delivery=CombinedRedisTests.wait_delivery

    def make_publisher(self):
        from uuid import uuid4
        self.ns='order'+uuid4().hex
        owner=injected.CombinedVideoTests('runTest');owner.setUpClass();owner.setUp();self.addCleanup(owner.doCleanups)
        e=owner.engine()
        for role in ('colour','plate','aggregator_plate'):self.start(role)
        journal=Journal(None,namespace=self.ns,trusted_org=ORG,role='plate',consumer='ordered-engine')
        return owner,e,V1Publisher(self.client,journal,self.pc,e)

    def wire_count(self,p):return p.wire_calls

    def interrupt(self,p,stream,mode):
        actual=self.client
        class Interrupted:
            fail=True
            attempts=[]
            def eval(proxy,*args):
                if args[2].endswith(stream):proxy.attempts.append((args[3],args[4]))
                target=proxy.fail and args[2].endswith(stream)
                if target and mode=='before':
                    proxy.fail=False;raise ConnectionError('before append')
                reply=actual.eval(*args)
                if target:
                    proxy.fail=False;raise ConnectionError('accepted append; reply lost')
                return reply
        p.client=Interrupted()

    def check_entries(self,p):
        import json
        for stream,field in zip(STREAMS,('closed','colour','plate')):
            rows=self.client.xrange(p.journal.key(stream))
            self.assertEqual([row[0] for row in rows],[b'1-0',b'2-0'])
            for plan in p.plans.values():
                self.assertEqual(dict(rows)[plan.stream_id.encode()][b'json'],getattr(plan,field))
        retry_ids=[]
        if hasattr(p.client,'attempts'):
            self.assertEqual(p.client.attempts[0],p.client.attempts[1])
            retry_ids=[entry for entry,raw in p.client.attempts]
        self.wait_delivery(2)
        rows=self.client.xrange(p.journal.key('db1.delivery.v1'))
        from contracts.db1.reference import canonical_json_bytes
        bundles={self.pc.decode(row[1][b'json']).body['bundle_sha256']:
                 canonical_json_bytes(self.pc.decode(row[1][b'json']).body['bundle']) for row in rows}
        for plan in p.plans.values():self.assertEqual(bundles[plan.reference.digest],plan.reference.payload.canonical)
        print('ORDER_RECEIPT '+json.dumps(dict(test=self.id(),namespace=self.ns,
            inputs={s:self.count(s) for s in STREAMS},deliveries=len(rows),retry_ids=retry_ids,
            plans=[dict(vehicle=x.sid,transport_id=x.stream_id,bundle_sha256=x.reference.digest,canonical_bytes_equal=True)
                   for x in p.plans.values()])),flush=True)
        # Each matrix case owns its workers; reap before admitting the next
        # namespace instead of retaining idle worker pools through the method.
        self.tearDown();self.children=[];self.handles=[]

    def test_both_closure_orders_keep_identical_natural_bytes(self):
        snapshots=[]
        for order in ((1,2),(2,1)):
            owner,e,p=self.make_publisher();lives=two_lives(owner,e,order)
            for sid in order:self.assertTrue(p.publish(lives[sid]))
            self.check_entries(p)
            snapshots.append({sid:(x.closed,x.colour,x.plate,x.reference.payload.canonical) for sid,x in p.plans.items()})
        self.assertEqual(snapshots[0],snapshots[1])
