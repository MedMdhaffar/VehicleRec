"""Pure bounded Plate transport tests; no Redis connections or recognizers."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
from itertools import permutations
import json
import subprocess
import sys
import unittest
from unittest.mock import Mock
from uuid import uuid4

from contracts.db1.reference import canonical_json_bytes
from vehicle.common.db1_rows.core import Blocked,FrozenJSON
from vehicle.common.redis_scalar.worker import load_plate_codec,Worker
from vehicle.common.redis_scalar.synthetic import ORG,IDENTITY_NAMESPACE
from vehicle.common.redis_scalar.plate_synthetic import fixture,SCENARIOS
from vehicle.common.redis_scalar.codec import bounded_json,unpack_life
from vehicle.common.redis_scalar.plate_codec import (MAX_WIRE,WIRE_LIMITS,STATE_LIMIT,INTENT_LIMIT,
    pack_transcript,unpack_transcript,read_plate)
from vehicle.common.redis_scalar.reducer import reduce_message
from vehicle.common.redis_scalar.journal import Journal


def altered(raw,fn):
    v=bounded_json(raw,max_bytes=MAX_WIRE,max_depth=32,max_nodes=200000,max_string=4096)
    fn(v)
    v['body_sha256']=sha256(canonical_json_bytes(v['body'])).hexdigest()
    return canonical_json_bytes(v)


def large_batch(codec,count=32,operations=1):
    _,_,raw,_=fixture(codec,scenario='unreadable')
    body=codec.decode(raw).body
    template=body['transcript']['attempts'][0]
    attempts=[]
    for i in range(count):
        ops=[dict(operation_id=f'whole-{i}-{j}',sequence=j+1,dependencies=[],kind='whole_ocr',
            outcome='succeeded',model=template['operations'][0]['model'],raw_text='X'*4096,
            confidence=.9,marker=None,route='private_local') for j in range(operations)]
        attempts.append(dict(attempt_id=f'attempt-{i}',submission_ordinal=i+1,
            candidate_id=template['candidate_id'],expected_operation_ids=sorted(o['operation_id'] for o in ops),operations=ops))
    body['transcript']['attempts']=attempts
    body['transcript']['expected_attempt_ids']=sorted(a['attempt_id'] for a in attempts)
    return codec.make('plate.batch',body)


class RedisPlateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.codec=load_plate_codec(ORG,IDENTITY_NAMESPACE)

    def components(self,scenario='whole'):
        closed,colour,batch,expected=fixture(self.codec,scenario=scenario)
        ps,po=reduce_message(self.codec,'plate',self.codec.decode(batch),None)
        _,co=reduce_message(self.codec.v0,'colour',self.codec.v0.decode(colour),None)
        return closed,co[0][1],batch,po,expected

    def test_exact_parity_all_orders_whole_and_both_regional_orders(self):
        for scenario in ('whole','region-left-first','region-right-first'):
            closed,colour,_,plate,expected=self.components(scenario)
            for order in permutations((closed,colour,plate[0][1])):
                state=None
                for n,raw in enumerate(order):
                    state,out=reduce_message(self.codec,'aggregator_plate',self.codec.decode(raw),state)
                    self.assertEqual(bool(out),n==2)
                delivery=self.codec.decode(out[0][1])
                self.assertEqual(canonical_json_bytes(delivery.body['bundle']),expected.payload.canonical)
                self.assertEqual(delivery.body['bundle_sha256'],expected.digest)
                for raw in order:
                    state,again=reduce_message(self.codec,'aggregator_plate',self.codec.decode(raw),state)
                    self.assertEqual(again,[])
                if scenario.startswith('region'):
                    model=delivery.body['bundle']['facts'][-1]['delivery']['selected_attempt']['model']
                    self.assertEqual(model['version'],9 if scenario.endswith('left-first') else 5)

    def test_outcomes_truthful_omissions_and_exact_original_receipts(self):
        for scenario in SCENARIOS:
            closed,colour,batch,plate,expected=self.components(scenario)
            t=self.codec.decode(batch)
            transcript=unpack_transcript(t.body['transcript'],t.life)
            self.assertEqual(canonical_json_bytes(pack_transcript(transcript)),canonical_json_bytes(t.body['transcript']))
            self.assertEqual(self.codec.decode(t.canonical).canonical,t.canonical)
            state=None
            for raw in (closed,colour,*(r for _,r in plate)):
                state,out=reduce_message(self.codec,'aggregator_plate',self.codec.decode(raw),state)
            if scenario=='waiting':
                self.assertEqual(plate,[]);self.assertEqual(out,[]);self.assertEqual(state['status'],'waiting')
            else:
                value=self.codec.decode(out[0][1]).body['bundle']
                self.assertEqual(canonical_json_bytes(value),expected.payload.canonical)
                if transcript.candidates:self.assertIsNone(transcript.candidates[0].quality)
                self.assertNotIn('no_plate',[f['delivery'].get('outcome') for f in value['facts']])

    def test_wait_survives_handoff_and_single_completion_is_idempotent(self):
        closed,colour,initial,plate,_=self.components('waiting')
        first=self.codec.decode(initial)
        state,out=reduce_message(self.codec,'plate',first,None)
        self.assertEqual(out,[]);self.assertEqual(state['status'],'waiting')
        state=FrozenJSON.of(state).value()
        _,_,completion,expected=fixture(self.codec,scenario='waiting',revision=1)
        state,out=reduce_message(self.codec,'plate',self.codec.decode(completion),state)
        self.assertEqual(state['status'],'terminal');self.assertEqual(len(out),1)
        for retry in (first,self.codec.decode(completion)):
            again,outputs=reduce_message(self.codec,'plate',retry,state)
            self.assertEqual(outputs,[]);self.assertEqual(again,state)
        joined=None
        for raw in (closed,colour,out[0][1]):
            joined,outputs=reduce_message(self.codec,'aggregator_plate',self.codec.decode(raw),joined)
        self.assertEqual(self.codec.decode(outputs[0][1]).body['bundle_sha256'],expected.digest)
        with self.assertRaises(Blocked):reduce_message(self.codec,'plate',self.codec.decode(completion),None)

    def test_changed_batch_revision_or_terminal_content_conflicts(self):
        _,_,raw,_=fixture(self.codec)
        m=self.codec.decode(raw);state,_=reduce_message(self.codec,'plate',m,None)
        for change in ('batch','text','revision'):
            body=deepcopy(m.body)
            if change=='batch':body['batch_id']=str(uuid4())
            elif change=='text':body['transcript']['attempts'][0]['operations'][0]['raw_text']='124 تونس 4567'
            else:body['revision']=1
            candidate=self.codec.decode(self.codec.make('plate.batch',body))
            with self.assertRaises(Blocked):reduce_message(self.codec,'plate',candidate,state)
        self.assertEqual(state['revisions']['0']['event_id'],m.event_id)

    def test_completion_cannot_change_original_plan_known_results_or_quality(self):
        _,_,initial,_=fixture(self.codec,scenario='waiting')
        state,_=reduce_message(self.codec,'plate',self.codec.decode(initial),None)
        _,_,raw,_=fixture(self.codec,scenario='waiting',revision=1)
        good=self.codec.decode(raw).body
        changes=[lambda b:b['transcript']['candidates'][0].update(quality=.7),
                 lambda b:b['transcript']['attempts'][0]['operations'][1]['model'].update(version=7),
                 lambda b:b['transcript']['attempts'][0].update(submission_ordinal=8),
                 lambda b:b['transcript'].update(admission_complete=False)]
        for fn in changes:
            body=deepcopy(good);fn(body)
            message=self.codec.decode(self.codec.make('plate.batch',body))
            with self.assertRaises(Blocked):reduce_message(self.codec,'plate',message,state)

    def test_malformed_unknown_versions_fields_numbers_and_identities(self):
        _,_,raw,_=fixture(self.codec)
        cases=[lambda v:v.update(schema_version='future'),lambda v:v.update(policy_version='future'),
            lambda v:v.update(secret='unexpected'),lambda v:v.update(event_id=str(uuid4())),
            lambda v:v.update(org_id=str(uuid4())),
            lambda v:v['body']['transcript']['parent'].update(camera_id='wrong'),
            lambda v:v['body']['transcript']['candidates'][0]['parent'].update(run_id='wrong'),
            lambda v:v['body']['transcript']['candidates'][0]['parent'].update(service_track_id=99),
            lambda v:v['body']['transcript']['attempts'][0]['operations'][0].update(confidence=True),
            lambda v:v['body']['transcript']['attempts'][0]['operations'][0].update(confidence=Decimal('.960000000000000000001'))]
        for fn in cases:
            with self.assertRaises(Blocked):self.codec.decode(altered(raw,fn))
        for bad in (b'{"a":1,"a":2}',b'{"a":NaN}',b'{"a":Infinity}',b'[',b'[[[['):
            with self.assertRaises(Blocked):self.codec.decode(bad)

    def test_terminal_claims_and_original_execution_model_are_authenticated(self):
        _,_,_,outputs,_=self.components('region-right-first');raw=outputs[0][1]
        for fn in (lambda v:v['body']['claims']['selected'].update(confidence=.1),
                   lambda v:v['body']['claims']['selected']['model'].update(version=99),
                   lambda v:v['body'].update(provenance_sha256='0'*64)):
            with self.assertRaises(Blocked):self.codec.decode(altered(raw,fn))

    def test_dependency_mismatch_and_later_failure_are_checked_at_decode(self):
        _,_,raw,_=fixture(self.codec,scenario='region-left-first');body=self.codec.decode(raw).body
        ops=body['transcript']['attempts'][0]['operations']
        marker=deepcopy(ops[1]);marker.update(operation_id='new-marker',sequence=3)
        marker['marker'].update(bbox=[20,0,30,30],center_x=25.)
        ops[2]['sequence']=4;ops[3]['sequence']=5;ops[3]['dependencies']=['new-marker','whole'];ops.insert(2,marker)
        a=body['transcript']['attempts'][0];a['expected_operation_ids']=sorted(o['operation_id'] for o in ops)
        with self.assertRaises(Blocked):self.codec.make('plate.batch',body)
        _,_,raw,_=fixture(self.codec,scenario='region-right-first');body=self.codec.decode(raw).body
        ops=body['transcript']['attempts'][0]['operations']
        failed=dict(operation_id='failed',sequence=5,dependencies=[],kind='whole_ocr',outcome='failed',
                    model=dict(model_name='failed-model',version=99,task='ocr'),raw_text=None,confidence=None,marker=None,route='private_local')
        ops.append(failed);body['transcript']['attempts'][0]['expected_operation_ids'].append('failed')
        t=unpack_transcript(body['transcript'],unpack_life(body['life']));body['transcript']=pack_transcript(t)
        state,outputs=reduce_message(self.codec,'plate',self.codec.decode(self.codec.make('plate.batch',body)),None)
        terminal=self.codec.decode(outputs[0][1]).body
        self.assertEqual(terminal['claims']['outcome'],'unreadable')
        self.assertEqual(terminal['claims']['selected']['model']['version'],99)

    def test_wrong_parent_or_batch_cannot_poison_accepted_join(self):
        closed,colour,_,plate,_=self.components()
        state,_=reduce_message(self.codec,'aggregator_plate',self.codec.decode(closed),None)
        original=deepcopy(state)
        body=deepcopy(self.codec.decode(plate[0][1]).body);body['batch_id']=str(uuid4())
        with self.assertRaises(Blocked):reduce_message(self.codec,'aggregator_plate',self.codec.decode(self.codec.make('plate.terminal',body)),state)
        for field in ('run_id','camera_id'):
            body=deepcopy(self.codec.decode(colour).body)
            body['life']['context'][field]='other'
            raw=self.codec.v0.encode('colour.terminal',str(uuid4()),body)
            with self.assertRaises(Blocked):reduce_message(self.codec,'aggregator_plate',self.codec.decode(raw),state)
        self.assertEqual(state,original)
        for raw in (colour,plate[0][1]):state,out=reduce_message(self.codec,'aggregator_plate',self.codec.decode(raw),state)
        self.assertEqual(len(out),1)

    def test_equivalent_typed_order_canonicalizes_and_retained_v0_is_rejected(self):
        _,_,raw,_=fixture(self.codec,scenario='region-left-first');m=self.codec.decode(raw)
        t=unpack_transcript(m.body['transcript'],m.life)
        a=t.attempts[0]
        t=replace(t,attempts=(replace(a,operations=tuple(reversed(a.operations)),
            expected_operation_ids=tuple(reversed(a.expected_operation_ids))),))
        self.assertEqual(canonical_json_bytes(pack_transcript(t)),canonical_json_bytes(m.body['transcript']))
        for role in ('plate','aggregator_plate'):
            with self.assertRaises(Blocked):reduce_message(self.codec,role,m,{'batch':{},'status':'waiting'})
        with self.assertRaises(Blocked):self.codec.v0.decode(raw)
        for role,sources in [('aggregator',['engine.lifecycle','results.colour']),
                             ('aggregator_plate',['engine.lifecycle.plate','results.colour','results.plate'])]:
            w=Worker(Mock(),codec=self.codec.v0 if role=='aggregator' else self.codec,namespace='test',role=role,consumer='c')
            self.assertEqual(w.sources,sources)
            self.assertEqual(w.journal.max_state_bytes,196608 if role=='aggregator' else STATE_LIMIT)

    def test_valid_above_v0_limit_and_over_wire_limit(self):
        raw=large_batch(self.codec)
        self.assertGreater(len(raw),65536)
        with self.assertRaises(Blocked):self.codec.v0.decode(raw)
        state,out=reduce_message(self.codec,'plate',self.codec.decode(raw),None)
        self.assertEqual(self.codec.decode(out[0][1]).body['claims']['outcome'],'unreadable')
        with self.assertRaises(Blocked):self.codec.decode(b' '*(MAX_WIRE+1))
        with self.assertRaises(Blocked):large_batch(self.codec,70,16) # exceeds canonical 4MiB provenance, below batch wire cap

    def test_state_and_output_intent_limits_precede_any_redis_mutation(self):
        for limits in (dict(max_state_bytes=10),dict(max_intent_bytes=10)):
            client=Mock();client.hget.return_value=b'0'
            j=Journal(client,namespace='test',trusted_org=ORG,role='plate',consumer='c',**limits)
            with self.assertRaises(Blocked):j.handoff(source='engine.evidence.plate',entry_id=b'1-0',
                marker='seen:test',digest='a'*64,vehicle='v',state={'large':'x'*100},outputs=[('results.plate',b'{}')])
            client.eval.assert_not_called()
        self.assertEqual(INTENT_LIMIT,2*MAX_WIRE+4096)
        client=Mock()
        j=Journal(client,namespace='test',trusted_org=ORG,role='plate',consumer='c')
        with self.assertRaises(Blocked):j.handoff(source='engine.evidence.plate',entry_id=b'1-0',
            marker='seen:test',digest='a'*64,outputs=[('results.plate',b' '*(MAX_WIRE+1))])
        client.eval.assert_not_called()


    def test_three_stream_fairness_pending_new_and_repair_priority(self):
        w=Worker(Mock(),codec=self.codec,namespace='test',role='aggregator_plate',consumer='c')
        w.journal.token=b't';w.journal.repair=Mock(return_value=False)
        w.client.xautoclaim.return_value=[b'0-0',[(b'1-0',{b'json':b'{}'})],[]]
        def new(*args,**kwargs):return [(b's',[(b'2-0',{b'json':b'{}'})])]
        w.client.xreadgroup.side_effect=new
        served=[];w.process=lambda source,entry,fields:served.append((source,entry))
        for _ in range(12):self.assertTrue(w.step())
        self.assertEqual([s for s,_ in served],w.sources*4)
        for source in w.sources:self.assertEqual([e for s,e in served if s==source],[b'1-0',b'2-0']*2)
        w.journal.repair.return_value=True
        w.client.reset_mock();w.step();w.client.xautoclaim.assert_not_called();w.client.xreadgroup.assert_not_called()

    def test_pure_import_no_connection_or_inference(self):
        code="""
