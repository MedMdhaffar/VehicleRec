"""Pure codec/reducer tests. No Redis connection, inference or provider execution."""
import copy
from dataclasses import replace
from hashlib import sha256
from decimal import Decimal
import json
import subprocess
import sys
import unittest
from unittest.mock import Mock
from uuid import uuid4

from contracts.db1.reference import canonical_json_bytes
from vehicle.common.db1_rows.core import Blocked
from vehicle.common.redis_scalar.codec import bounded_json, MAX_BYTES, unpack_life
from vehicle.common.redis_scalar.worker import Worker,load_codec
from vehicle.common.redis_scalar.synthetic import ORG,IDENTITY_NAMESPACE,fixture
from vehicle.common.redis_scalar.reducer import reduce_message
from vehicle.common.tests.test_colour_aggregator import fixture as memory_fixture
from vehicle.common.aggregator import Aggregator,Branch,validate_class_samples
from vehicle.common.colour_metadata import ColourMetadata


class CodecReducerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.codec=load_codec(ORG,IDENTITY_NAMESPACE)

    def setUp(self):
        self.closed,self.batch=fixture(self.codec)
        _,out=reduce_message(self.codec,'colour',self.codec.decode(self.batch),None)
        self.terminal=out[0][1]

    def test_exact_in_memory_bundle_both_arrival_orders(self):
        owner,measurements,classes=memory_fixture()
        expected=Aggregator(identity_policy=self.codec.identity_policy,validation=self.codec.validation).join(
            owner,classes,(Branch('colour','enabled',ColourMetadata().finalize(owner.lifetime,measurements)),
                          Branch('plate','disabled'),Branch('manufacturer','unsupported'))).delivery
        for order in ((self.closed,self.terminal),(self.terminal,self.closed)):
            with self.subTest(order=order[0][:30]):
                state=None
                for raw in order:
                    state,out=reduce_message(self.codec,'aggregator',self.codec.decode(raw),state)
                message=self.codec.decode(out[0][1])
                self.assertEqual(canonical_json_bytes(message.body['bundle']),expected.payload.canonical)
                self.assertEqual(message.body['bundle_sha256'],expected.digest)
                self.assertEqual(self.codec.decode(message.canonical).canonical,message.canonical)

    def test_equivalent_numbers_and_timestamps_have_same_wire_canonical_bytes(self):
        value=json.loads(self.batch)
        value['body']['life']['first']['ts']='2026-10-08T23:59:59+01:00'
        value['body']['measurements'][0]['confidence']=.8000
        self.assertEqual(self.codec.decode(json.dumps(value).encode()).canonical,self.batch)

    def test_exact_bigint_identity_and_no_silent_scalar_precision_loss(self):
        closed,_=fixture(self.codec,track=2**53+1)
        self.assertEqual(self.codec.decode(closed).life.service_track_id,2**53+1)
        changed=self.mutate(self.batch,lambda v:v['body']['measurements'][0].update(
            confidence=Decimal('0.80000000000000000001')))
        with self.assertRaises(Blocked) as raised:self.codec.decode(changed)
        self.assertEqual(raised.exception.code,'NUMERIC_PRECISION')

    def test_strict_json_and_resource_limits(self):
        bad=[b'{"org_id":1,"org_id":2}',b'{"x":NaN}',b'{"x":Infinity}',b'{"x":1e99999}',
             b'['*17+b'0'+b']'*17,b' '* (MAX_BYTES+1),b'{"x":'+b'['+b'0,'*128+b'0]}']
        for raw in bad:
            with self.subTest(raw=raw[:40]):
                with self.assertRaises(Blocked):self.codec.decode(raw)

    def mutate(self,raw,change):
        value=bounded_json(raw)
        change(value)
        value['body_sha256']=sha256(canonical_json_bytes(value['body'])).hexdigest()
        return canonical_json_bytes(value)

    def test_unknown_fields_types_cross_org_and_hash(self):
        changes=[lambda v:v.update(extra=True),lambda v:v['body']['life'].update(extra=True),
            lambda v:v['body']['life'].update(service_track_id=True),
            lambda v:v['body']['life'].update(service_track_id='1'),
            lambda v:v.update(org_id=str(uuid4())),
            lambda v:v['body']['life']['context'].update(org_id=str(uuid4())),
            lambda v:v['body']['measurements'][0].update(confidence=True)]
        for change in changes:
            with self.subTest(change=change):
                with self.assertRaises(Blocked):self.codec.decode(self.mutate(self.batch,change))
        bad=bounded_json(self.batch)
        bad['body_sha256']='0'*64
        with self.assertRaises(Blocked):self.codec.decode(canonical_json_bytes(bad))

    def test_batch_completeness_is_explicit_not_closure(self):
        for change in (lambda v:v['body'].update(complete=False),
                       lambda v:v['body'].pop('complete'),
                       lambda v:v['body']['measurements'].pop(),
                       lambda v:v['body']['expected_attempt_ids'].append('missing')):
            with self.assertRaises(Blocked):self.codec.decode(self.mutate(self.batch,change))
        state,out=reduce_message(self.codec,'aggregator',self.codec.decode(self.closed),None)
        self.assertEqual((state['status'],out),('waiting',[]))

    def test_waiting_plate_persists_without_blocking_other_vehicle(self):
        closed,_=fixture(self.codec,plate='enabled')
        state,_=reduce_message(self.codec,'aggregator',self.codec.decode(closed),None)
        state,out=reduce_message(self.codec,'aggregator',self.codec.decode(self.terminal),state)
        self.assertEqual((state['status'],out),('waiting',[]))
        restored=bounded_json(canonical_json_bytes(state))
        self.assertEqual(restored['status'],'waiting')
        ready,_=reduce_message(self.codec,'aggregator',self.codec.decode(self.closed),None)
        ready,out=reduce_message(self.codec,'aggregator',self.codec.decode(self.terminal),ready)
        self.assertEqual((ready['status'],len(out)),('ready',1))

    def test_identical_reducer_retries_do_not_republish_and_changes_conflict(self):
        message=self.codec.decode(self.batch)
        state,out=reduce_message(self.codec,'colour',message,None)
        identical,outputs=reduce_message(self.codec,'colour',message,state)
        self.assertEqual((identical,outputs),(state,[]))
        altered=self.mutate(self.batch,lambda v:v['body']['measurements'][0].update(quality=.7))
        with self.assertRaisesRegex(Blocked,'accepted batch changed'):
            reduce_message(self.codec,'colour',self.codec.decode(altered),state)
        state,_=reduce_message(self.codec,'aggregator',self.codec.decode(self.closed),None)
        state,_=reduce_message(self.codec,'aggregator',self.codec.decode(self.terminal),state)
        _,out=reduce_message(self.codec,'aggregator',self.codec.decode(self.terminal),state)
        self.assertEqual(out,[])

    def test_mismatched_lifetime_batch_and_colour_claims_block(self):
        state,_=reduce_message(self.codec,'aggregator',self.codec.decode(self.closed),None)
        other=self.mutate(self.terminal,lambda v:v['body'].update(batch_id=str(uuid4())))
        with self.assertRaisesRegex(Blocked,'batch identity'):
            reduce_message(self.codec,'aggregator',self.codec.decode(other),state)
        altered=self.mutate(self.terminal,lambda v:v['body']['result']['claims'].update(confidence=.4))
        with self.assertRaises(Blocked):self.codec.decode(altered)

    def test_delivery_rejects_unknown_nested_fields_and_changed_natural_claims(self):
        state,_=reduce_message(self.codec,'aggregator',self.codec.decode(self.closed),None)
        _,out=reduce_message(self.codec,'aggregator',self.codec.decode(self.terminal),state)
        raw=out[0][1]
        for change in (lambda b:b['omissions'][0].update(extra='no'),
                       lambda b:b['facts'][1]['delivery'].update(conf=.4),
                       lambda b:b['colour_provenance'].update(extra='no')):
            def update(v):
                change(v['body']['bundle'])
                v['body']['bundle_sha256']=sha256(canonical_json_bytes(v['body']['bundle'])).hexdigest()
            with self.assertRaises(Blocked):self.codec.decode(self.mutate(raw,update))

    def test_retained_journal_state_restores_exact_mathematical_integer_types(self):
        from vehicle.common.redis_scalar.journal import Journal
        state,_=reduce_message(self.codec,'aggregator',self.codec.decode(self.closed),None)
        raw=canonical_json_bytes(state)
        class StoredSnapshot:
            def hget(self,*_):return raw
        journal=Journal(StoredSnapshot(),namespace='pure',trusted_org=ORG,role='aggregator',consumer='test')
        restored=journal.read_state('vehicle')
        self.assertIs(type(restored['closed']['life']['raw_track_id']),int)
        ready,out=reduce_message(self.codec,'aggregator',self.codec.decode(self.terminal),restored)
        self.assertEqual((ready['status'],len(out)),('ready',1))

    def test_changed_accepted_batch_identity_conflicts_but_true_retry_is_identical(self):
        message=self.codec.decode(self.batch)
        state,out=reduce_message(self.codec,'colour',message,None)
        replacement=self.codec.decode(self.mutate(self.batch,lambda v:v.update(event_id=str(uuid4()))))
        with self.assertRaises(Blocked) as raised:
            reduce_message(self.codec,'colour',replacement,state)
        self.assertEqual(raised.exception.code,'PAYLOAD_CONFLICT')
        self.assertEqual(state['batch_id'],message.event_id)
        self.assertEqual(self.codec.decode(out[0][1]).body['batch_id'],message.event_id)
        same,outputs=reduce_message(self.codec,'colour',message,state)
        self.assertEqual((same,outputs),(state,[]))
        # Legacy retained state cannot authenticate its original batch identity.
        with self.assertRaises(Blocked):
            reduce_message(self.codec,'colour',message,{'batch':message.body})

    def test_invalid_closure_is_rejected_before_state_lookup_or_retention(self):
        def duplicate(v):
            v['body']['classes'].append(copy.deepcopy(v['body']['classes'][0]))
        def inconsistent_duplicate(v):
            extra=copy.deepcopy(v['body']['classes'][1])
            extra['receipt']['monotonic']=101.25
            v['body']['classes'].append(extra)
        changes=(duplicate,inconsistent_duplicate,
            lambda v:v['body']['classes'][0].update(confidence=True),
            lambda v:v['body']['classes'][0].update(confidence=1e-50),
            lambda v:v['body']['classes'][0].update(label='person'),
            lambda v:v['body']['classes'][0]['receipt'].update(frame_index=6),
            lambda v:v['body']['classes'][0]['receipt'].update(monotonic=100.25))
        for change in changes:
            with self.subTest(change=change):
                worker=Worker(None,codec=self.codec,namespace='pure',role='aggregator',consumer='test')
                worker.journal.seen=Mock(return_value=None)
                worker.journal.read_state=Mock(return_value=None)
                worker.journal.handoff=Mock()
                raw=self.mutate(self.closed,change)
                worker.process('engine.lifecycle',b'1-0',{b'json':raw})
                worker.journal.read_state.assert_not_called()
                rejected=worker.journal.handoff.call_args.kwargs
                self.assertNotIn('vehicle',rejected)
                self.assertNotIn('state',rejected)
                self.assertEqual(rejected['outputs'][0][0],'diagnostics.aggregator')
        # A later valid closure with the original event ID can still complete.
        state,_=reduce_message(self.codec,'aggregator',self.codec.decode(self.closed),None)
        ready,out=reduce_message(self.codec,'aggregator',self.codec.decode(self.terminal),state)
        self.assertEqual((ready['status'],len(out)),('ready',1))

    def test_shared_class_admission_preserves_lifetime_attribution(self):
        owner,_,samples=memory_fixture()
        self.assertEqual(validate_class_samples(owner.lifetime,samples),samples)
        wrong=replace(samples[0],identity=replace(samples[0].identity,service_track_id=2))
        with self.assertRaises(Blocked) as raised:
            validate_class_samples(owner.lifetime,(wrong,)+samples[1:])
        self.assertEqual(raised.exception.code,'PARENT_MISMATCH')

    def test_pure_imports_no_clients_or_heavy_modules(self):
        code='''
import sys
import vehicle.common.redis_scalar.codec
import vehicle.common.redis_scalar.reducer
import vehicle.common.redis_scalar.journal
import vehicle.common.redis_scalar.worker
import vehicle.common.redis_scalar.synthetic
for prefix in ('redis','cv2','numpy','torch','ultralytics','paddle','openai','sqlite3',
 'vehicle.common.db1_rows.builder','vehicle.common.observation_coordinator'):
 assert not any(n==prefix or n.startswith(prefix+'.') for n in sys.modules),prefix
'''
        subprocess.run([sys.executable,'-c',code],check=True)


