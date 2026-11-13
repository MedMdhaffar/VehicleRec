"""Injected native-runtime binding tests. No actual model initialization/execution."""
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from threading import Event
import tempfile
import unittest
from unittest.mock import Mock
import numpy as np

from vehicle.common.engine_plate import PlateExecutionAdapter,CropBinding,OutcomeUnknown
from vehicle.common.engine_plate_artifacts import ArtifactFile,LocalArtifact
from vehicle.common.engine_plate_local import LocalPlateBinding,raw_ocr,NativeCPU
from vehicle.common.engine_plate_injected import fixture
from vehicle.common.tests.test_engine_plate import ManualScheduler
from vehicle.common.redis_scalar.worker import load_plate_codec
from vehicle.common.redis_scalar.synthetic import ORG,IDENTITY_NAMESPACE
from vehicle.common.redis_scalar.journal import Journal
from vehicle.common.db1_rows.core import Blocked


class Boxes:
    def __init__(self,coords=((204.8,0,307.2,512),),conf=(.95,)):
        self.xyxy=np.array(coords).reshape(-1,4);self.conf=np.array(conf)
    def __len__(self):return len(self.conf)


class Runtime:
    def __init__(self,identity):
        self.identity=identity;self.images=[];self.init_calls=0;self.close_calls=0;self.error=None;self.bad=False
        self.wait=None;self.mutate=False;self.init_error=False
    def initialize(self):
        self.init_calls+=1
        if self.init_error:raise RuntimeError('injected init failure')
    def prepare(self,image):return image
    def predict(self,image):
        self.images.append(image.copy())
        if self.wait:self.wait.wait()
        if self.error:raise self.error
        if self.mutate:image[:]=255
        if self.bad:return [dict(unexpected='native format')]
        if self.identity.task=='detect':return [SimpleNamespace(boxes=Boxes())]
        return [dict(rec_text='123 تونس 4567',rec_score=.96)]
    def close(self):self.close_calls+=1


class LocalExecutorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.codec=load_plate_codec(ORG,IDENTITY_NAMESPACE)
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'injected-runtime.weights';self.path.write_bytes(b'explicit injected native runtime fixture')

    def identity(self,task):
        return LocalArtifact('injected-native-runtime',task,(ArtifactFile.capture('fixture',self.path),),
                             (('injected-test-runtime','1'),),(('regions','original-full-height-outside-marker-int-box-v1'),))

    def make(self,scenario='whole',*,scheduler=None):
        base,crop,plan,_,closed,colour,batch=fixture(self.codec,scenario=scenario,admit=False)
        base.stop()
        bindings={};runtimes={}
        for name,task in [('ocr','ocr'),('marker','detect')]:
            identity=self.identity(task);runtime=Runtime(identity);runtimes[name]=runtime
            bindings[name]=LocalPlateBinding(identity,runtime_factory=lambda ident,runtime=runtime:runtime)
        executors=tuple(bindings['marker' if o.kind=='marker' else 'ocr'].executor(o.executor_id,o.kind) for o in plan.operations)
        a=PlateExecutionAdapter(base.lifetime,executors,expected_attempt_ids=(plan.attempt_id,),scheduler=scheduler)
        plan=replace(plan,source='fixed-local-smoke-plan-v1');a.admit(crop.binding,crop,plan)
        a.mark_detection_complete();a.seal_admission()
        def dispose():
            for r in runtimes.values():
                if r.wait:r.wait.set()
            a.stop(drain_timeout=2,finish_admitted=True)
            if a._active:
                # Test runtimes have no native background use after return.
                a._active.context.retired()
                for b in bindings.values():b.active=False
            a.poll();a.cleanup(discard=True)
            for b in bindings.values():b.close()
        self.addCleanup(dispose)
        return a,crop,bindings,runtimes,batch

    def test_artifact_identity_changes_and_wrong_file_rejected_before_initialization(self):
        a,crop,bindings,rt,batch=self.make()
        old=bindings['ocr'].identity.model
        self.path.write_bytes(b'changed fixture bytes')
        self.assertNotEqual(old,self.identity('ocr').model)
        self.assertTrue(a.drain())
        self.assertTrue(all(r.init_calls==0 and not r.images for r in rt.values()))
        self.assertIn('ARTIFACT_BINDING_CONFLICT',a.blockers)
        with self.assertRaises(Blocked):a.prepare_publication(self.codec,batch_id=batch,stream_id='1-0')
        self.assertFalse(crop.released)

    def test_wrong_loaded_runtime_identity_is_rejected_without_invocation(self):
        a,_,bindings,rt,batch=self.make()
        rt['ocr'].identity=replace(rt['ocr'].identity,versions=(('injected-test-runtime','2'),))
        a.drain();self.assertFalse(rt['ocr'].images)
        self.assertIn('ARTIFACT_BINDING_CONFLICT',a.blockers)
        with self.assertRaises(Blocked):a.prepare_publication(self.codec,batch_id=batch,stream_id='1-0')

    def test_bgr_shape_bytes_hash_and_native_mutation_does_not_change_admitted_input(self):
        a,crop,_,rt,_=self.make();original=crop.read();rt['ocr'].mutate=True
        self.assertTrue(a.drain());self.assertEqual(rt['ocr'].images[0].shape,(30,100,3))
        self.assertEqual(crop.read(),original);self.assertEqual(sha256(original).hexdigest(),crop.binding.candidate.crop_sha256)

    def test_installed_ocr_response_decoding_keeps_raw_text_without_policy(self):
        value=raw_ocr([dict(res=dict(rec_text=' ١٢٣ تونس ABC ',rec_score=np.float32(.8)))])
        self.assertEqual(value['raw_text'],' ١٢٣ تونس ABC ')
        for bad in ([],[{}],[dict(rec_text='x',rec_score=float('nan'))],[dict(rec_text='x',rec_score=True)],[{},{}]):
            with self.assertRaises((Blocked,ValueError)):raw_ocr(bad)

    def test_marker_coordinates_and_both_dependency_bound_region_orders(self):
        for scenario in ('region-left-first','region-right-first'):
            with self.subTest(scenario=scenario):
                a,crop,b,rt,_=self.make(scenario)
                self.assertTrue(a.drain())
                marker=[r.scalar for r in a.records() if r.operation_id=='marker'][0]
                self.assertEqual(marker.bbox,(40,0,60,30))
                lineage=b['ocr'].lineage
                self.assertEqual([row['kind'] for row in lineage],['whole_ocr',*(['left_ocr','right_ocr'] if scenario.endswith('left-first') else ['right_ocr','left_ocr'])])
                for row in lineage[1:]:
                    self.assertEqual(set(row['dependency_ids']),{'whole','marker'})
                    self.assertEqual(row['input_box'],(0,0,40,30) if row['kind']=='left_ocr' else (60,0,100,30))
                    self.assertEqual(row['original_sha256'],crop.binding.candidate.crop_sha256)
                self.assertEqual(rt['ocr'].init_calls,1);self.assertEqual(len(rt['ocr'].images),3)

    def test_ineligible_marker_skips_regions_without_ocr_execution(self):
        a,_,b,rt,_=self.make('region-right-first')
        original=rt['marker'].predict
        rt['marker'].predict=lambda image:[SimpleNamespace(boxes=Boxes((),()))]
        a.drain();records={r.operation_id:r for r in a.records()}
        for name in ('left','right'):
            self.assertEqual((records[name].outcome,records[name].model,records[name].error),('not_started',None,'INELIGIBLE_GEOMETRY'))
        self.assertEqual(b['ocr'].calls,{'whole_ocr':1})

    def test_initialization_failure_has_no_execution_or_model(self):
        a,_,b,rt,_=self.make();rt['ocr'].init_error=True
        a.drain();record=[r for r in a.records() if r.operation_id=='whole'][0]
        self.assertEqual((record.outcome,record.model,record.error),('not_started',None,'INITIALIZATION_FAILED'))
        self.assertEqual(rt['ocr'].close_calls,1);self.assertFalse(rt['ocr'].images)

    def test_invocation_and_decode_failure_preserve_actual_model(self):
        for bad in (False,True):
            with self.subTest(bad=bad):
                a,_,b,rt,_=self.make()
                if bad:rt['ocr'].bad=True
                else:rt['ocr'].error=RuntimeError('injected native failure')
                a.drain();record=[r for r in a.records() if r.operation_id=='whole'][0]
                self.assertEqual(record.outcome,'failed');self.assertEqual(record.model,b['ocr'].identity.model)
                self.assertIsNone(record.scalar)
                self.assertEqual(record.error,'OUTPUT_DECODE_FAILED' if bad else 'INVOCATION_FAILED')

    def test_timeout_does_not_retire_or_release_and_native_completion_does(self):
        a,crop,b,rt,_=self.make();rt['ocr'].wait=Event()
        self.assertFalse(a.drain(.05));self.assertFalse(crop.released)
        with self.assertRaises(Blocked):b['ocr'].close()
        rt['ocr'].wait.set();self.assertTrue(a.drain(3))
        a.stop();a.cleanup(discard=True);self.assertTrue(crop.released)

    def test_unknown_native_outcome_requires_explicit_retirement(self):
        a,crop,b,rt,_=self.make();rt['ocr'].error=OutcomeUnknown()
        self.assertFalse(a.drain(.1));self.assertFalse(a._active.context.retirement.is_set())
        self.assertFalse(crop.released)
        with self.assertRaises(Blocked):b['ocr'].close()
        a._active.context.retired();b['ocr'].active=False
        self.assertTrue(a.drain(3))

    def test_publication_retry_does_not_repeat_local_calls_and_close_is_idempotent(self):
        a,crop,b,rt,batch=self.make();self.assertTrue(a.drain())
        j=Journal(None,namespace='local-test',trusted_org=ORG,role='plate',consumer='test')
        client=Mock();client.eval.side_effect=[RuntimeError('injected connection failure'),b'1-0']
        with self.assertRaises(RuntimeError):a.publish(client,j,self.codec,batch_id=batch,stream_id='1-0')
        self.assertFalse(crop.released);a.publish(client,j,self.codec,batch_id=batch,stream_id='1-0')
        a.cleanup();a.cleanup()
        for value in b.values():value.close();value.close()
        self.assertEqual(crop.release_calls,1);self.assertEqual(len(rt['ocr'].images),1)
        self.assertTrue(all(r.close_calls==1 for r in rt.values()))

    def test_native_marker_preprocess_is_512_stretch_with_explicit_cpu(self):
        runtime=NativeCPU(self.identity('detect'));runtime.model=Mock(return_value=[])
        runtime.predict(runtime.prepare(np.zeros((30,100,3),dtype=np.uint8)))
        args=runtime.model.predict.call_args
        self.assertEqual(args.args[0][0].shape,(512,512,3))
        self.assertEqual(args.kwargs,dict(conf=.05,imgsz=512,device='cpu',verbose=False,stream=False))

    def test_model_close_failure_retains_resource_for_retry(self):
        a,_,b,rt,_=self.make();self.assertTrue(a.drain())
        close=rt['ocr'].close;rt['ocr'].close=Mock(side_effect=[RuntimeError('injected cleanup failure'),None])
        with self.assertRaises(RuntimeError):b['ocr'].close()
        self.assertFalse(b['ocr'].closed);self.assertTrue(b['ocr'].cleanup_pending)
        b['ocr'].close();b['ocr'].close();self.assertTrue(b['ocr'].closed)
        self.assertFalse(b['ocr'].cleanup_pending)
        self.assertEqual(rt['ocr'].close.call_count,2)

    def test_crop_host_reference_uses_same_single_receipt_and_unresolved_context(self):
        import cv2
        from vehicle.common.plate_crop_smoke import evidence,reference,require_real_calls
        base,_,_,_,_,_,_=fixture(self.codec,admit=False);base.stop()
        path=Path(self.tmp.name)/'injected-input.jpg'
        ok,encoded=cv2.imencode('.jpg',np.zeros((30,100,3),dtype=np.uint8));self.assertTrue(ok)
        path.write_bytes(encoded.tobytes())
        context=replace(base.lifetime.context,resolution=replace(base.lifetime.context.resolution,synthetic=True))
        life,crop,measurements,classes,receipt=evidence(self.codec,context,path,1)
        with self.assertRaises(Blocked):require_real_calls(dict(receipts=[{}],invocations={'ocr':{},'detect':{'marker':1}}))
        require_real_calls(dict(receipts=[{}],invocations={'ocr':{'whole_ocr':1},'detect':{'marker':1}}))
        with self.assertRaises(Blocked):replace(self.identity('ocr'),versions=(['mutable-runtime','1'],))
        self.assertEqual(life.first_receipt,life.last_receipt)
        self.assertEqual(classes[0].receipt,life.first_receipt)
        self.assertEqual(measurements[0].receipt,life.first_receipt)
        a,old,b,rt,batch=self.make()
        a.stop();a.cleanup(discard=True)
        from vehicle.common.engine_plate import OperationPlan,CropPlan
        ops=(OperationPlan('whole',1,(),'whole_ocr','whole'),OperationPlan('marker',2,(),'marker','marker'))
        plan=CropPlan('crop-host-attempt',1,crop.binding.candidate.candidate_id,ops,'fixed-local-smoke-plan-v1')
        adapter=PlateExecutionAdapter(life,tuple(b['marker' if op.kind=='marker' else 'ocr'].executor(op.executor_id,op.kind) for op in ops),expected_attempt_ids=(plan.attempt_id,))
        try:
            adapter.admit(crop.binding,crop,plan);adapter.mark_detection_complete();adapter.seal_admission()
            self.assertTrue(adapter.drain())
            bundle=reference(self.codec,life,measurements,classes,adapter.snapshot())
            self.assertIsNotNone(bundle);self.assertEqual(bundle.digest,sha256(bundle.payload.canonical).hexdigest())
        finally:adapter.stop();adapter.cleanup(discard=True)

    def test_local_stage_mismatch_is_rejected_before_any_admission(self):
        kinds=('whole_ocr','left_ocr','right_ocr')
        for target in kinds:
            for wrong in kinds:
                if target==wrong:continue
                with self.subTest(target=target,wrong=wrong):
                    base,crop,plan,_,*_=fixture(self.codec,scenario='region-left-first',admit=False);base.stop()
                    identity=self.identity('ocr');runtime=Runtime(identity)
                    binding=LocalPlateBinding(identity,runtime_factory=lambda ident:runtime)
                    marker=LocalPlateBinding(self.identity('detect'),runtime_factory=Runtime)
                    executors=tuple((marker if op.kind=='marker' else binding).executor(
                        op.executor_id,wrong if op.kind==target else op.kind) for op in plan.operations)
                    adapter=PlateExecutionAdapter(base.lifetime,executors,expected_attempt_ids=(plan.attempt_id,))
                    try:
                        with self.assertRaises(Blocked) as error:adapter.admit(crop.binding,crop,plan)
                        self.assertEqual(error.exception.code,'EXECUTOR_STAGE_CONFLICT')
                        self.assertEqual(adapter.records(),());self.assertEqual(adapter.reserved_crop_bytes,0)
                        self.assertFalse(crop.released);self.assertEqual(runtime.init_calls,0);self.assertFalse(runtime.images)
                    finally:
                        adapter.stop();adapter.cleanup(discard=True);binding.close();marker.close()

    def test_local_executor_records_kind_and_correct_stages_share_one_runtime(self):
        a,_,bindings,rt,_=self.make('region-right-first')
        for operation in a._ops.values():
            plan=operation[2];self.assertEqual(a._executors[plan.executor_id].kind,plan.kind)
        self.assertTrue(a.drain())
        self.assertEqual(rt['ocr'].init_calls,1);self.assertEqual(len(rt['ocr'].images),3)
        self.assertEqual(bindings['ocr'].calls,{'whole_ocr':1,'right_ocr':1,'left_ocr':1})

    def test_direct_mismatched_request_is_not_executed(self):
        from vehicle.common.engine_plate import ExecutionContext,NativeRequest,OperationPlan
        from vehicle.common.engine_plate_local import LocalNotStarted
        base,crop,_,_,*_=fixture(self.codec,admit=False);base.stop()
        identity=self.identity('ocr');runtime=Runtime(identity)
        binding=LocalPlateBinding(identity,runtime_factory=lambda ident:runtime);self.addCleanup(binding.close)
        executor=binding.executor('whole','whole_ocr');context=ExecutionContext(executor)
        request=NativeRequest(memoryview(crop.read()),crop.binding.candidate,
            OperationPlan('left',3,(),'left_ocr','whole'),())
        with self.assertRaises(LocalNotStarted) as error:executor.invoke(request,context)
        self.assertEqual(error.exception.code,'EXECUTOR_STAGE_CONFLICT')
        self.assertIsNone(context.model);self.assertTrue(context.retirement.is_set())
        self.assertEqual(runtime.init_calls,0);self.assertFalse(runtime.images)

    def test_changed_executor_metadata_cannot_disguise_whole_as_region(self):
        from vehicle.common.engine_plate import ExecutionContext,NativeRequest,OperationPlan
        from vehicle.common.engine_plate_local import LocalNotStarted
        base,crop,_,_,*_=fixture(self.codec,admit=False);base.stop()
        binding=LocalPlateBinding(self.identity('ocr'),runtime_factory=Runtime);self.addCleanup(binding.close)
        executor=replace(binding.executor('whole','whole_ocr'),kind='left_ocr')
        context=ExecutionContext(executor)
        request=NativeRequest(memoryview(crop.read()),crop.binding.candidate,
            OperationPlan('left',3,(),'left_ocr','whole'),())
        with self.assertRaises(LocalNotStarted):executor.invoke(request,context)
        self.assertIsNone(context.model);self.assertFalse(binding.calls)
        # Even forged descriptor metadata cannot turn the captured whole callback
        # into regional evidence if admission has already accepted its descriptor.
        a,_,bindings,rt,batch=self.make('region-left-first')
        a._executors['left']=replace(bindings['ocr'].executor('left','whole_ocr'),kind='left_ocr')
        self.assertTrue(a.drain())
        self.assertEqual(len(rt['ocr'].images),2) # whole and right only
        self.assertIn('EXECUTOR_STAGE_CONFLICT',a.blockers)
        with self.assertRaises(Blocked):a.prepare_publication(self.codec,batch_id=batch,stream_id='1-0')

    def test_failed_initialization_cleanup_retains_runtime_and_blocks_reinitialization(self):
        from vehicle.common.engine_plate import ExecutionContext,NativeRequest,OperationPlan
        from vehicle.common.engine_plate_local import LocalNotStarted
        base,crop,_,_,*_=fixture(self.codec,admit=False);base.stop()
        identity=self.identity('ocr');runtime=Runtime(identity);runtime.init_error=True
        runtime.close=Mock(side_effect=[RuntimeError('injected failed partial cleanup'),RuntimeError('injected retry cleanup failure'),None])
        factory=Mock(return_value=runtime)
        binding=LocalPlateBinding(identity,runtime_factory=factory);self.addCleanup(binding.close)
        executor=binding.executor('whole','whole_ocr')
        request=NativeRequest(memoryview(crop.read()),crop.binding.candidate,
            OperationPlan('whole',1,(),'whole_ocr','whole'),())
        context=ExecutionContext(executor)
        with self.assertRaises(LocalNotStarted):executor.invoke(request,context)
        self.assertIs(binding.runtime,runtime)
        self.assertFalse(binding.closed);self.assertTrue(binding.cleanup_pending)
        self.assertIsNone(context.model);self.assertTrue(context.retirement.is_set())
        # A second operation may not overwrite or predict with this partial object.
        context=ExecutionContext(executor)
        with self.assertRaises(LocalNotStarted) as error:executor.invoke(request,context)
        self.assertEqual(error.exception.code,'RUNTIME_CLEANUP_PENDING')
        self.assertEqual(factory.call_count,1);self.assertEqual(runtime.init_calls,1)
        self.assertEqual(runtime.close.call_count,1);self.assertFalse(runtime.images)
        with self.assertRaises(RuntimeError):binding.close()
        self.assertIs(binding.runtime,runtime);self.assertFalse(binding.closed);self.assertTrue(binding.cleanup_pending)
        binding.close();binding.close()
        self.assertTrue(binding.closed);self.assertFalse(binding.cleanup_pending)
        self.assertEqual(runtime.close.call_count,3);self.assertEqual(factory.call_count,1)
        self.assertFalse(binding.calls);self.assertFalse(runtime.images)
