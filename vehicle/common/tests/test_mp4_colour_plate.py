"""Combined MP4 path with injected capture/native runtimes; no actual inference."""
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from threading import Event,Thread
import tempfile,time,unittest
from unittest.mock import Mock,patch
import numpy as np

from contracts.db1.reference import canonical_json_bytes
from vehicle.common.tests.test_colour_aggregator import fixture
from vehicle.common.tests.test_plate_local_executors import Runtime
from vehicle.common.tests.test_real_video import Capture
from vehicle.common.engine_plate import native_access
from vehicle.common.engine_plate_artifacts import LocalArtifact,ArtifactFile
from vehicle.common.engine_plate_local import LocalPlateBinding
from vehicle.common.frame_receipt import FrameReceiptClock,ClockPolicy
from vehicle.common.real_video.input import MP4Input
from vehicle.common.real_video.plate_local import PlateDetections,crop_in_frame,TrackerIDs
from vehicle.common.real_video.plate_engine import CombinedEngine,VideoPlateLimits
from vehicle.common.real_video.plate_host import V1Publisher,drive_video,reference
from vehicle.common.vehicle_observation import RunEnd
from vehicle.common.redis_scalar.worker import load_plate_codec
from vehicle.common.redis_scalar.synthetic import ORG,IDENTITY_NAMESPACE
from vehicle.common.redis_scalar.journal import Journal
from vehicle.common.redis_scalar.reducer import reduce_message
from vehicle.common.observation_evidence import RecognitionIdentity
from vehicle.common.db1_rows.core import Blocked


class Vehicles:
    def __init__(self,events,rows=None):self.events=events;self.rows=rows;self.calls=0;self.closed=False
    def process(self,image):
        self.events.append('vehicle');self.calls+=1
        return np.array(self.rows or [[0,0,120,90,7,.9,2,0]],dtype=float)
    def close(self):self.closed=True


class Plates:
    def __init__(self,events,batch=None):self.events=events;self.calls=0;self.closed=False;self.batch=batch
    def process(self,image):
        self.events.append('plate');self.calls+=1
        return self.batch or PlateDetections(((40,60,80,72,.9),),((1,(40,60,80,72,.9)),))
    def close(self):self.closed=True


class CombinedVideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.codec=load_plate_codec(ORG,IDENTITY_NAMESPACE)
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'injected.mp4';self.path.write_bytes(b'injected capture only')
        self.weights=Path(self.tmp.name)/'injected-native';self.weights.write_bytes(b'injected runtime artifact')
        owner,_,_=fixture();self.context=owner.lifetime.context
        self.events=[];self.tick=0
        def utc():self.events.append('utc');return self.context.started_at+timedelta(seconds=self.tick)
        def mono():self.events.append('monotonic');self.tick+=1;return 100+self.tick-1
        self.clock=FrameReceiptClock(self.context,utc_clock=utc,monotonic_clock=mono,policy=ClockPolicy())
        self.bindings={};self.runtimes={}
        for name in ('ocr','detect'):
            ident=LocalArtifact('injected-combined-native',name,(ArtifactFile.capture('fixture',self.weights),),(('injected-runtime','1'),),())
            rt=Runtime(ident);self.runtimes[name]=rt
            self.bindings[name]=LocalPlateBinding(ident,runtime_factory=lambda ident,rt=rt:rt)
        self.vehicle=Vehicles(self.events);self.plate=Plates(self.events)
        self.engines=[]
        self.addCleanup(self.dispose)

    def engine(self,**kwargs):
        e=CombinedEngine(self.context,self.clock,self.vehicle,self.plate,self.bindings,
            algorithm=RecognitionIdentity('injected-estimator',1,'classify'),quality_algorithm='injected-quality',
            plate_quality_algorithm='injected-plate-quality',estimate=lambda c:SimpleNamespace(label='red',confidence=.8),
            quality_fn=lambda c,s,b:SimpleNamespace(combined=.9),plate_quality_fn=lambda c,conf,s,b:float(c.size),**kwargs)
        self.engines.append(e);return e

    def dispose(self):
        for rt in self.runtimes.values():
            if rt.wait:rt.wait.set()
        for e in self.engines:
            for v in e.plate.lives.values():
                if v.adapter:
                    v.adapter.stop(drain_timeout=2,finish_admitted=True)
                    if v.adapter._active:v.adapter._active.context.retired()
                    v.adapter.poll()
                    v.adapter.cleanup(discard=True)
                else:
                    for _,crop in v.best.values():crop.release()
            e.close()

    def source(self,count):
        self.capture=Capture([np.full((100,160,3),50,dtype=np.uint8) for _ in range(count)],self.events)
        self.open_count=0
        def factory(path):self.open_count+=1;return self.capture
        return MP4Input(self.path,self.clock,capture_factory=factory)

    def publisher(self,engine,client=None):
        self.client=client or Mock(eval=Mock(side_effect=lambda *args:args[3].encode()))
        return V1Publisher(self.client,Journal(None,namespace='combined',trusted_org=ORG,role='plate',consumer='engine'),self.codec,engine)

    def one_frame(self,engine):
        s=self.source(1)
        with s:engine.process(s.read())
        engine.finish(RunEnd.SERVICE_STOP,trigger='injected_stop')
        return next(iter(engine.pending.values()))

    def test_console_profile_processes_101_frames_with_bounded_admitted_evidence(self):
        limits=VideoPlateLimits(streaming=True,max_frames=None,max_seconds=None,max_api_calls=None,max_raw_total=None)
        engine=self.engine(limits=limits)
        source=self.source(101)
        with source:
            while True:
                packet=source.read()
                if packet is None:break
                engine.process(packet)
        engine.finish(RunEnd.END_OF_INPUT)
        closed=next(iter(engine.pending.values()))
        self.assertEqual((source.decoded,self.vehicle.calls,self.plate.calls),(101,101,101))
        self.assertEqual(closed.owner.lifetime.frames_observed,101)
        self.assertEqual(closed.owner.lifetime.closure.value,'end_of_input')
        self.assertEqual(len(closed.classes),20)
        self.assertEqual(len(closed.measurements),20)
        self.assertEqual(engine.colour.hsv_calls,101)
        self.assertEqual(engine.colour.evidence_not_admitted,81)
        self.assertEqual(len(engine.plate.lives[1].candidates),16)
        self.assertLessEqual(len(engine.audit.events),128)

    def test_one_decoder_receipt_before_both_models_pts_and_real_scalar_tee(self):
        e=self.engine(limits=VideoPlateLimits(max_frames=3));p=self.publisher(e);packets=[]
        result=drive_video(self.source(3),e,p,on_frame=lambda packet:packets.append(packet.receipt))
        self.assertEqual((self.open_count,self.capture.reads,self.vehicle.calls,self.plate.calls),(1,3,3,3))
        self.assertEqual(self.events[:5],['decode','utc','monotonic','vehicle','plate'])
        self.assertEqual(result['reason'],'frame_limit')
        plan=next(iter(p.plans.values()));t=plan.transcript
        self.assertEqual(t.candidates[0].receipt,packets[0]);self.assertEqual(packets[2].media_pts,.12)
        self.assertEqual(plan.reference.payload.value()['facts'][0]['delivery']['class']['name'],'car')
        self.assertEqual(self.runtimes['ocr'].init_calls,1);self.assertEqual(len(self.runtimes['ocr'].images),3)
        self.assertEqual(e.audit.max_active,1);self.assertIsNone(e.audit.active)
        self.assertTrue(self.capture.released and self.vehicle.closed and self.plate.closed)

    def test_same_captured_evidence_direct_and_distributed_reduction_bytes(self):
        e=self.engine();closed=self.one_frame(e);p=self.publisher(e);self.assertTrue(p.publish(closed));plan=p.plans[closed.owner.service_track_id]
        before=dict(e.audit.calls)
        _,colour=reduce_message(self.codec.v0,'colour',self.codec.v0.decode(plan.colour),None)
        _,plate=reduce_message(self.codec,'plate',self.codec.decode(plan.plate),None)
        state=None
        for raw in (plate[0][1],plan.closed,colour[0][1]):state,rows=reduce_message(self.codec,'aggregator_plate',self.codec.decode(raw),state)
        message=self.codec.decode(rows[0][1])
        self.assertEqual(canonical_json_bytes(message.body['bundle']),plan.reference.payload.canonical)
        self.assertEqual(e.audit.calls,before)
        self.assertEqual(message.body['bundle_sha256'],plan.reference.digest)

    def test_eof_closes_at_the_original_last_receipt(self):
        e=self.engine();p=self.publisher(e);source=self.source(1)
        result=drive_video(source,e,p)
        self.assertEqual(result['reason'],'eof');self.assertEqual(source.decoded,1)
        self.assertEqual(self.codec.decode(next(iter(p.plans.values())).closed).life.closure.value,'end_of_input')
        self.assertEqual(self.tick,1) # failed EOF read does not create a receipt

    def test_controlled_stop_does_not_become_eof(self):
        e=self.engine();p=self.publisher(e);source=self.source(1)
        result=drive_video(source,e,p,stop_requested=lambda:True)
        self.assertEqual((result['reason'],source.decoded),('controlled_stop',0));self.assertFalse(p.plans)

    def test_elapsed_admission_stop_does_not_decode_or_become_eof(self):
        e=self.engine();p=self.publisher(e);source=self.source(1);values=iter((0,181,182))
        result=drive_video(source,e,p,monotonic=lambda:next(values))
        self.assertEqual((result['reason'],source.decoded),('time_limit',0));self.assertFalse(p.plans)

    def test_mapping_clipping_and_pixel_identity(self):
        image=np.arange(100*160*3,dtype=np.uint8).reshape(100,160,3)
        crop=crop_in_frame(image,(-4.7,2.9,18.6,10.2,.8),origin=(10,20))
        self.assertEqual(crop.bbox,(5,22,28,30))
        np.testing.assert_array_equal(crop.crop,image[22:30,5:28])
        crop.crop[:]=0;self.assertFalse(np.all(image[22:30,5:28]==0))
        self.assertIsNone(crop_in_frame(image,(-20,-20,-5,-4,.8)))

    def assert_diagnostic_only(self,status):
        if status=='ambiguous':self.vehicle.rows=[[0,0,120,90,7,.9,2,0],[1,0,121,90,8,.9,2,0]]
        else:self.plate.batch=PlateDetections(((130,90,150,98,.8),),((1,(130,90,150,98,.8)),))
        e=self.engine();closed=self.one_frame(e);p=self.publisher(e)
        for row in tuple(e.pending.values()):
            self.assertTrue(p.publish(row));t=p.plans[row.owner.service_track_id].transcript
            self.assertTrue(all(c.association==status and not c.crop_accepted for c in t.candidates))
            self.assertEqual(t.attempts,())
        self.assertEqual(self.runtimes['ocr'].init_calls,0)
        self.assertIsNone(e.plate.associations[0]['association']['selected_track_id'])

    def test_ambiguous_ownership_is_diagnostic_only(self):self.assert_diagnostic_only('ambiguous')
    def test_unassociated_detections_are_diagnostic_only(self):self.assert_diagnostic_only('unassociated')

    def test_no_detection_has_no_model_and_truthful_omission(self):
        self.plate.batch=PlateDetections((),());e=self.engine();closed=self.one_frame(e);p=self.publisher(e)
        self.assertTrue(p.publish(closed))
        self.assertIn(dict(domain='plate',reason='no_detection'),p.plans[1].reference.payload.value()['omissions'])
        self.assertEqual(self.runtimes['ocr'].init_calls,0)

    def test_tiny_associated_crop_is_rejected_without_recognition(self):
        d=(40,60,42,62,.8);self.plate.batch=PlateDetections((d,),((1,d),))
        e=self.engine();closed=self.one_frame(e);p=self.publisher(e);self.assertTrue(p.publish(closed))
        self.assertIn(dict(domain='plate',reason='rejected_crop'),p.plans[1].reference.payload.value()['omissions'])
        self.assertEqual(e.plate.associations[0]['status'],'rejected_size');self.assertFalse(e.plate.buffers)

    def test_fully_outside_rectangle_is_a_blocker_not_invented_crop(self):
        d=(-20,-20,-5,-4,.8);self.plate.batch=PlateDetections((d,),((1,d),))
        e=self.engine();closed=self.one_frame(e);p=self.publisher(e)
        with self.assertRaises(Blocked):p.prepare(closed)
        self.assertEqual(e.plate.lives[1].blocker,'UNREPRESENTABLE_PLATE_GEOMETRY')
        self.assertEqual(self.runtimes['ocr'].init_calls,0);self.assertFalse(e.plate.buffers)

    def test_capacity_is_checked_before_private_crop_admission(self):
        self.plate.batch=PlateDetections(((40,60,60,70,.9),(70,60,90,70,.9)),((1,(40,60,60,70,.9)),(2,(70,60,90,70,.9))))
        e=self.engine(limits=VideoPlateLimits(max_handles=1));s=self.source(1)
        with patch('vehicle.common.real_video.plate_engine.EngineCrop',side_effect=AssertionError('partial allocation')):
            with s:
                with self.assertRaises(Blocked) as error:e.process(s.read())
        self.assertEqual(error.exception.code,'CAPACITY_EXCEEDED');self.assertEqual(e.plate.buffers,[])
        self.assertTrue(all(not v.candidates for v in e.plate.lives.values()))

    def test_crop_and_operation_budgets_reject_without_recognition(self):
        e=self.engine(limits=VideoPlateLimits(max_api_calls=3));closed=self.one_frame(e)
        with self.assertRaises(Blocked):self.publisher(e).prepare(closed)
        self.assertEqual(self.runtimes['ocr'].init_calls,0)

    def test_foreign_journal_rejects_all_streams_before_eval(self):
        from uuid import UUID
        e=self.engine();closed=self.one_frame(e);p=self.publisher(e)
        p.journal=Journal(None,namespace='combined',trusted_org=UUID(int=1),role='plate',consumer='x')
        with self.assertRaises(Blocked):p.publish(closed)
        self.client.eval.assert_not_called();self.assertFalse(p.confirmed)

    def test_publication_and_release_retry_keep_original_bytes_without_inference(self):
        client=Mock();client.eval.side_effect=[b'1-0',ConnectionError('injected Colour reply loss'),b'1-0',b'1-0']
        e=self.engine();closed=self.one_frame(e);p=self.publisher(e,client)
        with self.assertRaises(ConnectionError):p.publish(closed)
        original=p.plans[1];calls=dict(e.audit.calls)
        self.assertEqual(len(e.pending),1);self.assertFalse(e.plate.lives[1].best[1][1].released)
        self.assertTrue(p.publish(closed));self.assertIs(p.plans[1],original);self.assertEqual(e.audit.calls,calls)
        self.assertEqual(client.eval.call_args_list[1].args,client.eval.call_args_list[2].args)
        adapter=e.plate.lives[1].adapter
        cleanup=adapter.cleanup;adapter.cleanup=Mock(side_effect=[RuntimeError('injected close failure'),None])
        with self.assertRaises(RuntimeError):p.publish(closed)
        self.assertTrue(p.publish(closed));self.assertEqual(e.audit.calls,calls)
        adapter.cleanup=cleanup

    def test_pending_work_after_closure_waits_without_any_publication(self):
        e=self.engine();closed=self.one_frame(e);adapter=e.plate.prepare(closed)
        self.runtimes['ocr'].wait=Event()
        self.assertFalse(adapter.drain(.05))
        with patch.object(adapter,'drain',return_value=False):self.assertIsNone(self.publisher(e).prepare(closed))
        self.client.eval.assert_not_called();self.assertFalse(adapter.confirmed)
        self.runtimes['ocr'].wait.set();self.assertTrue(adapter.drain(3))

    def test_shared_lane_refuses_detector_overlap_with_recognizer(self):
        e=self.engine();closed=self.one_frame(e);adapter=e.plate.prepare(closed);self.runtimes['ocr'].wait=Event()
        self.assertFalse(adapter.drain(.1))
        count=self.plate.calls
        with self.assertRaises(Blocked):e.audit.run('plate',lambda:self.plate.process(np.zeros((1,1,3),dtype=np.uint8)))
        self.assertEqual(self.plate.calls,count)
        self.runtimes['ocr'].wait.set();self.assertTrue(adapter.drain(3));self.assertEqual(e.audit.max_active,1)

    def test_tracker_counter_isolation_does_not_reset_or_consume_vehicle_ids(self):
        counter=SimpleNamespace(_count=9);plate=TrackerIDs(counter)
        with plate.use():counter._count=0 # actual BYTETracker constructor reset
        self.assertEqual(counter._count,9)
        with plate.use():counter._count+=1;self.assertEqual(counter._count,1)
        self.assertEqual(counter._count,9)
        counter._count+=1
        with plate.use():counter._count+=1;self.assertEqual(counter._count,2)
        self.assertEqual(counter._count,10)

    def test_tracker_reset_and_parent_reuse_change_plate_segments(self):
        e=self.engine();s=self.source(3)
        with s:
            e.process(s.read());first=e.plate.lives[1].candidates[-1].segment_id
            e.colour.pipeline.reset_tracking();e.colour._retain_closures()
            e.plate.reset_plate_tracker();e.process(s.read())
            second=e.plate.lives[2].candidates[-1].segment_id
        self.assertNotEqual(first,second)
        self.assertNotEqual(e.plate.lives[1].candidates[-1].identity,e.plate.lives[2].candidates[-1].identity)

    def test_untracked_detection_blocks_instead_of_inventing_tracker_identity(self):
        self.plate.batch=PlateDetections(((40,60,80,72,.9),),(),((40,60,80,72,.9),))
        e=self.engine();closed=self.one_frame(e)
        with self.assertRaises(Blocked):self.publisher(e).prepare(closed)
        self.assertEqual(e.plate.lives[1].blocker,'UNCONFIRMED_PLATE_TRACK')
        self.assertIsNone(e.plate.associations[0]['raw_plate_id'])

    def test_image_quality_and_hsv_are_computed_from_the_current_pixels(self):
        from vehicle.common.real_video.local import pixel_operations
        estimate,quality=pixel_operations();e=self.engine()
        e.colour.estimate=estimate;e.colour.quality=quality
        s=self.source(1)
        with s:
            packet=s.read();packet.pixels[:,:,2]=190
            expected=estimate(packet.pixels[:90,:120].copy())
            e.process(packet)
        sample=e.colour.working[1][1][0]
        self.assertEqual((sample.label,sample.confidence),(expected.label,expected.confidence))
        self.assertEqual(sample.receipt,packet.receipt);self.assertEqual((e.colour.hsv_calls,e.colour.quality_calls),(1,1))

    def test_provider_modules_are_not_loaded_by_shared_geometry(self):
        import subprocess,sys
        code="""
import sys,importlib.abc
class Guard(importlib.abc.MetaPathFinder):
 def find_spec(self,name,path=None,target=None):
  if name.split('.')[0] in {'requests','httpx','openai','sqlite3','psycopg'}:raise AssertionError(name)
sys.meta_path.insert(0,Guard())
from vehicle.common.real_video.plate_local import geometry
geometry()
assert not any(n.endswith('.agent') or n.endswith('.run_video_prototype') for n in sys.modules)
"""
        result=__import__('subprocess').run([sys.executable,'-c',code],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_actual_installed_bytetrack_counter_is_scoped(self):
        from ultralytics.trackers.basetrack import BaseTrack
        from ultralytics.trackers.byte_tracker import BYTETracker
        from vehicle.common.real_video.plate_local import geometry
        g,_=geometry();previous=BaseTrack._count
        try:
            BaseTrack._count=9;ids=TrackerIDs(BaseTrack)
            args=SimpleNamespace(track_high_thresh=.3,track_low_thresh=.1,new_track_thresh=.25,
                track_buffer=75,match_thresh=.8,fuse_score=False)
            with ids.use():tracker=BYTETracker(args,frame_rate=25)
            self.assertEqual(BaseTrack._count,9)
            with ids.use():rows=tracker.update(g.TrackerDetections.from_tuples([(1,1,20,10,.9)]))
            self.assertEqual(int(rows[0][4]),1);self.assertEqual(BaseTrack._count,9)
            self.assertEqual(BaseTrack.next_id(),10)
            with ids.use():tracker.update(g.TrackerDetections.from_tuples([(1,1,20,10,.9),(60,60,80,70,.9)]))
            self.assertEqual(ids.value,2);self.assertEqual(BaseTrack._count,10)
        finally:BaseTrack._count=previous

    def test_real_legacy_pixel_quality_helper_is_shared_after_extraction(self):
        from vehicle.common.real_video.plate_local import geometry
        g,_=geometry()
        # Numeric expected geometry/quality, not an independently copied formula.
        image=np.arange(12*40*3,dtype=np.uint8).reshape(12,40,3)
        score,area,sharpness=g.crop_quality_evidence(image,.8)
        self.assertEqual(area,480);self.assertGreater(score,0);self.assertGreaterEqual(sharpness,0)

    def test_same_best_crop_is_one_attempt_and_both_region_orders_are_explicit(self):
        e=self.engine();p=self.publisher(e);drive_video(self.source(2),e,p)
        self.assertEqual(len(p.plans[1].transcript.attempts),1)
        ops=p.plans[1].transcript.attempts[0].operations
        self.assertEqual([op.kind for op in ops],['whole_ocr','marker','left_ocr','right_ocr'])
        self.assertEqual(self.runtimes['ocr'].init_calls,1)

    def test_returned_unknown_work_does_not_publish_or_release(self):
        from vehicle.common.engine_plate import OutcomeUnknown
        e=self.engine();closed=self.one_frame(e);adapter=e.plate.prepare(closed)
        self.runtimes['ocr'].error=OutcomeUnknown()
        self.assertFalse(adapter.drain(.1))
        adapter._active.returned.wait(1)
        with self.assertRaises(Blocked) as error:self.publisher(e).prepare(closed)
        self.assertEqual(error.exception.code,'NATIVE_RETIREMENT_UNKNOWN')
        self.client.eval.assert_not_called();self.assertFalse(adapter.confirmed)
        # Explicit retirement of this returned injected runtime, not timeout proof.
        e.audit.retire('whole_ocr');adapter._active.context.retired();self.bindings['ocr'].active=False

    def test_two_crop_plans_share_models_and_run_both_regional_orders(self):
        rows=((40,60,65,72,.9),(75,60,100,72,.85))
        self.plate.batch=PlateDetections(rows,((1,rows[0]),(2,rows[1])))
        e=self.engine();closed=self.one_frame(e);p=self.publisher(e);self.assertTrue(p.publish(closed))
        attempts=p.plans[1].transcript.attempts
        self.assertEqual([[o.kind for o in a.operations] for a in attempts],
            [['whole_ocr','marker','left_ocr','right_ocr'],['whole_ocr','marker','right_ocr','left_ocr']])
        self.assertEqual(self.runtimes['ocr'].init_calls,1);self.assertEqual(len(self.runtimes['ocr'].images),6)
        self.assertEqual(self.runtimes['detect'].init_calls,1);self.assertEqual(len(self.runtimes['detect'].images),2)

    def test_changed_closed_payload_retry_conflicts_without_new_wire_or_model_calls(self):
        e=self.engine();closed=self.one_frame(e);p=self.publisher(e);self.assertTrue(p.publish(closed))
        count=self.client.eval.call_count;models=dict(e.audit.calls)
        changed=replace(closed,measurements=(replace(closed.measurements[0],confidence=.3),))
        with self.assertRaises(Blocked) as error:p.publish(changed)
        self.assertEqual(error.exception.code,'PAYLOAD_CONFLICT');self.assertEqual(self.client.eval.call_count,count)
        self.assertEqual(e.audit.calls,models)

    def test_best_buffer_replacement_preserves_all_candidates_and_original_timestamps(self):
        e=self.engine();e.plate.quality_fn=lambda c,*args:float(c[0,0,0]);s=self.source(2)
        with s:
            e.process(s.read());old=e.plate.buffers[0]
            packet=s.read();packet.pixels[:]=60;e.process(packet)
        self.assertTrue(old.released);self.assertEqual(old.release_calls,1)
        self.assertEqual(len(e.plate.lives[1].candidates),2)
        e.finish(RunEnd.SERVICE_STOP);p=self.publisher(e);self.assertTrue(p.publish(next(iter(e.pending.values()))))
        t=p.plans[1].transcript
        self.assertFalse(t.candidates[0].crop_accepted);self.assertTrue(t.candidates[1].crop_accepted)
        self.assertEqual(t.candidates[1].receipt,packet.receipt);self.assertEqual(len(t.attempts),1)
        self.assertEqual(self.runtimes['ocr'].init_calls,1)

    def test_private_byte_bound_blocks_before_any_admission(self):
        e=self.engine(limits=VideoPlateLimits(max_crop_bytes=100));s=self.source(1)
        with s:
            with self.assertRaises(Blocked):e.process(s.read())
        self.assertFalse(e.plate.buffers);self.assertFalse(e.plate.lives[1].candidates)

    def test_v0_private_runtime_defaults_are_unchanged(self):
        from vehicle.common.real_video.runtime import PrivateRuntime
        # Construction checks the binary's availability, without launching it.
        import sys
        original=PrivateRuntime(sys.executable,self.tmp.name,namespace='unit',org=ORG,identity_namespace=IDENTITY_NAMESPACE)
        target=PrivateRuntime(sys.executable,self.tmp.name,namespace='unit',org=ORG,identity_namespace=IDENTITY_NAMESPACE,delivery_version='v1')
        self.assertEqual(original.roles,('colour','aggregator'))
        self.assertEqual(target.roles,('colour','plate','aggregator_plate'))


class SmokeReporterTests(unittest.TestCase):
    def test_original_association_tuple_metadata_can_be_reported(self):
        from scripts.vehicle_mp4_colour_plate_smoke import write_evidence
        import json
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'associations.json'
            write_evidence(path,[dict(raw_bbox=(1,2,3,4),clipped_bbox=(1,2,3,4),crop_size=(2,2),
                association=dict(scores=[dict(track_id=1,bbox=(0,0,20,20))]))])
            row=json.loads(path.read_text())[0]
            self.assertEqual(row['raw_bbox'],[1,2,3,4]);self.assertEqual(row['crop_size'],[2,2])

    def test_primary_failure_report_survives_nested_tuple_lineage(self):
        from scripts.vehicle_mp4_colour_plate_smoke import write_evidence
        import json
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'report.json'
            write_evidence(path,dict(status='failed',error_code='ORIGINAL_FAILURE',recognition_lineage={
                'ocr':[dict(shape=(12,40,3),origin=(0,0))]},cleanup=dict(live_pids=[])))
            self.assertEqual(json.loads(path.read_text())['error_code'],'ORIGINAL_FAILURE')
