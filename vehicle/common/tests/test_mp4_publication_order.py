"""Observable publication ordering/retry tests with injected evidence only."""
from dataclasses import replace
from unittest.mock import patch
import unittest

from contracts.db1.reference import canonical_json_bytes
from vehicle.common.db1_rows.core import Blocked
from vehicle.common.real_video.plate_host import V1Publisher,drive_video
from vehicle.common.redis_scalar.reducer import reduce_message
from vehicle.common.tests import test_mp4_colour_plate as injected

STREAMS=('engine.lifecycle.plate','engine.evidence.colour','engine.evidence.plate')


class OrderedRedis:
    """PRODUCE semantics, including Redis's increasing explicit ID requirement."""
    def __init__(self):self.rows={};self.calls=[];self.interrupt=None
    def eval(self,*args):
        _,_,key,entry,raw,_=args;self.calls.append((key,entry,raw))
        failure=self.interrupt
        if failure and key.endswith(failure[0]) and failure[1]=='before':
            self.interrupt=None;raise ConnectionError('before append')
        rows=self.rows.setdefault(key,{})
        if entry in rows:
            if rows[entry]!=raw:raise RuntimeError('PAYLOAD_CONFLICT')
        else:
            if rows and int(entry.split('-')[0])<=max(int(v.split('-')[0]) for v in rows):
                raise RuntimeError('Redis ID is equal or smaller than the target stream top item')
            rows[entry]=raw
        if failure and key.endswith(failure[0]) and failure[1]=='after':
            self.interrupt=None;raise ConnectionError('accepted append; reply lost')
        return entry.encode()


def two_lives(owner,engine,order=(2,1)):
    owner.vehicle.rows=[[0,0,100,90,7,.9,2,0],[110,0,155,90,8,.8,2,0]]
    source=owner.source(1)
    with source:engine.process(source.read())
    # Exercise actual lifecycle closure and Engine retention, in selected order.
    for sid in order:
        engine.colour.pipeline._finalize([sid+6]);engine.colour._retain_closures()
    return dict(engine.pending)


class OrderingCases:
    def parity(self,p):
        for plan in p.plans.values():
            _,co=reduce_message(p.codec.v0,'colour',p.codec.v0.decode(plan.colour),None)
            _,pl=reduce_message(p.codec,'plate',p.codec.decode(plan.plate),None)
            state=None
            for raw in (plan.closed,co[0][1],pl[0][1]):
                state,out=reduce_message(p.codec,'aggregator_plate',p.codec.decode(raw),state)
            self.assertEqual(canonical_json_bytes(p.codec.decode(out[0][1]).body['bundle']),plan.reference.payload.canonical)
            self.assertEqual(p.codec.decode(plan.closed).life.service_track_id,plan.sid)

    def test_close_two_before_one(self):
        owner,e,p=self.make_publisher();lives=two_lives(owner,e)
        for sid in (2,1):self.assertTrue(p.publish(lives[sid]))
        self.assertEqual([p.plans[s].stream_id for s in (2,1)],['1-0','2-0'])
        self.check_entries(p);self.parity(p)

    def test_preparation_fifo_blocks_reverse_publication(self):
        owner,e,p=self.make_publisher();lives=two_lives(owner,e)
        first=p.prepare(lives[1]);second=p.prepare(lives[2]);before=self.wire_count(p)
        self.assertFalse(p.publish(lives[2]));self.assertEqual(self.wire_count(p),before)
        self.assertTrue(p.publish(lives[1]));self.assertTrue(p.publish(lives[2]))
        self.assertIs(p.prepare(lives[1]),first);self.assertIs(p.prepare(lives[2]),second)
        self.check_entries(p);self.parity(p)

    def test_interrupt_each_append_before_and_after(self):
        for stream in STREAMS:
            for mode in ('before','after'):
                with self.subTest(stream=stream,mode=mode):
                    owner,e,p=self.make_publisher();lives=two_lives(owner,e)
                    self.interrupt(p,stream,mode)
                    with self.assertRaises(ConnectionError):p.publish(lives[2])
                    original=p.plans[2];crop=e.plate.buffers[:]
                    self.assertFalse(p.publish(lives[1]))
                    self.assertTrue(all(not c.released for c in crop));calls=dict(e.audit.calls)
                    self.assertTrue(p.publish(lives[2]));self.assertTrue(p.publish(lives[1]))
                    self.assertIs(p.plans[2],original);self.assertEqual(e.audit.calls,calls)
                    self.assertTrue(all(c.released for c in crop))
                    self.check_entries(p);self.parity(p)


