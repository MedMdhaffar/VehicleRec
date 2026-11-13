"""Progressive domain/session regressions. Native calls are explicitly injected."""
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4
import time, unittest

from vehicle.common.redis_scalar.worker import load_request_codec
from vehicle.common.redis_scalar.synthetic import ORG, IDENTITY_NAMESPACE
from vehicle.common.engine_plate_injected import fixture
from vehicle.common.engine_plate import PlateExecutionAdapter
from vehicle.common.plate_metadata import PlateMetadata
from vehicle.common.plate_requests.codec import digest
from vehicle.common.plate_requests.engine import EngineSession
from vehicle.common.plate_requests.policy import reduce_event, common
from vehicle.common.db1_rows.core import Blocked


def session_fixture(codec,*,scenario='whole',track=1,capabilities=('whole_ocr','marker','left_ocr','right_ocr'),operation_budget=32,no_crop=None):
    base,crop,plan,calls,closed,colour,batch=fixture(codec.v1,scenario=scenario,track=track,admit=False)
    base.stop()
    expected=() if no_crop else (plan.attempt_id,)
    adapter=PlateExecutionAdapter(base.lifetime,tuple(base._executors.values()),expected_attempt_ids=expected,progressive=True)
    if no_crop=='rejected':adapter.observe_candidate(replace(crop.binding.candidate,crop_accepted=False))
    elif not no_crop:adapter.observe_candidate(crop.binding.candidate)
    adapter.mark_detection_complete()
    offers=[] if no_crop else [dict(attempt_id=plan.attempt_id,ordinal=plan.submission_ordinal,candidate_id=plan.candidate_id)]
    session=EngineSession(codec,adapter,{} if no_crop else {plan.candidate_id:crop},offers,session=str(uuid4()),batch_id=batch,
        bindings=dict(whole_ocr='whole',marker='marker',left_ocr='left',right_ocr='right'),capabilities=capabilities,operation_budget=operation_budget)
    return session,crop,calls,closed,colour


def complete_loop(codec,session,*,order=('left_ocr','right_ocr'),evidence_first=False):
    state,outputs=reduce_event(codec,codec.decode(session.raw),None,regional_order=order)
    requests=[]
    for _ in range(40):
        if not outputs:return state,requests,None
        _,raw=outputs[0]
        message=codec.decode(raw)
        if message.kind=='seal':
            sealed=session.finish_generation(message)
            if sealed is None:return state,requests,None
            state,outputs=reduce_event(codec,codec.decode(sealed),state,regional_order=order)
            return state,requests,outputs[0][1]
        requests.append(message.body)
        admission=session.admission(message)
        session.allow_execution(message.body['request_id'])
        assert session.adapter.drain(1)
        evidence=[codec.make('evidence',b) for b in session.collect()]
        incoming=evidence+[admission] if evidence_first else [admission]+evidence
        for packet in incoming:state,outputs=reduce_event(codec,codec.decode(packet),state,regional_order=order)
    raise AssertionError('bounded loop did not settle')


class RequestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.codec=load_request_codec(ORG,IDENTITY_NAMESPACE)

    def make(self,**kwargs):
        s,crop,*rest=session_fixture(self.codec,**kwargs)
        def dispose():
            s.adapter.stop(drain_timeout=1,finish_admitted=True)
            if s.adapter._active:s.adapter._active.context.retired() # only injected callbacks owned by this fixture
            s.adapter.poll();s.adapter.cleanup(discard=True)
            if not crop.released:crop.release()
        self.addCleanup(dispose)
        return s,crop,*rest

    def first(self,s):
        state,out=reduce_event(self.codec,self.codec.decode(s.raw),None)
        return state,self.codec.decode(out[0][1])

    def test_whole_sufficient_no_unnecessary_regions_original_provenance(self):
        s,crop,calls,*_=self.make()
        state,requests,raw=complete_loop(self.codec,s)
        self.assertEqual([r['operation'] for r in requests],['whole_ocr','marker'])
        self.assertEqual([len(s.adapter._plans[a].operations) for a in s.adapter._plans],[2])
        self.assertEqual(set(calls),{'whole','marker'})
        self.assertFalse(crop.released);self.assertFalse(s.adapter.confirmed)
        terminal=self.codec.v1.decode(raw)
        self.assertEqual(terminal.body['claims']['outcome'],'read')
        self.assertEqual(s.adapter.snapshot().candidates[0],crop.binding.candidate)
        self.assertEqual(state['status'],'terminal')
        s.complete(raw);self.assertTrue(crop.released);self.assertEqual(crop.release_calls,1)
        self.assertEqual(s.adapter.snapshot().attempts[0].operations[0].model,calls['whole'].model)

    def test_regions_both_orders_exact_generations_and_reordered_admission(self):
        for order in [('left_ocr','right_ocr'),('right_ocr','left_ocr')]:
            s,crop,calls,*_=self.make(scenario='region-left-first')
            state,requests,raw=complete_loop(self.codec,s,order=order,evidence_first=True)
            self.assertEqual([r['operation'] for r in requests],['whole_ocr','marker',*order])
            for r in requests[2:]:
                self.assertEqual({d['operation_id'] for d in r['dependencies']},{r['request_id'] for r in requests[:2]})
                self.assertTrue(all(d['generation']==s.evidence[d['operation_id']]['generation'] for d in r['dependencies']))
            self.assertEqual(self.codec.v1.decode(raw).body['claims']['outcome'],'read')
            self.assertTrue(all(c.calls==1 for c in calls.values()))

    def test_duplicate_request_reuses_acceptance_evidence_without_native_call(self):
        s,_,calls,*_=self.make();_,request=self.first(s)
        raw=s.admission(request);self.assertEqual(raw,s.admission(request));self.assertEqual(calls['whole'].calls,0)
        s.allow_execution(request.body['request_id']);s.adapter.drain(1);e=s.collect()
        self.assertEqual(s.admission(request),raw);s.step();self.assertEqual(s.collect(),[])
        self.assertEqual(calls['whole'].calls,1);self.assertEqual(len(e),1)

    def test_changed_request_identity_conflicts_before_mutation(self):
        s,_,calls,*_=self.make();_,r=self.first(s);s.admission(r)
        changed=dict(r.body,operation='marker')
        with self.assertRaises(Blocked):s.admission(self.codec.decode(self.codec.make('request',changed)))
        self.assertEqual(len(s.accepted),1);self.assertTrue(all(c.calls==0 for c in calls.values()))

    def test_bindings_session_and_generation_rejected_before_native(self):
        for field,value in [('session',str(uuid4())),('candidate_id','different'),('ordinal',2),('attempt_id','different')]:
            s,_,calls,*_=self.make();_,m=self.first(s);r=dict(m.body);r[field]=value;r['request_id']=self.codec.request_id(r)
            result=self.codec.decode(s.admission(self.codec.decode(self.codec.make('request',r))))
            self.assertFalse(result.body['accepted']);self.assertEqual(s.accepted,[])
            self.assertTrue(all(c.calls==0 for c in calls.values()))
        s,_,calls,*_=self.make();state,first=self.first(s)
        admission=s.admission(first);s.allow_execution(first.body['request_id']);s.adapter.drain(1)
        evidence=s.collect()[0]
        state,_=reduce_event(self.codec,self.codec.decode(admission),state)
        state,out=reduce_event(self.codec,self.codec.decode(self.codec.make('evidence',evidence)),state)
        r=self.codec.decode(out[0][1]).body;r['dependencies'][0]['generation']='0'*64
        rejected=self.codec.decode(s.admission(self.codec.decode(self.codec.make('request',r))))
        self.assertEqual(rejected.body['reason'],'dependency');self.assertEqual(calls['marker'].calls,0)

    def test_no_detection_rejected_crop_and_disabled_capability(self):
        for value,reason in [('none','no_detection'),('rejected','rejected_crop')]:
            s,_,calls,*_=self.make(no_crop=value);state,requests,raw=complete_loop(self.codec,s)
            self.assertEqual(requests,[]);self.assertEqual(self.codec.v1.decode(raw).body['claims']['omission_reason'],reason)
            self.assertTrue(all(c.calls==0 for c in calls.values()))
        for capability in ('disabled','unsupported'):
            s,_,calls,*_=self.make(no_crop='none')
            from vehicle.common.redis_scalar.plate_codec import unpack_transcript,pack_transcript
            t=unpack_transcript(s.crop['transcript'],s.adapter.lifetime)
            s.capability=capability;s.crop['transcript']=pack_transcript(replace(t,capability=capability));s.raw=self.codec.make('crop',s.crop)
            state,requests,raw=complete_loop(self.codec,s)
            self.assertEqual(self.codec.v1.decode(raw).body['claims']['omission_reason'],capability+'_capability')

    def test_failed_marker_does_not_request_regions_or_fabricate_success(self):
        s,_,calls,*_=self.make(scenario='region-left-first');calls['marker'].output={'malformed':True}
        state,requests,raw=complete_loop(self.codec,s)
        self.assertEqual([r['operation'] for r in requests],['whole_ocr','marker'])
        self.assertEqual(self.codec.v1.decode(raw).body['claims']['outcome'],'unreadable')
        marker=next(v for v in s.evidence.values() if v['operation']['kind']=='marker')
        self.assertEqual(marker['operation']['outcome'],'failed');self.assertIsNotNone(marker['operation']['model'])

    def test_unsupported_budget_and_unfinished_enabled_remain_waiting(self):
        for kwargs,reason in [(dict(capabilities=('whole_ocr',)),'unsupported_capability'),(dict(operation_budget=1),'operation_budget')]:
            s,_,_,*_=self.make(**kwargs);state,requests,raw=complete_loop(self.codec,s)
            self.assertEqual(state['status'],'blocked');self.assertEqual(state['reason'],reason);self.assertIsNone(raw)
        s,crop,*_=self.make();state,r=self.first(s)
        self.assertEqual(state['status'],'waiting');self.assertFalse(s.adapter._admission_complete)
        self.assertEqual(s.adapter._ops,{})
        with self.assertRaises(Blocked):s.complete(b'{}')
        self.assertFalse(crop.released)

    def test_rejections_explicit_and_never_terminal_no_detection(self):
        s,_,_,*_=self.make();state,r=self.first(s)
        s.crop['capabilities']=[]
        admission=s.admission(r);self.assertEqual(self.codec.decode(admission).body['reason'],'unsupported_capability')
        state,out=reduce_event(self.codec,self.codec.decode(admission),state)
        self.assertEqual(state['status'],'blocked');self.assertEqual(out,[])
        self.assertEqual(s.adapter.records(),())

    def test_exact_seal_not_counts_and_late_evidence_cannot_change_terminal(self):
        s,_,_,*_=self.make();state,_,raw=complete_loop(self.codec,s)
        seal=dict(state['seal'],request_ids=[str(uuid4()) for _ in state['seal']['request_ids']]);seal['request_ids'].sort()
        with self.assertRaises(Blocked):s.finish_generation(self.codec.decode(self.codec.make('seal',seal)))
        altered=deepcopy(next(iter(s.evidence.values())));altered['operation']['raw_text']='changed';altered['generation']=digest(altered['operation'])
        before=deepcopy(state)
        with self.assertRaises(Blocked):reduce_event(self.codec,self.codec.decode(self.codec.make('evidence',altered)),state)
        self.assertEqual(state,before);self.assertEqual(self.codec.v1.decode(raw).body['claims']['outcome'],'read')

    def test_wire_closed_shapes_versions_and_no_executor_selector(self):
        s,_,_,*_=self.make();_,m=self.first(s)
        for key,value in [('executor','arbitrary'),('path','/tmp/private'),('model_url','https://model.invalid'),('credentials','secret')]:
            with self.assertRaises(Blocked):self.codec.make('request',dict(m.body,**{key:value}))
        with self.assertRaises(Blocked):self.codec.v1.decode(s.raw)
        with self.assertRaises(Blocked):self.codec.decode(self.codec.v1.make('plate.batch',dict(life=s.crop['life'],batch_id=s.crop['batch_id'],revision=0,transcript=s.crop['transcript'])))

    def test_native_return_is_not_retirement_and_followup_crop_pinned(self):
        from vehicle.common.engine_plate import InjectedExecutor,decode_ocr
        s,crop,*_=self.make();state,r=self.first(s);ctx=[]
        old=s.adapter._executors['whole']
        def invoke(request,context):
            ctx.append(context);context.started(old.model)
            return b'{"raw_text":"123456","confidence":0.96}'
        s.adapter._executors['whole']=replace(old,invoke=invoke)
        s.admission(r);s.allow_execution(r.body['request_id']);s.step()
        end=time.monotonic()+1
        while not s.adapter._active.returned.is_set() and time.monotonic()<end:time.sleep(.005)
        self.assertTrue(s.adapter._active.returned.is_set());self.assertEqual(s.collect(),[])
        self.assertFalse(crop.released)
        with self.assertRaises(Blocked):s.adapter.cleanup()
        ctx[0].retired();self.assertEqual(len(s.collect()),1)
        self.assertFalse(crop.released) # possible marker/region followup still pins it

    def test_completion_release_failure_is_retryable_no_reinference(self):
        s,crop,calls,*_=self.make();_,_,terminal=complete_loop(self.codec,s)
        before={k:c.calls for k,c in calls.items()};crop.fail_release=1
        with self.assertRaises(RuntimeError):s.complete(terminal)
        self.assertFalse(crop.released);s.complete(terminal)
        self.assertTrue(crop.released);self.assertEqual(crop.release_calls,2)
        self.assertEqual(before,{k:c.calls for k,c in calls.items()})

    def test_capacity_rejection_and_strict_cross_run_camera_lifetime_org(self):
        from vehicle.common.redis_scalar.codec import unpack_life,pack_life
        s,_,calls,*_=self.make();state,r=self.first(s)
        accepted=s.admission(r);s.allow_execution(r.body['request_id']);s.adapter.drain(1)
        evidence=s.collect()[0]
        state,_=reduce_event(self.codec,self.codec.decode(accepted),state)
        state,out=reduce_event(self.codec,self.codec.decode(self.codec.make('evidence',evidence)),state)
        marker=self.codec.decode(out[0][1]);s.crop['operation_budget']=1
        self.assertEqual(self.codec.decode(s.admission(marker)).body['reason'],'capacity')
        self.assertEqual(calls['marker'].calls,0)
        for mutate in [lambda life:replace(life,context=replace(life.context,run_id='other')),
                       lambda life:replace(life,context=replace(life.context,camera=replace(life.context.camera,camera_id='other'))),
                       lambda life:replace(life,lifetime_sequence=2),lambda life:replace(life,raw_track_id=888),
                       lambda life:replace(life,tracker_epoch=1)]:
            session,_,invocations,*_=self.make();_,msg=self.first(session);body=deepcopy(msg.body)
            body['life']=pack_life(mutate(unpack_life(body['life'])));body['request_id']=self.codec.request_id(body)
            rejection=self.codec.decode(session.admission(self.codec.decode(self.codec.make('request',body))))
            self.assertEqual(rejection.body['reason'],'binding');self.assertFalse(session.accepted)
            self.assertTrue(all(c.calls==0 for c in invocations.values()))
        session,_,_,*_=self.make();_,msg=self.first(session);body=deepcopy(msg.body)
        life=unpack_life(body['life']);org=uuid4();context=replace(life.context,org_id=org,camera=replace(life.context.camera,org_id=org))
        body['life']=pack_life(replace(life,context=context));body['request_id']=self.codec.request_id(body)
        with self.assertRaises(Blocked):self.codec.make('request',body)

    def test_stop_and_timeouts_never_prove_semantic_settlement(self):
        from vehicle.common.tests.test_engine_plate import ManualScheduler
        s,crop,*_=self.make();_,r=self.first(s)
        # No request accepted: EOF/stop leaves possible followup ownership intact.
        self.assertTrue(s.adapter.drain(0)) # empty admitted set alone is not generation closure
        self.assertFalse(s.adapter.snapshot().admission_complete)
        with self.assertRaises(Blocked):s.adapter.cleanup()
        s.adapter.stop();self.assertFalse(crop.released)

    def test_scalar_metadata_imports_no_native_runtime(self):
        import subprocess,sys
        code="import sys; import vehicle.common.plate_requests.worker; assert not ({'torch','cv2','numpy','paddle','paddleocr','sqlite3'} & set(sys.modules))"
        result=subprocess.run([sys.executable,'-c',code],capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr.decode())

    def test_regions_reject_stale_whole_or_marker_generation(self):
        for stale_kind in ('whole_ocr','marker'):
            s,_,calls,*_=self.make(scenario='region-left-first')
            state,m=self.first(s)
            for _ in range(2):
                admission=s.admission(m);s.allow_execution(m.body['request_id']);self.assertTrue(s.adapter.drain(1))
                state,_=reduce_event(self.codec,self.codec.decode(admission),state)
                for body in s.collect():state,out=reduce_event(self.codec,self.codec.decode(self.codec.make('evidence',body)),state)
                m=self.codec.decode(out[0][1])
            self.assertIn(m.body['operation'],{'left_ocr','right_ocr'})
            changed=deepcopy(m.body)
            target=next(d for d in changed['dependencies'] if s.evidence[d['operation_id']]['operation']['kind']==stale_kind)
            target['generation']='f'*64
            rejection=self.codec.decode(s.admission(self.codec.decode(self.codec.make('request',changed))))
            self.assertEqual(rejection.body['reason'],'dependency')
            self.assertEqual(calls['left'].calls+calls['right'].calls,0)

    def test_changed_private_binding_after_acceptance_prevents_model_start(self):
        s,crop,calls,*_=self.make();_,request=self.first(s)
        s.admission(request);s.allow_execution(request.body['request_id'])
        crop._binding=replace(crop.binding,handle_id='changed-after-admission')
        self.assertTrue(s.adapter.drain(1))
        body=s.collect()[0]
        self.assertEqual(body['operation']['outcome'],'not_started');self.assertIsNone(body['operation']['model'])
        self.assertEqual(calls['whole'].calls,0);self.assertIn('CROP_BINDING_CONFLICT',s.adapter.blockers)