class SchedulingTests(unittest.TestCase):
    """Exercise actual Worker.step selection, not Redis/PEL semantics."""
    def make_worker(self):
        client=Mock()
        client.xautoclaim.side_effect=lambda *a,**kw:[b'0-0',[(b'1-0',{b'mode':b'pending'})],[]]
        client.xreadgroup.side_effect=lambda group,consumer,streams,**kw:[
            (next(iter(streams)).encode(),[(b'2-0',{b'mode':b'new'})])]
        worker=Worker(client,codec=Mock(org=ORG),namespace='pure',role='aggregator',consumer='test')
        worker.journal.token=b'owned'
        worker.journal.renew=Mock()
        worker.journal.repair=Mock(return_value=False)
        worker.process=Mock()
        return worker,client

    def test_continuously_ready_streams_and_pending_new_both_have_bounded_service(self):
        worker,client=self.make_worker()
        for _ in range(8):self.assertTrue(worker.step())
        served=[(call.args[0],call.args[2][b'mode']) for call in worker.process.call_args_list]
        for source in worker.sources:
            modes=[mode for stream,mode in served if stream==source]
            self.assertEqual(len(modes),4,served)
            self.assertEqual(modes,[b'pending',b'new',b'pending',b'new'])
        self.assertTrue(all(served[i][0]!=served[i+1][0] for i in range(7)),served)
        self.assertEqual(client.xautoclaim.call_count+client.xreadgroup.call_count,8)

    def test_repair_precedes_polling_and_empty_scan_remains_bounded(self):
        worker,client=self.make_worker()
        worker.journal.repair.return_value=True
        self.assertTrue(worker.step())
        client.xautoclaim.assert_not_called();client.xreadgroup.assert_not_called()
        worker.journal.repair.return_value=False
        client.xautoclaim.side_effect=lambda *a,**kw:[b'0-0',[],[]]
        client.xreadgroup.side_effect=lambda *a,**kw:[]
        self.assertFalse(worker.step())
        self.assertEqual(client.xautoclaim.call_count+client.xreadgroup.call_count,4)
        worker.process.assert_not_called()

    def test_processing_error_does_not_reset_first_stream_preference(self):
        worker,_=self.make_worker()
        worker.process.side_effect=RuntimeError('injected processing failure')
        with self.assertRaises(RuntimeError):worker.step()
        worker.process.side_effect=None
        worker.step()
        self.assertEqual([call.args[0] for call in worker.process.call_args_list],worker.sources)


if __name__=='__main__':unittest.main()