class PublicationOrderTests(OrderingCases,unittest.TestCase):
    def make_publisher(self):
        owner=injected.CombinedVideoTests('runTest');owner.setUpClass();owner.setUp();self.addCleanup(owner.doCleanups)
        e=owner.engine();p=owner.publisher(e,OrderedRedis());return owner,e,p
    def wire_count(self,p):return len(p.client.calls)
    def interrupt(self,p,stream,mode):p.client.interrupt=(stream,mode)
    def check_entries(self,p):
        for stream in STREAMS:
            rows=p.client.rows[p.journal.key(stream)]
            self.assertEqual(list(rows),['1-0','2-0'])
            for plan in p.plans.values():
                self.assertEqual(rows[plan.stream_id],getattr(plan,{'engine.lifecycle.plate':'closed','engine.evidence.colour':'colour','engine.evidence.plate':'plate'}[stream]))

    def test_changed_retry_has_no_wire_mutation(self):
        owner,e,p=self.make_publisher();closed=owner.one_frame(e);self.interrupt(p,STREAMS[1],'after')
        with self.assertRaises(ConnectionError):p.publish(closed)
        count=self.wire_count(p);calls=dict(e.audit.calls)
        changed=replace(closed,measurements=(replace(closed.measurements[0],confidence=.2),))
        with self.assertRaises(Blocked) as error:p.publish(changed)
        self.assertEqual(error.exception.code,'PAYLOAD_CONFLICT');self.assertEqual(self.wire_count(p),count)
        self.assertEqual(e.audit.calls,calls);self.assertTrue(p.publish(closed))

    def test_cleanup_failure_does_not_republish_or_block_transport(self):
        owner,e,p=self.make_publisher();lives=two_lives(owner,e);p.prepare(lives[1])
        adapter=e.plate.lives[1].adapter
        with patch.object(adapter,'cleanup',side_effect=RuntimeError('release failed')):
            with self.assertRaises(RuntimeError):p.publish(lives[1])
            self.assertTrue(e.plate.buffers)
            self.assertTrue(p.publish(lives[2])) # all earlier appends are confirmed
        count=self.wire_count(p);calls=dict(e.audit.calls)
        self.assertTrue(p.publish(lives[1]));self.assertEqual(self.wire_count(p),count)
        self.assertEqual(e.audit.calls,calls);self.assertFalse(e.plate.buffers)
        self.assertTrue(p.publish(lives[2]));self.check_entries(p)

    def test_host_drains_prepared_fifo_in_reverse_pending_order(self):
        owner,e,p=self.make_publisher();lives=two_lives(owner,e)
        p.prepare(lives[1]);p.prepare(lives[2])
        with patch('vehicle.common.real_video.plate_host.time.sleep') as sleep:
            drive_video(owner.source(0),e,p)
        sleep.assert_called_once();self.assertFalse(e.pending);self.check_entries(p)

    def test_retry_does_not_repeat_scalar_semantic_evaluation(self):
        owner,e,p=self.make_publisher();closed=owner.one_frame(e);p.prepare(closed)
        self.interrupt(p,STREAMS[2],'after')
        with patch('vehicle.common.engine_plate.PlateMetadata.evaluate',side_effect=AssertionError('reinterpreted')):
            with self.assertRaises(ConnectionError):p.publish(closed)
            self.assertTrue(p.publish(closed));self.assertTrue(p.publish(closed))

    def test_changed_plate_evidence_retry_has_no_wire_mutation(self):
        owner,e,p=self.make_publisher();closed=owner.one_frame(e);p.prepare(closed)
        adapter=e.plate.lives[1].adapter;transcript=p.plans[1].transcript
        changed=replace(transcript,candidates=(replace(transcript.candidates[0],quality=.1),))
        before=self.wire_count(p)
        with patch.object(adapter,'snapshot',return_value=changed):
            with self.assertRaises(Blocked) as error:p.publish(closed)
        self.assertEqual(error.exception.code,'PAYLOAD_CONFLICT');self.assertEqual(self.wire_count(p),before)
        self.assertTrue(p.publish(closed))

    def test_scalar_admission_rejection_preserves_original_and_retry(self):
        owner,e,p=self.make_publisher();lives=two_lives(owner,e);first=p.prepare(lives[2])
        limits=e.limits;e.limits=replace(limits,max_scalar_bytes=p.retained_bytes)
        with self.assertRaises(Blocked) as error:p.prepare(lives[1])
        self.assertEqual(error.exception.code,'CAPACITY_EXCEEDED');self.assertIs(p.plans[2],first)
        self.assertEqual(set(e.pending),{1,2});self.assertTrue(e.plate.buffers)
        e.limits=limits;self.assertTrue(p.publish(lives[2]));self.assertTrue(p.publish(lives[1]));self.check_entries(p)

    def test_lifetime_bound_before_admission(self):
        owner,e,p=self.make_publisher();lives=two_lives(owner,e);p.prepare(lives[2])
        e.limits=replace(e.limits,max_lifetimes=1)
        with self.assertRaises(Blocked) as error:p.prepare(lives[1])
        self.assertEqual(error.exception.code,'CAPACITY_EXCEEDED');self.assertEqual(set(e.pending),{1,2})
        self.assertTrue(p.publish(lives[2]))

    def test_host_wait_is_bounded_without_hot_loop(self):
        owner,e,p=self.make_publisher();e.limits=replace(e.limits,max_frames=1);clock=iter((0,0,0,0,301))
        with patch.object(p,'publish',side_effect=[False,False,AssertionError('host hot loop')]) as publish,patch('vehicle.common.real_video.plate_host.time.sleep') as sleep:
            with self.assertRaises(Blocked) as error:drive_video(owner.source(1),e,p,monotonic=lambda:next(clock))
        self.assertEqual(error.exception.code,'PUBLICATION_WAITING');self.assertLessEqual(publish.call_count,2)
        sleep.assert_called_once();self.assertTrue(e.pending);self.assertFalse(p.confirmed)
