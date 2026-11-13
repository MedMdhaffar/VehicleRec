"""Injected execution/resource tests. No real recognition, video or databases."""
from concurrent.futures import Future
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
import json
import subprocess
import sys
import unittest
from unittest.mock import Mock

from contracts.db1.reference import canonical_json_bytes
from vehicle.common.engine_plate import (PlateExecutionAdapter,CropBinding,CropPlan,OperationPlan,
    InjectedExecutor,OCRScalar,decode_ocr,decode_marker,KnownNotStarted,OutcomeUnknown,BASE_RESERVE)
from vehicle.common.engine_plate_injected import fixture,InjectedCrop
from vehicle.common.plate_metadata import PlateMetadata,PlateModel
from vehicle.common.db1_rows.core import Blocked
from vehicle.common.redis_scalar.worker import load_plate_codec
from vehicle.common.redis_scalar.synthetic import ORG,IDENTITY_NAMESPACE
from vehicle.common.redis_scalar.journal import Journal
from vehicle.common.redis_scalar.codec import unpack_attempt,unpack_class_samples
from vehicle.common.colour_metadata import ColourMetadata
from vehicle.common.aggregator import Aggregator,Branch
from vehicle.common.vehicle_frame import VehicleBoxObservation


class ManualScheduler:
    def __init__(self,mode='normal'):self.jobs=[];self.mode=mode
    def submit(self,fn,*args):
        if self.mode=='not_started':raise KnownNotStarted()
        f=Future();self.jobs.append((f,fn,args))
        if self.mode=='lost_reply':raise RuntimeError('unknown submit outcome')
        return f
    def run(self):
        f,fn,args=self.jobs.pop(0)
        if f.set_running_or_notify_cancel():
            try:f.set_result(fn(*args))
            except BaseException as error:f.set_exception(error)


def reference(codec,adapter,closed,colour_batch):
    life=adapter.lifetime;t=adapter.snapshot();plate=PlateMetadata().evaluate(life,t)
    if plate.result is None:return None
    colour=ColourMetadata().finalize(life,tuple(unpack_attempt(v,life) for v in codec.v0.decode(colour_batch).body['measurements']))
    return Aggregator(identity_policy=codec.identity_policy,validation=codec.validation).join_with_plate(
        VehicleBoxObservation(life,(),'unknown',0.),unpack_class_samples(codec.decode(closed).body['classes'],life),(
        Branch('colour','enabled',colour),Branch('plate','enabled',plate.result),Branch('manufacturer','unsupported'))).delivery


class EnginePlateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.codec=load_plate_codec(ORG,IDENTITY_NAMESPACE)

    def make(self,**kwargs):
        values=fixture(self.codec,**kwargs);adapter=values[0]
        def dispose():
            adapter.stop()
            pool=adapter._pool
            if isinstance(pool,ManualScheduler):
                while pool.jobs:pool.run()
            if adapter._active:adapter._active.context.retired() # test-owned fakes have no native/background use
            adapter.poll();adapter.cleanup(discard=True)
        self.addCleanup(dispose)
        return values

    def complete(self,a):
        a.mark_detection_complete();a.seal_admission();self.assertTrue(a.drain())
        return a.snapshot()

    def test_whole_and_regional_plans_counts_models_receipts_and_quality(self):
        for scenario in ('whole','region-left-first','region-right-first'):
            a,crop,plan,calls,closed,colour,batch=self.make(scenario=scenario)
            original=crop.binding.candidate;t=self.complete(a)
            self.assertEqual(t.candidates,(original,));self.assertIsNone(original.quality)
            self.assertEqual(t.attempts[0].attempt_id,plan.attempt_id)
            self.assertEqual(len(t.attempts),1)
            self.assertTrue(all(c.calls==1 for c in calls.values()))
            result=PlateMetadata().evaluate(a.lifetime,t).result
            self.assertEqual(result.outcome,'read')
            selected='whole' if scenario=='whole' else 'right' if scenario.endswith('left-first') else 'left'
            self.assertEqual(result.selected.model,calls[selected].model)
            self.assertEqual(result.selected.receipt,original.receipt)
            if scenario!='whole':
                for name in ('left','right'):
                    deps={d.operation_id:d for d in calls[name].dependencies}
                    self.assertEqual(deps['marker'].model,calls['marker'].model)
                    self.assertEqual(deps['marker'].scalar.bbox,(40,0,60,30))
            publication=a.prepare_publication(self.codec,batch_id=batch,stream_id='1-0')
            self.assertNotIn(crop.binding.handle_id.encode(),publication.payload)
            self.assertNotIn(b'\x00'*32,publication.payload)
            self.assertEqual(self.codec.decode(publication.payload).body['revision'],0)

    def test_no_hidden_followup_or_text_policy_in_engine(self):
        a,crop,plan,calls,*_=self.make()
        calls['whole'].output['raw_text']='123456' # ambiguous; supplied plan has no regions
        t=self.complete(a)
        self.assertEqual(len(t.attempts[0].operations),2)
        self.assertEqual(PlateMetadata().evaluate(a.lifetime,t).result.outcome,'unreadable')
        self.assertEqual(calls['whole'].calls,1)

    def test_invalid_binding_or_dependency_rejected_before_admission_and_invocation(self):
        a,crop,plan,calls,*_=self.make(admit=False)
        changes=[replace(plan,operations=(replace(plan.operations[0],dependencies=('missing',)),)),
                 replace(plan,operations=plan.operations+(plan.operations[0],)),
                 replace(plan,operations=(replace(plan.operations[0],executor_id='marker'),)),
                 replace(plan,candidate_id='wrong')]
        for changed in changes:
            with self.assertRaises(Blocked):a.admit(crop.binding,crop,changed)
            self.assertEqual(a.records(),());self.assertEqual(a.reserved_crop_bytes,0)
        wrong=replace(crop.binding,candidate=replace(crop.binding.candidate,
            identity=replace(crop.binding.candidate.identity,service_track_id=99)))
        with self.assertRaises(Blocked):a.admit(wrong,crop,plan)
        self.assertTrue(all(c.calls==0 for c in calls.values()))

    def test_dependency_failed_marks_followups_not_started(self):
        a,_,_,calls,*_=self.make(scenario='region-right-first')
        calls['whole'].output={'bad':'native response'}
        t=self.complete(a);ops={o.operation_id:o for o in t.attempts[0].operations}
        self.assertEqual(ops['whole'].outcome,'failed');self.assertEqual(ops['whole'].model,calls['whole'].model)
        for name in ('left','right'):
            self.assertEqual(ops[name].outcome,'not_started');self.assertIsNone(ops[name].model)
            self.assertEqual(calls[name].calls,0)
        self.assertEqual(calls['marker'].calls,1)

    def custom(self,invoke,*,scheduler=None):
        base,crop,plan,_,closed,colour,batch=self.make(admit=False)
        base.stop()
        model=PlateModel('synthetic-custom-model',7,'ocr')
        a=PlateExecutionAdapter(base.lifetime,(InjectedExecutor('custom',model,invoke,decode_ocr),),expected_attempt_ids=('custom-attempt',),scheduler=scheduler)
        p=CropPlan('custom-attempt',1,crop.binding.candidate.candidate_id,(OperationPlan('custom-op',1,(),'whole_ocr','custom'),))
        a.admit(crop.binding,crop,p)
        def dispose():
            a.stop()
            if isinstance(a._pool,ManualScheduler):
                while a._pool.jobs:a._pool.run()
            if a._active:a._active.context.retired()
            a.poll();a.cleanup(discard=True)
        self.addCleanup(dispose)
        return a,crop,p,model,batch

    def test_native_success_then_bad_output_keeps_actual_model_failed(self):
        count=[]
        def invoke(req,ctx):
            count.append(1);ctx.started(PlateModel('synthetic-custom-model',7,'ocr'))
            ctx.retired();return b'{"malformed":true}'
        a,_,_,model,_=self.custom(invoke);t=self.complete(a)
        op=t.attempts[0].operations[0]
        self.assertEqual((op.outcome,op.model,op.raw_text),('failed',model,None))
        self.assertEqual(len(count),1)
        self.assertEqual(a.records()[0].error,'OUTPUT_DECODE_FAILED')

    def test_not_started_does_not_borrow_configured_model(self):
        def invoke(req,ctx):raise KnownNotStarted()
        a,_,_,_,_=self.custom(invoke);t=self.complete(a);op=t.attempts[0].operations[0]
        self.assertEqual((op.outcome,op.model),('not_started',None))
        r=PlateMetadata().evaluate(a.lifetime,t).result
        self.assertEqual((r.outcome,r.omission_reason),('omitted','failure_before_model'))

    def test_missing_start_is_unknown_even_with_well_formed_output(self):
        def invoke(req,ctx):ctx.retired();return b'{"raw_text":"123 T 4567","confidence":0.99}'
        a,_,_,_,_=self.custom(invoke);a.mark_detection_complete();a.seal_admission()
        self.assertTrue(a.drain())
        self.assertEqual(a.records()[0].outcome,'unknown');self.assertIsNone(a.records()[0].model)
        self.assertEqual(PlateMetadata().evaluate(a.lifetime,a.snapshot()).state,'waiting')
        a.stop();a.settle_uncertain()
        self.assertEqual(PlateMetadata().evaluate(a.lifetime,a.snapshot()).result.omission_reason,'unsettled_operation')

    def test_timeout_unknown_pins_crop_until_explicit_retirement(self):
        contexts=[]
        def invoke(req,ctx):
            ctx.started(PlateModel('synthetic-custom-model',7,'ocr'));contexts.append(ctx)
            raise OutcomeUnknown()
        a,crop,_,model,batch=self.custom(invoke)
        a.mark_detection_complete();a.seal_admission();self.assertFalse(a.drain(.02))
        a.stop();self.assertFalse(crop.released)
        self.assertEqual(PlateMetadata().evaluate(a.lifetime,a.snapshot()).state,'waiting')
        a.settle_uncertain();p=a.prepare_publication(self.codec,batch_id=batch,stream_id='1-0')
        self.assertEqual(a.snapshot().attempts[0].operations[0].model,model)
        a.publish(Mock(eval=Mock(return_value=b'1-0')),Journal(None,namespace='test',trusted_org=ORG,role='plate',consumer='c'),self.codec,batch_id=batch,stream_id='1-0')
        self.assertEqual(a.cleanup(),('engine-candidate',));self.assertEqual(crop.release_calls,0)
        contexts[0].retired();a.poll();self.assertEqual(a.cleanup(),());self.assertEqual(crop.release_calls,1)
        self.assertEqual(a.prepare_publication(self.codec,batch_id=batch,stream_id='1-0'),p)

    def test_pending_after_closure_does_not_settle_and_stop_is_truthful(self):
        pool=ManualScheduler();a,crop,plan,calls,_,_,_=self.make(scheduler=pool)
        a.mark_detection_complete();a.seal_admission();a.close_lifetime(a.lifetime)
        a.step();a.stop()
        t=a.snapshot()
        self.assertEqual(t.settlement,'open')
        self.assertEqual(PlateMetadata().evaluate(a.lifetime,t).state,'waiting')
        self.assertEqual({o.operation_id:o.outcome for o in t.attempts[0].operations},
                         {'whole':'unknown','marker':'not_started'})
        with self.assertRaises(Blocked):a.cleanup()
        self.assertFalse(crop.released)
        pool.run();a.poll();self.assertEqual(calls['whole'].calls,1);self.assertEqual(calls['marker'].calls,0)

    def test_stop_can_drain_previously_admitted_work_without_new_admission(self):
        a,crop,plan,calls,*_=self.make()
        a.mark_detection_complete();a.seal_admission();a.stop(finish_admitted=True,drain_timeout=1)
        self.assertTrue(all(c.calls==1 for c in calls.values()))
        self.assertEqual(PlateMetadata().evaluate(a.lifetime,a.snapshot()).state,'terminal')
        with self.assertRaises(Blocked):a.admit(crop.binding,crop,replace(plan,attempt_id='new'))

    def test_identical_retry_and_changed_plan_crop_or_model_conflict(self):
        a,crop,plan,calls,_,_,_=self.make();self.complete(a)
        original=a.records()
        self.assertEqual(a.admit(crop.binding,crop,plan),original)
        a.drain();self.assertTrue(all(c.calls==1 for c in calls.values()))
        for changed in (replace(plan,submission_ordinal=9),replace(plan,operations=tuple(reversed(plan.operations)))):
            with self.assertRaises(Blocked):a.admit(crop.binding,crop,changed)
        with self.assertRaises(Blocked):a.admit(replace(crop.binding,handle_id='changed'),crop,plan)
        with self.assertRaises(Blocked):a.admit(crop.binding,InjectedCrop(crop.binding,bytearray(9000)),plan)

    def test_changed_actual_model_is_recorded_and_blocks_publication(self):
        a,_,_,calls,_,_,batch=self.make()
        wrong=replace(calls['whole'].model,version=99);calls['whole'].model=wrong
        a.mark_detection_complete();a.seal_admission();a.drain()
        self.assertEqual(a.records()[1].model if a.records()[1].operation_id=='whole' else a.records()[0].model,wrong)
        with self.assertRaises(Blocked):a.prepare_publication(self.codec,batch_id=batch,stream_id='1-0')
        self.assertIn('EXECUTOR_MODEL_CONFLICT',a.blockers)

    def test_capacity_rejects_without_partial_admission(self):
        for limits in (dict(max_total_crop_bytes=100),dict(max_crop_bytes=100),dict(max_operations=1),dict(max_evidence_bytes=BASE_RESERVE)):
            a,crop,plan,calls,*_=self.make(admit=False,adapter_options=limits)
            with self.assertRaises(Blocked):a.admit(crop.binding,crop,plan)
            self.assertEqual(a.records(),());self.assertEqual(a.reserved_crop_bytes,0)
            self.assertEqual(crop.read_calls,0);self.assertEqual(crop.release_calls,0)
            self.assertTrue(all(c.calls==0 for c in calls.values()))

    def test_publication_failure_retry_and_cleanup_retry_never_rerun(self):
        a,crop,_,calls,_,_,batch=self.make();self.complete(a)
        j=Journal(None,namespace='test',trusted_org=ORG,role='plate',consumer='c')
        client=Mock();client.eval.side_effect=[RuntimeError('publication failure'),b'1-0']
        with self.assertRaises(RuntimeError):a.publish(client,j,self.codec,batch_id=batch,stream_id='1-0')
        self.assertFalse(a.confirmed);self.assertFalse(crop.released)
        first=client.eval.call_args.args
        publication=a.publish(client,j,self.codec,batch_id=batch,stream_id='1-0')
        self.assertEqual(client.eval.call_args.args,first)
        a.publish(client,j,self.codec,batch_id=batch,stream_id='1-0');self.assertEqual(client.eval.call_count,2)
        crop.fail_release=1
        with self.assertRaises(RuntimeError):a.cleanup()
        self.assertEqual(a.cleanup(),());self.assertEqual(crop.release_calls,2)
        self.assertEqual(a.cleanup(),());self.assertEqual(crop.release_calls,2)
        self.assertTrue(all(c.calls==1 for c in calls.values()))
        with self.assertRaises(Blocked):a.prepare_publication(self.codec,batch_id=batch,stream_id='2-0')
        self.assertEqual(a.prepare_publication(self.codec,batch_id=batch,stream_id='1-0'),publication)

    def test_lost_submission_reply_retains_crop_until_job_retires(self):
        pool=ManualScheduler('lost_reply');a,crop,_,calls,_,_,_=self.make(scheduler=pool)
        a.mark_detection_complete();a.seal_admission();a.step();a.stop()
        self.assertFalse(crop.released);self.assertEqual(a.snapshot().settlement,'open')
        pool.run();a.poll()
        self.assertEqual(calls['whole'].calls,1)
        self.assertEqual(a.snapshot().attempts[0].operations[0].outcome,'succeeded')

    def test_known_submission_rejection_has_no_model_or_invocation(self):
        pool=ManualScheduler('not_started');a,_,_,calls,*_=self.make(scheduler=pool)
        t=self.complete(a)
        self.assertTrue(all(o.outcome=='not_started' and o.model is None for o in t.attempts[0].operations))
        self.assertTrue(all(c.calls==0 for c in calls.values()))

    def test_open_lifetime_detection_admission_and_settlement_are_independent(self):
        base,crop,plan,calls,*_=self.make(admit=False)
        executors=tuple(base._executors.values())
        open_life=replace(base.lifetime,closure=None)
        a=PlateExecutionAdapter(open_life,executors,expected_attempt_ids=(plan.attempt_id,));self.addCleanup(a.stop)
        a.admit(crop.binding,crop,plan);self.assertTrue(a.drain())
        self.assertIsNone(a.lifetime.closure)
        a.close_lifetime(base.lifetime)
        self.assertEqual(PlateMetadata().evaluate(a.lifetime,a.snapshot()).state,'waiting')
        a.mark_detection_complete()
        self.assertEqual(PlateMetadata().evaluate(a.lifetime,a.snapshot()).state,'waiting')
        a.seal_admission()
        self.assertEqual(PlateMetadata().evaluate(a.lifetime,a.snapshot()).state,'terminal')
        a.stop();a.cleanup(discard=True)

    def test_direct_natural_bundle_parity_uses_adapter_transcript(self):
        from vehicle.common.redis_scalar.reducer import reduce_message
        for scenario in ('whole','region-left-first','region-right-first'):
            a,_,_,_,closed,colour,batch=self.make(scenario=scenario);self.complete(a)
            expected=reference(self.codec,a,closed,colour)
            packet=a.prepare_publication(self.codec,batch_id=batch,stream_id='1-0')
            _,po=reduce_message(self.codec,'plate',self.codec.decode(packet.payload),None)
            _,co=reduce_message(self.codec.v0,'colour',self.codec.v0.decode(colour),None)
            state=None
            for raw in (po[0][1],closed,co[0][1]):state,out=reduce_message(self.codec,'aggregator_plate',self.codec.decode(raw),state)
            m=self.codec.decode(out[0][1])
            self.assertEqual(canonical_json_bytes(m.body['bundle']),expected.payload.canonical)
            self.assertEqual(m.body['bundle_sha256'],expected.digest)

    def test_fresh_imports_do_not_load_models_database_or_redis_client(self):
        code='''
import sys,importlib.abc
class Guard(importlib.abc.MetaPathFinder):
 def find_spec(self,name,path=None,target=None):
  if name.split('.')[0] in {'cv2','numpy','torch','ultralytics','paddle','paddleocr','sqlite3','psycopg','redis','openai','requests','httpx'}:raise AssertionError(name)
sys.meta_path.insert(0,Guard())
from vehicle.common.engine_plate import PlateExecutionAdapter
from vehicle.common.engine_plate_injected import fixture
'''
        p=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)

    def test_rejected_observations_preserve_detection_and_receipts_without_execution(self):
        base,crop,_,calls,*_=self.make(admit=False)
        a=PlateExecutionAdapter(base.lifetime,(),expected_attempt_ids=())
        self.addCleanup(a.stop)
        c=replace(crop.binding.candidate,crop_accepted=False,association='ambiguous')
        a.observe_candidate(c);a.mark_detection_complete();a.seal_admission()
        t=a.snapshot()
        self.assertEqual(t.candidates,(c,));self.assertEqual(t.attempts,())
        result=PlateMetadata().evaluate(a.lifetime,t).result
        self.assertEqual(result.omission_reason,'ambiguous_association')
        self.assertTrue(all(call.calls==0 for call in calls.values()))
        a.stop();self.assertEqual(a.cleanup(discard=True),())

    def test_unadmitted_expected_work_cannot_be_sealed_or_disappear(self):
        a,crop,plan,calls,*_=self.make(admit=False,adapter_options=dict(max_operations=1))
        with self.assertRaises(Blocked):a.admit(crop.binding,crop,plan)
        a.mark_detection_complete()
        with self.assertRaises(Blocked):a.seal_admission()
        self.assertEqual(a.snapshot().expected_attempt_ids,(plan.attempt_id,))
        self.assertEqual(PlateMetadata().evaluate(a.lifetime,a.snapshot()).state,'waiting')
        self.assertTrue(all(call.calls==0 for call in calls.values()))


    def test_changed_binding_and_matching_pixels_after_admission_block_execution(self):
        a,crop,_,calls,_,_,batch=self.make()
        admitted=crop.binding
        pixels=bytes([7])*admitted.size_bytes
        crop._binding=replace(admitted,candidate=replace(admitted.candidate,crop_sha256=sha256(pixels).hexdigest()))
        crop._pixels=pixels
        self.complete(a)
        self.assertTrue(all(c.calls==0 for c in calls.values()))
        self.assertIn('CROP_BINDING_CONFLICT',a.blockers)
        with self.assertRaises(Blocked):a.prepare_publication(self.codec,batch_id=batch,stream_id='1-0')
        self.assertFalse(a.confirmed);self.assertFalse(crop.released)

    def test_binding_changed_during_read_blocks_execution_and_publication(self):
        for change_pixels in (False,True):
            with self.subTest(change_pixels=change_pixels):
                a,crop,_,calls,_,_,batch=self.make()
                admitted=crop.binding;read=crop.read
                def changed_read():
                    pixels=bytes([8])*admitted.size_bytes if change_pixels else crop._pixels
                    crop._binding=replace(admitted,handle_id='replacement-handle',candidate=replace(
                        admitted.candidate,crop_sha256=sha256(pixels).hexdigest()))
                    crop._pixels=pixels
                    return read()
                crop.read=changed_read
                self.complete(a)
                self.assertTrue(all(c.calls==0 for c in calls.values()))
                self.assertIn('CROP_BINDING_CONFLICT',a.blockers)
                with self.assertRaises(Blocked):a.prepare_publication(self.codec,batch_id=batch,stream_id='1-0')

    def test_native_request_uses_immutable_admitted_candidate(self):
        a,crop,_,calls,*_=self.make()
        admitted=crop.binding
        # An equal fresh object is allowed, but never becomes the authority.
        crop._binding=replace(admitted,candidate=replace(admitted.candidate))
        observed=[]
        for executor in a._executors.values():
            invoke=executor.invoke
            def record(request,context,invoke=invoke):
                observed.append(request.candidate)
                return invoke(request,context)
            a._executors[executor.executor_id]=replace(executor,invoke=record)
        self.complete(a)
        self.assertEqual(len(observed),2)
        self.assertTrue(all(c is admitted.candidate for c in observed))
        self.assertTrue(all(c.calls==1 for c in calls.values()))

    def test_wrong_org_journal_rejected_before_eval_then_correct_retry(self):
        from uuid import UUID
        a,crop,_,calls,_,_,batch=self.make();self.complete(a)
        publication=a.prepare_publication(self.codec,batch_id=batch,stream_id='1-0')
        client=Mock(eval=Mock(return_value=b'1-0'))
        wrong=Journal(None,namespace='test',trusted_org=UUID(int=99),role='plate',consumer='c')
        with self.assertRaises(Blocked):a.publish(client,wrong,self.codec,batch_id=batch,stream_id='1-0')
        client.eval.assert_not_called();self.assertFalse(a.confirmed)
        with self.assertRaises(Blocked):a.cleanup()
        self.assertFalse(crop.released)
        correct=Journal(None,namespace='test',trusted_org=ORG,role='plate',consumer='c')
        retried=a.publish(client,correct,self.codec,batch_id=batch,stream_id='1-0')
        self.assertEqual(retried,publication);self.assertTrue(a.confirmed)
        self.assertEqual(client.eval.call_count,1)
        self.assertEqual(client.eval.call_args.args[-2],publication.payload)
        self.assertTrue(all(c.calls==1 for c in calls.values()))
        a.cleanup();self.assertEqual(crop.release_calls,1)

    def test_closure_cannot_rewrite_recorded_last_frame_receipt(self):
        base,_,plan,*_=self.make(admit=False)
        opened=replace(base.lifetime,closure=None)
        for field,value in [('utc',opened.last_receipt.utc+timedelta(seconds=.5)),
                            ('monotonic',opened.last_receipt.monotonic+.5),
                            ('media_pts',opened.last_receipt.media_pts+.01)]:
            with self.subTest(field=field):
                a=PlateExecutionAdapter(opened,(),expected_attempt_ids=())
                self.addCleanup(a.stop)
                changed=replace(base.lifetime,last_receipt=replace(opened.last_receipt,**{field:value}))
                with self.assertRaises(Blocked) as error:a.close_lifetime(changed)
                self.assertEqual(error.exception.code,'FRAME_RECEIPT_MISMATCH')
                self.assertIs(a.lifetime,opened)
                a.close_lifetime(base.lifetime);self.assertEqual(a.lifetime,base.lifetime)
                a.close_lifetime(base.lifetime);self.assertEqual(a.lifetime,base.lifetime)

    def test_closure_can_extend_last_frame_and_retry_identically(self):
        base,*_=self.make(admit=False)
        opened=replace(base.lifetime,closure=None)
        a=PlateExecutionAdapter(opened,(),expected_attempt_ids=());self.addCleanup(a.stop)
        previous=opened.last_receipt
        extended=replace(base.lifetime,last_receipt=replace(previous,frame_index=previous.frame_index+1,
            utc=previous.utc+timedelta(seconds=1),monotonic=previous.monotonic+1,media_pts=previous.media_pts+.04),
            frames_observed=opened.frames_observed+1)
        a.close_lifetime(extended);a.close_lifetime(extended)
        self.assertIs(a.lifetime,extended)
        with self.assertRaises(Blocked):a.close_lifetime(base.lifetime)
        self.assertIs(a.lifetime,extended)

    def test_changed_private_allocation_conflicts_before_any_invocation(self):
        a,crop,_,calls,_,_,batch=self.make()
        crop._pixels=bytes([1])*crop.binding.size_bytes
        a.mark_detection_complete();a.seal_admission();a.drain()
        self.assertTrue(all(c.calls==0 for c in calls.values()))
        self.assertTrue(all(r.model is None for r in a.records()))
        self.assertIn('CROP_BINDING_CONFLICT',a.blockers)
        with self.assertRaises(Blocked):a.prepare_publication(self.codec,batch_id=batch,stream_id='1-0')

    def test_cleanup_skips_successful_releases_across_multiple_handles(self):
        base,crop,plan,calls,_,_,batch=self.make(admit=False)
        candidate=replace(crop.binding.candidate,candidate_id='second',segment_id=2,raw_plate_id=12,crop_ref='second-opaque-ref')
        binding=CropBinding(candidate,'second-private-handle',crop.binding.size_bytes)
        second=InjectedCrop(binding,bytearray(binding.size_bytes));second.fail_release=1
        operations=tuple(replace(o,operation_id=o.operation_id+'-2') for o in plan.operations)
        p2=replace(plan,attempt_id='second-attempt',submission_ordinal=8,candidate_id='second',operations=operations)
        a=PlateExecutionAdapter(base.lifetime,tuple(base._executors.values()),expected_attempt_ids=(plan.attempt_id,p2.attempt_id))
        self.addCleanup(a.stop)
        a.admit(crop.binding,crop,plan);a.admit(binding,second,p2);self.complete(a)
        a.publish(Mock(eval=Mock(return_value=b'1-0')),Journal(None,namespace='test',trusted_org=ORG,role='plate',consumer='c'),self.codec,batch_id=batch,stream_id='1-0')
        with self.assertRaises(RuntimeError):a.cleanup()
        self.assertEqual((crop.release_calls,second.release_calls),(1,1))
        self.assertEqual(a.cleanup(),());self.assertEqual((crop.release_calls,second.release_calls),(1,2))
        self.assertEqual(a.cleanup(),());self.assertEqual((crop.release_calls,second.release_calls),(1,2))
        self.assertTrue(all(c.calls==2 for c in calls.values()))

    def test_late_known_success_cannot_rewrite_frozen_uncertainty_and_cannot_release_early(self):
        contexts=[]
        def invoke(req,ctx):
            ctx.started(PlateModel('synthetic-custom-model',7,'ocr'));contexts.append(ctx)
            return b'{"raw_text":"123 T 4567","confidence":0.96}'
        a,crop,_,_,batch=self.custom(invoke)
        a.mark_detection_complete();a.seal_admission();self.assertFalse(a.drain(.02))
        self.assertEqual(a.records()[0].outcome,'succeeded')
        a.stop();a.settle_uncertain()
        p=a.prepare_publication(self.codec,batch_id=batch,stream_id='1-0')
        self.assertEqual(a.snapshot().attempts[0].operations[0].outcome,'unknown')
        a.publish(Mock(eval=Mock(return_value=b'1-0')),Journal(None,namespace='test',trusted_org=ORG,role='plate',consumer='c'),self.codec,batch_id=batch,stream_id='1-0')
        self.assertTrue(a.cleanup());self.assertFalse(crop.released)
        contexts[0].retired();a.poll();a.cleanup()
        self.assertTrue(crop.released)
        self.assertEqual(a.prepare_publication(self.codec,batch_id=batch,stream_id='1-0'),p)
        self.assertEqual(a.late_records()[0].outcome,'succeeded')

    def test_generic_invocation_failure_before_and_after_start_stays_distinct(self):
        for started in (False,True):
            def invoke(req,ctx):
                try:
                    if started:ctx.started(PlateModel('synthetic-custom-model',7,'ocr'))
                    raise RuntimeError('injected native failure')
                finally:ctx.retired()
            a,_,_,model,_=self.custom(invoke);a.mark_detection_complete();a.seal_admission();self.assertTrue(a.drain())
            record=a.records()[0]
            self.assertEqual(record.outcome,'failed' if started else 'unknown')
            self.assertEqual(record.model,model if started else None)

    def test_unexpected_publication_reply_is_not_success_and_does_not_release(self):
        a,crop,_,calls,_,_,batch=self.make();self.complete(a)
        client=Mock();client.eval.return_value=None
        j=Journal(None,namespace='test',trusted_org=ORG,role='plate',consumer='c')
        with self.assertRaises(Blocked):a.publish(client,j,self.codec,batch_id=batch,stream_id='1-0')
        self.assertFalse(a.confirmed)
        with self.assertRaises(Blocked):a.cleanup()
        self.assertFalse(crop.released)
        client.eval.return_value=b'1-0';a.publish(client,j,self.codec,batch_id=batch,stream_id='1-0')
        self.assertTrue(a.confirmed);self.assertTrue(all(c.calls==1 for c in calls.values()))

    def test_exact_dependency_generation_reaches_regional_executor(self):
        from vehicle.common.engine_plate_injected import InjectedCall
        base,crop,plan,calls,*_=self.make(admit=False,scenario='region-left-first')
        model=PlateModel('synthetic-second-marker',17,'detect')
        call=InjectedCall(model,dict(detected=True,confidence=.99,bbox=[20,0,30,30],center_x=25.))
        extra=InjectedExecutor('marker-new',model,call,decode_marker)
        a=PlateExecutionAdapter(base.lifetime,tuple(base._executors.values())+(extra,),expected_attempt_ids=(plan.attempt_id,))
        self.addCleanup(a.stop)
        operations=plan.operations[:2]+(OperationPlan('marker-new',3,(),'marker','marker-new'),)+tuple(replace(o,sequence=o.sequence+1) for o in plan.operations[2:])
        a.admit(crop.binding,crop,replace(plan,operations=operations));self.complete(a)
        for side in ('left','right'):
            dependencies={d.operation_id:d for d in calls[side].dependencies}
            self.assertEqual(dependencies['marker'].scalar.bbox,(40,0,60,30))
            self.assertEqual(dependencies['marker'].model,calls['marker'].model)
            self.assertNotEqual(dependencies['marker'].model,model)
        self.assertEqual(call.calls,1)
        a.stop();a.cleanup(discard=True)

    def test_serial_slot_waits_for_retirement_after_invocation_returns(self):
        base,crop,_,_,*_=self.make(admit=False)
        model=PlateModel('synthetic-serial-executor',3,'ocr');contexts=[];invoked=[]
        def invoke(req,ctx):
            invoked.append(req.plan.operation_id);ctx.started(model);contexts.append(ctx)
            if len(invoked)==2:ctx.retired()
            return b'{"raw_text":"12 T 3456","confidence":0.96}'
        executor=InjectedExecutor('serial',model,invoke,decode_ocr)
        plan=CropPlan('serial-attempt',1,crop.binding.candidate.candidate_id,(
            OperationPlan('one',1,(),'whole_ocr','serial'),OperationPlan('two',2,(),'whole_ocr','serial')))
        a=PlateExecutionAdapter(base.lifetime,(executor,),expected_attempt_ids=(plan.attempt_id,))
        self.addCleanup(a.stop);a.admit(crop.binding,crop,plan);a.mark_detection_complete();a.seal_admission()
        self.assertFalse(a.drain(.02));self.assertEqual(invoked,['one'])
        for _ in range(3):self.assertFalse(a.step())
        self.assertEqual(invoked,['one']);self.assertFalse(crop.released)
        contexts[0].retired();self.assertTrue(a.drain());self.assertEqual(invoked,['one','two'])
        a.stop();a.cleanup(discard=True)

    def test_false_scheduler_not_started_cannot_erase_observed_execution(self):
        class InconsistentScheduler:
            def submit(self,fn,*args):fn(*args);raise KnownNotStarted()
        def invoke(req,ctx):
            ctx.started(PlateModel('synthetic-custom-model',7,'ocr'));ctx.retired()
            return b'{"raw_text":"12 T 3456","confidence":0.96}'
        a,crop,_,model,batch=self.custom(invoke,scheduler=InconsistentScheduler())
        a.mark_detection_complete();a.seal_admission();a.step();a.poll()
        self.assertEqual(a.records()[0].outcome,'succeeded');self.assertEqual(a.records()[0].model,model)
        self.assertIn('SUBMISSION_CLAIM_CONFLICT',a.blockers)
        with self.assertRaises(Blocked):a.prepare_publication(self.codec,batch_id=batch,stream_id='1-0')

    def test_process_wide_native_lane_is_pinned_until_retirement(self):
        contexts=[]
        def first(req,ctx):
            ctx.started(PlateModel('synthetic-custom-model',7,'ocr'));contexts.append(ctx)
            return b'{"raw_text":"123 T 4567","confidence":0.96}'
        a,_,_,_,_=self.custom(first)
        a.mark_detection_complete();a.seal_admission();self.assertFalse(a.drain(.02))
        b,_,_,calls,*_=self.make()
        b.mark_detection_complete();b.seal_admission();self.assertFalse(b.drain(.02))
        self.assertTrue(all(c.calls==0 for c in calls.values()))
        contexts[0].retired()
        self.assertTrue(b.drain());self.assertTrue(all(c.calls==1 for c in calls.values()))