import sys,importlib.abc
class Guard(importlib.abc.MetaPathFinder):
 def find_spec(self,name,path=None,target=None):
  if name.split('.')[0] in {'redis','cv2','numpy','torch','ultralytics','paddle','paddleocr','sqlite3','psycopg','openai'}:
   raise AssertionError(name)
sys.meta_path.insert(0,Guard())
from vehicle.common.redis_scalar.worker import load_plate_codec
from vehicle.common.redis_scalar.plate_reducer import reduce_plate
from vehicle.common.redis_scalar.plate_synthetic import fixture
from vehicle.common.redis_scalar.synthetic import ORG,IDENTITY_NAMESPACE
fixture(load_plate_codec(ORG,IDENTITY_NAMESPACE))
"""
        p=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)

    def test_cross_component_receipt_conflict_rejected_before_partial_join(self):
        closed,colour,_,plate,_=self.components()
        state,_=reduce_message(self.codec,'aggregator_plate',self.codec.decode(colour),None)
        original=deepcopy(state)
        body=deepcopy(self.codec.decode(closed).body)
        body['classes'][1]['receipt']['monotonic']=101.5
        wrong=self.codec.make('vehicle.closed.plate',body) # independently valid closure
        with self.assertRaises(Blocked) as error:
            reduce_message(self.codec,'aggregator_plate',self.codec.decode(wrong),state)
        self.assertEqual(error.exception.code,'FRAME_RECEIPT_MISMATCH')
        self.assertEqual(state,original)
        for raw in (closed,plate[0][1]):state,out=reduce_message(self.codec,'aggregator_plate',self.codec.decode(raw),state)
        self.assertEqual(len(out),1)

    def test_unknown_retained_journal_profile_is_not_repaired_as_v0(self):
        client=Mock();client.hget.return_value=b'{"source":"engine.evidence.plate","entry_id":"1-0","outputs":[]}'
        j=Journal(client,namespace='test',trusted_org=ORG,role='plate',consumer='c');j.token=b't'
        with self.assertRaises(Blocked) as error:j.repair()
        self.assertEqual(error.exception.code,'STATE_VERSION_CONFLICT')
        self.assertEqual(client.eval.call_count,1) # renewal only; no publication/ACK

    def test_intent_json_escaping_retains_exact_output_inside_profile(self):
        _,_,batch,_=fixture(self.codec,scenario='unreadable')
        body=self.codec.decode(batch).body
        body['transcript']['attempts'][0]['operations'][0]['raw_text']='"\\\nت'*256
        _,outputs=reduce_message(self.codec,'plate',self.codec.decode(self.codec.make('plate.batch',body)),None)
        client=Mock();client.hget.return_value=b'0'
        j=Journal(client,namespace='test',trusted_org=ORG,role='plate',consumer='c')
        j.call=Mock(return_value=b'prepared');j.repair=Mock(return_value=True)
        j.handoff(source='engine.evidence.plate',entry_id=b'1-0',marker='seen:test',digest='a'*64,
                  vehicle='v',state={'state_version':'plate-state-v1'},outputs=outputs)
        raw=j.call.call_args.args[-3]
        self.assertEqual(json.loads(raw)['outputs'][0]['data'].encode(),outputs[0][1])
        self.assertLessEqual(len(raw),2*len(outputs[0][1])+4096)
        self.assertLessEqual(len(raw),INTENT_LIMIT)

    def test_specific_batch_limit_applies_below_global_delivery_limit(self):
        _,_,raw,_=fixture(self.codec)
        oversized=raw+b' '*(WIRE_LIMITS['plate.batch']-len(raw)+1)
        self.assertLess(len(oversized),MAX_WIRE)
        with self.assertRaises(Blocked) as error:self.codec.decode(oversized)
        self.assertEqual(error.exception.code,'WIRE_LIMIT')

    def test_v1_delivery_rejects_unknown_fact_omission_and_provenance_fields(self):
        closed,colour,_,plate,_=self.components()
        state=None
        for raw in (closed,colour,plate[0][1]):
            state,out=reduce_message(self.codec,'aggregator_plate',self.codec.decode(raw),state)
        output=out[0][1]
        changes=[lambda p:p.update(extra='unknown'),lambda p:p['facts'][0].update(extra='unknown'),
                 lambda p:p['facts'][0]['delivery'].update(run_key=99),
                 lambda p:p['omissions'][0].update(extra='unknown'),
                 lambda p:p['plate_provenance'].update(extra='unknown')]
        for change in changes:
            def mutate(v):
                change(v['body']['bundle'])
                v['body']['bundle_sha256']=sha256(canonical_json_bytes(v['body']['bundle'])).hexdigest()
            with self.assertRaises(Blocked):self.codec.decode(altered(output,mutate))

    def test_completion_before_initial_stays_pending_without_ack_or_rejection(self):
        _,_,raw,_=fixture(self.codec,scenario='waiting',revision=1)
        w=Worker(Mock(),codec=self.codec,namespace='test',role='plate',consumer='c')
        w.journal.seen=Mock(return_value=None);w.journal.read_state=Mock(return_value=None)
        w.journal.handoff=Mock()
        w.process('engine.evidence.plate',b'1-0',{b'json':raw})
        w.journal.handoff.assert_not_called()
