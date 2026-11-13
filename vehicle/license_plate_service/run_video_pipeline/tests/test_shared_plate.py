"""Injected ownership/future tests; no real models, providers or persistence."""
import contextlib
import io
import sys
import tempfile
import time
import threading
import unittest
from concurrent.futures import Future
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from run_video_pipeline.shared import SharedPlatePipeline, PrototypeRecognizer, PlateCapacityError
from run_video_pipeline.shared_evidence import RecognizerModel, Execution
from run_video_pipeline.schemas import UnifiedPlateResult
from run_video_pipeline.plate_detector import PlateCrop
from run_video_pipeline.prototype_pipeline import PrototypePlatePipeline
from run_video_pipeline.agent import AgentPrediction, OpenRouterPlateAgent
from run_video_pipeline.ocr import OCRResult
from run_video_pipeline.marker_localizer import MarkerLocalization
from run_video_pipeline.run_video_prototype import VideoPrototypeRunner, TrackState, RecognitionAttempt
from vehicle.common.tests.test_foundations import START, context, clock_for
from vehicle.common.frame_receipt import ClockEpochInvalid
from vehicle.common.observation_coordinator import ObservationCoordinator
from vehicle.common.vehicle_observation import VehicleClosure, RunEnd
from vehicle.vehicle_classification_manufacturer_service.vehicle_classification_manufacturer_service.service import VehicleAnalysisService
from vehicle.vehicle_classification_manufacturer_service.vehicle_classification_manufacturer_service.tests.test_observation_integration import row, Capture
from vehicle.vehicle_classification_manufacturer_service.vehicle_classification_manufacturer_service.config import LOST_FRAMES
from vehicle.vehicle_color_service.vehicle_color_service.shared import SharedColourPipeline
from vehicle.vehicle_color_service.vehicle_color_service.temporal import CropQuality
from vehicle.vehicle_color_service.vehicle_color_service import config as colour_config

LOCAL = RecognizerModel('injected-local-artifact', 2)
AGENT = RecognizerModel('injected-agent-prompt', 4)
SMALL = (30,65,50,75,.9)
LARGE = (20,60,60,80,.9)


def result(text='123 تونس 4567', *, accepted=True, agent=False, complete=True, confidence=.9):
    return UnifiedPlateResult(sample_id='recognizer-sample', timestamp='2020-01-01T00:00:00+00:00',
        plate_text_raw=text or '', plate_text_normalized=text,
        category_confidence=confidence, text_confidence=confidence,
        route='agent' if agent else 'private_local', route_confidence=.9, route_reason='injected',
        source='agent' if agent else 'local_ocr', model_version='descriptive-only',
        bbox=[0,0,20,10], status='accepted' if accepted else 'rejected',
        evidence={'text_complete':complete, 'local':{'parser_status':'valid'}})


def recognize(plate, record, *, receipt):
    record(Execution('private_local', LOCAL))
    return result()


class Executor:
    def __init__(self, immediate=False, fail=False):
        self.tasks = []
        self.immediate, self.fail = immediate, fail

    def submit(self, fn, *args):
        if self.fail:
            raise RuntimeError('executor failed')
        f = Future()
        self.tasks.append((f, fn, args))
        if self.immediate:
            self.complete(len(self.tasks)-1)
        return f

    def start(self, index):
        return self.tasks[index][0].set_running_or_notify_cancel()

    def complete(self, index):
        f, fn, args = self.tasks[index]
        if not f.running() and not self.start(index):
            return
        f.set_result(fn(*args))


class SharedPlateTests(unittest.TestCase):
    def setUp(self):
        self.patch = patch.object(colour_config, 'COLOR_AGENT_ENABLED', False)
        self.patch.start(); self.addCleanup(self.patch.stop)

    def setup_owner(self, outputs=None, *, plates=None, raw_ids=None, executor=None,
                    recognizer=recognize, colour=False, walls=None, **kwargs):
        ctx = context()
        detector = Mock(process=Mock(side_effect=outputs or [[row()]]*40))
        owner = VehicleAnalysisService(enable_roi=False, _detector_tracker=detector,
                    observation_clock=clock_for(ctx, walls=walls, monos=list(range(100,140))))
        detections = iter(plates if plates is not None else [[SMALL]]*40)
        plate_detector = Mock(predict=Mock(side_effect=lambda *a,**k: next(detections)))
        raw = iter(raw_ids if raw_ids is not None else [7]*40)
        def track(d, frame):
            rid = next(raw)
            return [list(box)+[rid] for box in d.xyxy]  # Actual padded tracking boxes.
        tracker = Mock(update=Mock(side_effect=track))
        executor = executor if executor is not None else Executor(immediate=True)
        plate = SharedPlatePipeline(ctx, detector=plate_detector, tracker=tracker,
            recognizer=recognizer, executor=executor,
            **{'strong_quality':0,'strong_min_candidates':1,'minimum_exit_candidates':1,
               'drain_timeout':0,**kwargs})
        consumers = {}
        if colour:
            estimate = lambda *a,**k: SimpleNamespace(label='red',confidence=.9,diagnostics=lambda: {})
            consumers['colour'] = SharedColourPipeline(ctx, estimate_colour=estimate,
                                crop_quality=lambda *a,**k: CropQuality(1,1,1,1,1,100,0,0))
        consumers['plate'] = plate
        coord = ObservationCoordinator(owner, consumers)
        return owner, plate, coord, executor, consumers.get('colour')

    def frame(self, coord, index, image=None):
        receipt = coord.capture_receipt(index)
        image = np.full((120,240,3), 23, np.uint8) if image is None else image
        return coord.process_frame(image,index,receipt=receipt)

    def final(self, coord, *, stop=False):
        coord.stop() if stop else coord.finish()
        done = coord.drain_completed()
        return [dict(v.consumer_evidence)['plate'] for v in done]

    def test_single_owner_exact_receipt_and_model(self):
        owner,p,c,ex,_ = self.setup_owner(colour=True)
        trace = []
        owner.pipeline.detector_tracker.process.side_effect = lambda im: trace.append('vehicle') or [row()]
        p.detector.predict.side_effect = lambda *a,**k: trace.append('plate') or [SMALL]
        image = np.full((120,240,3),23,np.uint8)
        capture = Capture([image]*3,trace=trace)
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()), patch('cv2.VideoCapture', side_effect=AssertionError('extra decode')):
            path=Path(d)/'clip'; path.touch()
            c.process_video(path,_capture_factory=lambda source:capture,_media_pts_reader=lambda cap:1.25)
        self.assertEqual(capture.trace.count('decode'),4)
        self.assertEqual(owner.pipeline.detector_tracker.process.call_count,3)
        self.assertEqual(p.detector.predict.call_count,3)
        self.assertEqual(trace[:3],['decode','vehicle','plate'])
        done=c.drain_completed()[0]; evidence=dict(done.consumer_evidence)
        self.assertEqual(evidence['colour'].identity,evidence['plate'].identity)
        self.assertIs(evidence['colour'].samples[0].receipt,evidence['plate'].ledger[0].crop.receipt)
        self.assertIs(evidence['plate'].selected.receipt, done.vehicle.lifetime.first_receipt)
        self.assertEqual(evidence['plate'].selected.model,LOCAL)
        self.assertEqual(evidence['plate'].selected.receipt.media_pts,1.25)

    def test_geometry_not_numeric_equality(self):
        right=[120,0,220,100,7,.9,2]
        _,p,c,_,_=self.setup_owner(outputs=[[row(99),right]],raw_ids=[7])
        self.frame(c,0)
        e=self.final(c)
        self.assertEqual(e[0].outcome,'read'); self.assertEqual(e[0].identity.raw_track_id,99)
        self.assertEqual(e[1].attempts,0)

    def test_ambiguous_never_admitted(self):
        _,p,c,ex,_=self.setup_owner(outputs=[[row(7),row(8)]])
        self.frame(c,0); es=self.final(c)
        self.assertEqual(len(ex.tasks),0)
        self.assertTrue(all(e.omission_reason=='ambiguous_association' and e.attempts==0 for e in es))

    def test_association_audit_is_frozen_and_uses_actual_receipt(self):
        events=[]
        _,p,c,_,_=self.setup_owner(on_association=events.append)
        frame=self.frame(c,0)
        self.assertEqual(events[0].status,'associated')
        self.assertEqual(events[0].selected_service_track_id,1)
        self.assertIs(events[0].receipt,frame.observation.receipt)
        with self.assertRaises(FrozenInstanceError): events[0].status='ambiguous'
        self.assertEqual(self.final(c)[0].outcome,'read')

    def test_absent_association_never_admitted(self):
        _,p,c,ex,_=self.setup_owner(plates=[[(150,65,170,75,.9)]])
        self.frame(c,0); e=self.final(c)[0]
        self.assertEqual(e.omission_reason,'unassociated_crop'); self.assertEqual(e.attempts,0)

    def test_no_vehicle_never_recognized(self):
        _,p,c,ex,_=self.setup_owner(outputs=[[]])
        self.frame(c,0); self.assertEqual(self.final(c),[]); self.assertEqual(ex.tasks,[])

    def test_no_detection_omission(self):
        _,p,c,ex,_=self.setup_owner(plates=[[]])
        self.frame(c,0); e=self.final(c)[0]
        self.assertEqual(e.omission_reason,'no_detection'); self.assertIsNone(e.selected)

    def test_accepted_unscheduled_crop_is_not_association_rejection(self):
        _,p,c,ex,_=self.setup_owner(plates=[[SMALL],[(150,65,170,75,.9)]],
            strong_min_candidates=100,min_candidates=100,minimum_exit_candidates=100)
        self.frame(c,0);self.frame(c,1)
        e=self.final(c)[0]
        self.assertEqual(e.admitted_crops,1)
        self.assertEqual(e.unassociated_crops,1)
        self.assertEqual(e.attempts,0);self.assertEqual(ex.tasks,[])
        self.assertEqual(e.omission_reason,'failure_before_model')
        self.assertIsNone(e.selected)

    def test_rejected_crop_omission(self):
        _,p,c,ex,_=self.setup_owner(plates=[[(30,65,32,67,.9)]])
        self.frame(c,0); self.assertEqual(self.final(c)[0].omission_reason,'rejected_crop')

    def test_capability_omissions_do_not_detect(self):
        for capability, reason in [('disabled','disabled_capability'),('unsupported','unsupported_capability')]:
            with self.subTest(capability=capability):
                _,p,c,_,_=self.setup_owner(capability=capability)
                self.frame(c,0); self.assertEqual(self.final(c)[0].omission_reason,reason)
                p.detector.predict.assert_not_called()

    def test_absent_vehicle_gets_no_new_attempt(self):
        _,p,c,ex,_=self.setup_owner(outputs=[[row()],[]])
        self.frame(c,0); self.frame(c,1)
        self.assertEqual(self.final(c)[0].attempts,1)

    def test_reassociation_targets_original_parent_out_of_order(self):
        ex=Executor()
        _,p,c,_,_=self.setup_owner(outputs=[[row(7),[120,0,220,100,8,.9,2]]]*2,
                   plates=[[SMALL],[(150,65,170,75,.9)]],executor=ex)
        first=self.frame(c,0); second=self.frame(c,1)
        self.assertEqual(len(ex.tasks),2)
        ex.complete(1); p.poll(); ex.complete(0); p.poll()
        es=self.final(c)
        self.assertEqual([e.attempts for e in es],[1,1])
        self.assertNotEqual(es[0].ledger[0].segment_id,es[1].ledger[0].segment_id)
        self.assertIs(es[0].selected.receipt,first.observation.receipt)
        self.assertIs(es[1].selected.receipt,second.observation.receipt)
        self.assertEqual([e.ledger[0].parent.service_track_id for e in es],[1,2])

    def test_plate_raw_reuse_creates_new_segment(self):
        _,p,c,_,_=self.setup_owner(plates=[[SMALL],[],[SMALL]])
        self.frame(c,0); self.frame(c,5); self.frame(c,6)
        e=self.final(c)[0]
        self.assertEqual(e.attempts,2)
        self.assertNotEqual(e.ledger[0].segment_id,e.ledger[1].segment_id)

    def test_vehicle_raw_reuse_after_loss_is_fresh(self):
        _,p,c,_,_=self.setup_owner(outputs=[[row()],[],[row()]],plates=[[SMALL],[],[SMALL]])
        self.frame(c,0); self.frame(c,LOST_FRAMES+1)
        old=dict(c.drain_completed()[0].consumer_evidence)['plate']
        self.frame(c,LOST_FRAMES+2); new=self.final(c)[0]
        self.assertNotEqual(old.identity.service_track_id,new.identity.service_track_id)
        self.assertEqual(old.attempts,new.attempts)
        self.assertEqual(old.closure,VehicleClosure.TRACK_LOSS)

    def test_reset_fresh_parent(self):
        _,p,c,_,_=self.setup_owner()
        self.frame(c,0); c.reset_tracking()
        old=dict(c.drain_completed()[0].consumer_evidence)['plate']
        self.frame(c,1); new=self.final(c)[0]
        self.assertEqual(old.closure,VehicleClosure.TRACKER_RESET)
        self.assertEqual(new.identity.tracker_epoch,1)
        self.assertNotEqual(old.ledger[0].segment_id,new.ledger[0].segment_id)

    def test_submission_failure_counted_before_dispatch(self):
        ex=Executor(fail=True)
        _,p,c,_,_=self.setup_owner(executor=ex)
        self.frame(c,0); e=self.final(c)[0]
        self.assertEqual(e.attempts,1)
        self.assertEqual(e.ledger[0].status,'submission_failed')
        self.assertIs(e.ledger[0].recognizer_executed,False)
        self.assertEqual(e.omission_reason,'failure_before_model')

    def test_worker_exception_preserves_execution(self):
        def fail(plate,record,**kwargs):
            record(Execution('private_local',LOCAL)); raise RuntimeError('secret detail')
        _,p,c,_,_=self.setup_owner(recognizer=fail)
        self.frame(c,0); e=self.final(c)[0]
        self.assertEqual(e.attempts,1); self.assertEqual(e.outcome,'unreadable')
        self.assertIs(e.ledger[0].recognizer_executed,True)
        self.assertEqual(e.ledger[0].error,'recognizer_error')
        self.assertEqual(e.selected.model,LOCAL)

    def test_pre_model_error_not_fabricated(self):
        _,p,c,_,_=self.setup_owner(recognizer=Mock(side_effect=RuntimeError()))
        self.frame(c,0); e=self.final(c)[0]
        self.assertEqual(e.omission_reason,'failure_before_model'); self.assertFalse(e.ledger[0].executions)

    def test_cancel_queued_operation(self):
        ex=Executor()
        _,p,c,_,_=self.setup_owner(executor=ex)
        self.frame(c,0); e=self.final(c)[0]
        self.assertTrue(ex.tasks[0][0].cancelled())
        self.assertEqual(e.ledger[0].status,'cancelled')
        self.assertIs(e.ledger[0].recognizer_executed,False)

    def test_running_deadline_late_completion_cannot_mutate(self):
        ex=Executor()
        _,p,c,_,_=self.setup_owner(executor=ex)
        self.frame(c,0); ex.start(0)
        started=time.monotonic(); e=self.final(c)[0]
        self.assertLess(time.monotonic()-started,.5)
        self.assertEqual(e.omission_reason,'unsettled_operation')
        self.assertIs(e.ledger[0].recognizer_executed,None)
        snapshot=repr(e)
        self.assertEqual(len(p._inflight),1)
        ex.complete(0); p.poll()
        self.assertEqual(repr(e),snapshot); self.assertEqual(len(p._inflight),0)
        with self.assertRaises(FrozenInstanceError): e.outcome='read'

    def test_workers_return_only_owner_applies(self):
        ex=Executor()
        _,p,c,_,_=self.setup_owner(executor=ex)
        self.frame(c,0)
        operation=p._vehicles[1].segments[0].operations[0]
        ex.complete(0)
        self.assertEqual(operation.evidence.status,'submitted')
        p.poll(); self.assertEqual(operation.evidence.status,'completed')
        self.final(c)

    def test_crop_bytes_receipt_stable_and_inference_owns_buffer(self):
        def mutate(plate, record, **kwargs):
            self.assertEqual(int(plate.crop[0,0,0]),23)
            plate.crop[:]=99
            record(Execution('private_local',LOCAL)); return result()
        ex=Executor()
        _,p,c,_,_=self.setup_owner(executor=ex,recognizer=mutate)
        im=np.full((120,240,3),23,np.uint8)
        first=self.frame(c,0,im); im[:]=71
        ex.complete(0); p.poll()
        self.frame(c,1,im)
        e=self.final(c)[0]
        self.assertEqual(e.selected.crop.array()[0,0,0],23)
        self.assertIs(e.selected.receipt,first.observation.receipt)
        copied=e.selected.crop.array(); copied[:]=0
        self.assertEqual(e.selected.crop.array()[0,0,0],23)

    def test_unreadable_best_attempt_all_provenance_together(self):
        def unreadable(plate,record,**kwargs):
            record(Execution('private_local',LOCAL)); return result(None,accepted=False)
        _,p,c,_,_=self.setup_owner(plates=[[SMALL],[LARGE]],recognizer=unreadable)
        a=self.frame(c,0); b=self.frame(c,1); e=self.final(c)[0]
        self.assertEqual(e.attempts,2); self.assertEqual(e.outcome,'unreadable')
        self.assertIs(e.selected.receipt,b.observation.receipt)
        self.assertEqual(e.selected.crop.bbox,LARGE[:4])
        self.assertEqual(e.selected.attempt_id,e.ledger[1].attempt_id)
        self.assertEqual(e.selected.crop_score,e.ledger[1].crop_score)
        self.assertEqual(e.selected.model,e.ledger[1].executions[0].model)

    def test_null_quality_fallback_is_earliest(self):
        def unreadable(plate,record,**kwargs):
            record(Execution('private_local',LOCAL)); return result(None,accepted=False)
        _,p,c,_,_=self.setup_owner(plates=[[SMALL],[LARGE]],recognizer=unreadable,quality=lambda *a:None)
        a=self.frame(c,0); self.frame(c,1); e=self.final(c)[0]
        self.assertEqual(e.attempts,2); self.assertEqual(e.outcome,'unreadable')
        self.assertIs(e.selected.receipt,a.observation.receipt)
        self.assertIsNone(e.selected.crop_score)

    def test_measured_quality_preferred_to_null(self):
        q=iter([None,1])
        def unreadable(plate,record,**kwargs):
            record(Execution('private_local',LOCAL)); return result(None,accepted=False)
        _,p,c,_,_=self.setup_owner(plates=[[SMALL],[LARGE]],recognizer=unreadable,quality=lambda *a:next(q))
        self.frame(c,0); b=self.frame(c,1); e=self.final(c)[0]
        self.assertIs(e.selected.receipt,b.observation.receipt)

    def test_local_read_replacement_policy_preserved(self):
        texts=iter([result('A',confidence=.9),result('B',confidence=.89)])
        def rec(plate,record,**kwargs):
            record(Execution('private_local',LOCAL)); return next(texts)
        _,p,c,_,_=self.setup_owner(plates=[[SMALL],[LARGE]],recognizer=rec)
        a=self.frame(c,0); self.frame(c,1); e=self.final(c)[0]
        self.assertEqual(e.selected.text,'A'); self.assertIs(e.selected.receipt,a.observation.receipt)

    def test_agent_completeness_admission_and_model(self):
        for complete,expected in [(False,'unreadable'),(True,'read')]:
            def rec(plate,record,**kwargs):
                record(Execution('private_local',LOCAL)); record(Execution('agent',AGENT))
                return result(agent=True,complete=complete)
            _,p,c,_,_=self.setup_owner(recognizer=rec)
            a=self.frame(c,0); e=self.final(c)[0]
            self.assertEqual(e.outcome,expected); self.assertEqual(e.selected.model,AGENT)
            self.assertEqual(e.selected.route,'agent'); self.assertIs(e.selected.receipt,a.observation.receipt)

    def test_invalid_text_and_structure_not_read(self):
        for response in [result('?'),result(''),result().model_copy(update={'evidence':{'local':{'parser_status':'ambiguous'}}})]:
            def rec(plate,record,**kwargs):
                record(Execution('private_local',LOCAL)); return response
            _,p,c,_,_=self.setup_owner(recognizer=rec)
            self.frame(c,0); self.assertEqual(self.final(c)[0].outcome,'unreadable')

    def test_missing_execution_never_borrows_model_description(self):
        _,p,c,_,_=self.setup_owner(recognizer=lambda *a,**k:result())
        self.frame(c,0); e=self.final(c)[0]
        self.assertEqual(e.omission_reason,'failure_before_model'); self.assertIsNone(e.selected)

    def test_end_stop_process_error_causes_and_idempotence(self):
        for mode,cause in [('end',VehicleClosure.END_OF_INPUT),('stop',VehicleClosure.SERVICE_STOP),('error',VehicleClosure.PROCESS_ERROR)]:
            _,p,c,ex,_=self.setup_owner(outputs=[[row()],RuntimeError('inference')])
            self.frame(c,0)
            if mode=='error':
                with self.assertRaises(RuntimeError): self.frame(c,1)
            elif mode=='stop': c.stop()
            else: c.finish(RunEnd.END_OF_INPUT)
            c.finish(); c.stop(); c.flush_closures()
            done=c.drain_completed(); self.assertEqual(len(done),1)
            self.assertEqual(dict(done[0].consumer_evidence)['plate'].closure,cause)
            self.assertEqual(len(ex.tasks),1); self.assertEqual(c.drain_completed(),())

    def test_clock_fault_closes_without_next_frame_and_quarantines(self):
        _,p,c,_,_=self.setup_owner(walls=[START,START-timedelta(seconds=1)])
        self.frame(c,0)
        with self.assertRaises(ClockEpochInvalid): c.capture_receipt(1)
        e=dict(c.drain_completed()[0].consumer_evidence)['plate']
        self.assertEqual(e.closure,VehicleClosure.CLOCK_DISCONTINUITY); self.assertTrue(e.quarantined)
        with self.assertRaises(RuntimeError): c.capture_receipt(2)
        self.assertEqual(p._vehicles,{})

    def test_colour_staged_result_survives_second_consumer_failure(self):
        _,p,c,ex,colour=self.setup_owner(colour=True)
        self.frame(c,0)
        with patch.object(p,'finalize_vehicle',side_effect=RuntimeError('temporary finalize failure')):
            with self.assertRaises(RuntimeError): c.finish()
        staged=colour._pending[1]
        self.assertEqual(len(c.classification.pending_finalized_observations(owner=c)),1)
        c.flush_closures(); c.flush_closures()
        completed=c.drain_completed(); self.assertEqual(len(completed),1)
        self.assertIs(dict(completed[0].consumer_evidence)['colour'],staged)
        self.assertEqual(len(ex.tasks),1); self.assertFalse(colour._pending); self.assertFalse(p._pending)

    def test_finalization_retry_returns_same_evidence(self):
        _,p,c,ex,_=self.setup_owner(colour=True)
        self.frame(c,0)
        with patch.object(c.classification,'acknowledge_finalized_observations',side_effect=RuntimeError()):
            with self.assertRaises(RuntimeError): c.finish()
        vehicle=c.classification.pending_finalized_observations(owner=c)[0]
        first=p.finalize_vehicle(vehicle,owner=c)
        self.assertIs(first,p.finalize_vehicle(vehicle,owner=c))
        c.flush_closures(); c.drain_completed()
        self.assertIsNone(p.finalize_vehicle(vehicle,owner=c)); self.assertEqual(len(ex.tasks),1)

    def test_owner_only_drains_and_release(self):
        _,p,c,_,_=self.setup_owner()
        self.frame(c,0)
        with self.assertRaises(RuntimeError): p.drain_finalized()
        with self.assertRaises(RuntimeError): c.classification.drain_finalized_observations()
        e=self.final(c)[0]
        self.assertFalse(p._vehicles); self.assertFalse(p._pending); self.assertFalse(p._plate_current)
        self.assertEqual(e.attempts,1)
        p.shutdown(); p.shutdown()

    def test_pending_budget_detached_tasks_remain_charged(self):
        ex=Executor()
        _,p,c,_,_=self.setup_owner(executor=ex,max_pending=1)
        self.frame(c,0); ex.start(0); c.reset_tracking(); c.drain_completed()
        with self.assertRaises(PlateCapacityError): self.frame(c,1)
        self.assertEqual(len(ex.tasks),1)
        es=[dict(v.consumer_evidence)['plate'] for v in c.drain_completed()]
        self.assertEqual(es[0].attempts,1)  # Explicit closure submission failure, no silent crop loss.
        self.assertEqual(es[0].ledger[0].error,'closure_capacity_exhausted')
        ex.complete(0); p.poll(); self.assertFalse(p._inflight)

    def test_crop_and_segment_bounds_fail_explicitly(self):
        for kwargs in [dict(max_crop_bytes=10),dict(max_segments=1)]:
            _,p,c,_,_=self.setup_owner(plates=[[SMALL],[LARGE]],raw_ids=[1,2],**kwargs)
            if 'max_crop_bytes' in kwargs:
                with self.assertRaises(PlateCapacityError): self.frame(c,0)
            else:
                self.frame(c,0)
                with self.assertRaises(PlateCapacityError): self.frame(c,1)
            self.assertTrue(c.drain_completed())

    def test_closed_ids_compressed_and_no_observation_history(self):
        _,p,c,_,_=self.setup_owner()
        for i in range(12):
            self.frame(c,i); c.reset_tracking(); c.drain_completed()
        self.assertEqual(p._closed_through,12); self.assertEqual(p._closed_ranges,[])
        self.assertFalse(p._vehicles); self.assertFalse(p._pending)

    def test_two_vehicles_plate_boxes_share_one_receipt(self):
        right=[120,0,220,100,8,.9,2]
        _,p,c,_,_=self.setup_owner(outputs=[[row(),right]],plates=[[SMALL,(150,65,170,75,.9)]])
        p.tracker.update.side_effect=lambda d,im:[list(box)+[100+i] for i,box in enumerate(d.xyxy)]
        update=self.frame(c,0); es=self.final(c)
        self.assertEqual(len(es),2)
        self.assertTrue(all(e.selected.receipt is update.observation.receipt for e in es))

    def test_bounded_wait_accepts_completion_before_deadline(self):
        ex=Executor()
        _,p,c,_,_=self.setup_owner(executor=ex,drain_timeout=.3)
        self.frame(c,0); ex.start(0)
        timer=threading.Timer(.02,lambda:ex.complete(0)); timer.start()
        try:
            e=self.final(c)[0]
        finally:
            timer.join()
        self.assertEqual(e.outcome,'read'); self.assertFalse(e.settlement_incomplete)

    def test_unexpected_future_exception_is_truthfully_unknown(self):
        ex=Executor()
        _,p,c,_,_=self.setup_owner(executor=ex)
        self.frame(c,0); ex.start(0); ex.tasks[0][0].set_exception(RuntimeError('worker disappeared'))
        e=self.final(c)[0]
        self.assertEqual(e.ledger[0].error,'worker_exception')
        self.assertIs(e.ledger[0].recognizer_executed,None)
        self.assertEqual(e.omission_reason,'unsettled_operation')

    def test_result_size_is_bounded_without_losing_execution(self):
        def rec(plate,record,**kwargs):
            record(Execution('private_local',LOCAL))
            return result().model_copy(update={'evidence':{'huge':'x'*2000}})
        _,p,c,_,_=self.setup_owner(recognizer=rec,max_result_bytes=1000)
        self.frame(c,0); e=self.final(c)[0]
        self.assertEqual(e.outcome,'unreadable'); self.assertIsNone(e.ledger[0].result_json)
        self.assertEqual(e.ledger[0].executions,(Execution('private_local',LOCAL),))

    def test_quarantine_running_operation_is_immutable(self):
        ex=Executor()
        _,p,c,_,_=self.setup_owner(executor=ex)
        self.frame(c,0); ex.start(0); c.clock_fault('injected fault')
        e=dict(c.drain_completed()[0].consumer_evidence)['plate']
        self.assertTrue(e.quarantined); self.assertTrue(e.settlement_incomplete)
        ex.complete(0); p.poll()
        self.assertIsNone(e.selected); self.assertEqual(e.closure,VehicleClosure.CLOCK_DISCONTINUITY)

    def test_limits_and_eof_keep_owner_end_triggers(self):
        for limit,trigger in [(None,'end_of_input'),(1,'frame_limit')]:
            _,p,c,_,_=self.setup_owner()
            capture=Capture([np.zeros((120,240,3),np.uint8)])
            with tempfile.TemporaryDirectory() as d,contextlib.redirect_stdout(io.StringIO()):
                path=Path(d)/'clip';path.touch()
                c.process_video(path,max_frames=limit,_capture_factory=lambda source:capture,_media_pts_reader=lambda cap:None)
            completed=c.drain_completed()[0]
            self.assertEqual(dict(completed.consumer_evidence)['plate'].closure,
                             VehicleClosure.END_OF_INPUT if limit is None else VehicleClosure.SERVICE_STOP)
            self.assertEqual(c.classification.pipeline.end_trigger,trigger)

    def test_plate_detector_error_closes_colour_and_plate(self):
        _,p,c,_,colour=self.setup_owner(colour=True)
        self.frame(c,0); p.detector.predict.side_effect=RuntimeError('plate detector error')
        with self.assertRaises(RuntimeError): self.frame(c,1)
        completed=c.drain_completed()[0]
        self.assertTrue(all(e.closure==VehicleClosure.PROCESS_ERROR for _,e in completed.consumer_evidence))
        self.assertFalse(p._vehicles); self.assertFalse(colour._tracks)

    def test_finalized_queue_bound_retains_pending_evidence(self):
        _,p,c,_,_=self.setup_owner(outputs=[[row(7),[120,0,220,100,8,.9,2]]],max_finalized=1)
        self.frame(c,0)
        # Isolate consumer protocol to demonstrate the bound without coordinator releases.
        c.classification.finish()
        vehicles=c.classification.pending_finalized_observations(owner=c)
        first=p.finalize_vehicle(vehicles[0],owner=c)
        with self.assertRaises(PlateCapacityError): p.finalize_vehicle(vehicles[1],owner=c)
        self.assertIs(first,p._pending[1])
        p.release_finalized(1,owner=c)
        second=p.finalize_vehicle(vehicles[1],owner=c)
        self.assertEqual(second.identity.service_track_id,2)
        p.release_finalized(2,owner=c)

    def test_invalid_measured_score_ineligible(self):
        for score in [float('nan'),float('inf'),-1]:
            def rec(plate,record,**kwargs):
                record(Execution('private_local',LOCAL)); return result(None,accepted=False)
            _,p,c,_,_=self.setup_owner(recognizer=rec,quality=lambda *a:score)
            self.frame(c,0); e=self.final(c)[0]
            self.assertIsNone(e.selected)
            self.assertTrue(e.ledger[0].recognizer_executed)

    def test_cancelled_default_queue_keeps_crop_budget_until_retired(self):
        from run_video_pipeline.shared import _RecognitionExecutor
        gate, entered = threading.Event(), threading.Event()
        def blocking(plate,record,**kwargs):
            entered.set()
            if not gate.wait(2):
                raise RuntimeError('test gate timeout')
            record(Execution('private_local',LOCAL));return result()
        executor=_RecognitionExecutor()
        _,p,c,_,_=self.setup_owner(executor=executor,recognizer=blocking,max_pending=2,
                     outputs=[[row(),[120,0,220,100,8,.9,2]]],
                     plates=[[SMALL,(150,65,170,75,.9)]])
        p.tracker.update.side_effect=lambda d,im:[list(box)+[100+i] for i,box in enumerate(d.xyxy)]
        try:
            self.frame(c,0);self.assertTrue(entered.wait(1))
            es=self.final(c)
            self.assertEqual(es[0].ledger[0].status,'settlement_timeout')
            self.assertEqual(es[1].ledger[0].status,'cancelled')
            self.assertEqual(len(p._inflight),2)  # Cancelled queued crop is still owned by worker queue.
            p.poll();self.assertEqual(len(p._inflight),2)
        finally:
            gate.set();executor.shutdown(wait=True)
        p.poll();self.assertFalse(p._inflight)

    def test_context_and_stale_frame_rejection(self):
        _,p,c,_,_=self.setup_owner()
        a=self.frame(c,0)
        with self.assertRaises(ValueError): p.process_vehicle_frame(np.zeros((120,240,3),np.uint8),a.observation)
        self.final(c)
        with self.assertRaises(ValueError): SharedPlatePipeline(None,detector=None,tracker=None,recognizer=None)
        with self.assertRaises(ValueError): SharedPlatePipeline(replace(context(),source_kind='rtsp'),detector=None,tracker=None,recognizer=None)


class RecognitionAdapterTests(unittest.TestCase):
    def setUp(self):
        self.crop=PlateCrop(np.full((20,80,3),23,np.uint8),(10,10,90,30),.9)
        self.receipt=clock_for(context()).capture(0)

    def pipeline(self,reader,agent):
        store=Mock(capture=Mock(side_effect=lambda result,crop:result))
        return PrototypePlatePipeline(reader,None,agent,store)

    def test_prototype_shared_receipt_no_review_and_local_execution(self):
        pipeline=self.pipeline(Mock(read_result=Mock(return_value=OCRResult('123T4567','123T4567',.99))),Mock())
        from run_video_pipeline.routing import RouteDecision
        with patch('run_video_pipeline.prototype_pipeline.route_plate',return_value=RouteDecision('private_local',.99,'test')):
            recorder=[]
            response=PrototypeRecognizer(pipeline,local_model=LOCAL)(self.crop,recorder.append,receipt=self.receipt)
        self.assertEqual(recorder,[Execution('private_local',LOCAL)])
        self.assertEqual(response.timestamp,self.receipt.utc.isoformat()); pipeline.store.capture.assert_not_called()

    def test_agent_retries_one_crop_operation_and_complete_claim(self):
        prediction=dict(visible_text='AB-123',normalized_text='AB-123',category='other',confidence=.99,evidence_summary='visible',text_complete=True)
        transport=Mock(side_effect=[TimeoutError(),{'choices':[{'message':{'content':__import__('json').dumps(prediction)}}]}])
        agent=OpenRouterPlateAgent(enabled=True,api_key='injected-test',model='explicit',transport=transport,retry_limit=1)
        pipeline=self.pipeline(Mock(read_result=Mock(return_value=OCRResult('','',0))),agent)
        records=[]
        response=PrototypeRecognizer(pipeline,local_model=LOCAL,agent_model=AGENT)(self.crop,records.append,receipt=self.receipt)
        self.assertEqual(transport.call_count,2)
        self.assertTrue(response.evidence['text_complete'])
        self.assertEqual(pipeline.counters.snapshot()['processed'],1)
        self.assertIn('text_complete',transport.call_args.args[2]['response_format']['json_schema']['schema']['required'])
        self.assertEqual(records.count(Execution('agent',AGENT)),2)  # Worker deduplicates calls, ledger still one operation.
        pipeline.store.capture.assert_not_called()

    def test_split_ocr_calls_remain_one_operation_execution(self):
        from run_video_pipeline.shared import _work
        from run_video_pipeline.shared_evidence import OwnedCrop
        reader=Mock(read_result=Mock(side_effect=[OCRResult('123456','123456',.99),
                       OCRResult('123','123',.99),OCRResult('456','456',.99)]))
        pipeline=self.pipeline(reader,OpenRouterPlateAgent(enabled=False))
        pipeline.marker_localizer=Mock(locate=Mock(return_value=MarkerLocalization(True,.99,(30,0,40,20),35)))
        recognizer=PrototypeRecognizer(pipeline,local_model=LOCAL)
        outcome=_work(recognizer,OwnedCrop.capture(self.crop.crop,self.crop.bbox,self.receipt),
                         .9,time.monotonic,65536)
        self.assertEqual(reader.read_result.call_count,3)
        self.assertEqual(outcome.executions,(Execution('private_local',LOCAL),))
        response=UnifiedPlateResult.model_validate_json(outcome.result_json)
        self.assertEqual(response.plate_text_normalized,'123 تونس 456')
        self.assertEqual(pipeline.counters.snapshot()['processed'],1)

    def test_agent_disabled_does_not_claim_execution(self):
        pipeline=self.pipeline(Mock(read_result=Mock(return_value=OCRResult('','',0))),OpenRouterPlateAgent(enabled=False))
        records=[]
        PrototypeRecognizer(pipeline,local_model=LOCAL)(self.crop,records.append,receipt=self.receipt)
        self.assertEqual(records,[Execution('private_local',LOCAL)])

    def test_legacy_prediction_missing_completeness_still_parses(self):
        p=AgentPrediction(visible_text='x',normalized_text='x',category='other',confidence=.9,evidence_summary='x')
        self.assertFalse(p.text_complete)
        with self.assertRaises(Exception): AgentPrediction.model_validate({**p.model_dump(),'unexpected':1})
        with self.assertRaises(Exception): AgentPrediction.model_validate({**p.model_dump(),'text_complete':'true'})

    def test_explicit_local_ocr_artifacts_and_device_preserve_defaults(self):
        from run_video_pipeline.ocr import PlateReader
        constructor=Mock()
        with patch.dict(sys.modules,{'paddleocr':SimpleNamespace(TextRecognition=constructor)}):
            PlateReader()
            constructor.assert_called_once_with(model_name='PP-OCRv6_medium_rec')
            constructor.reset_mock()
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(FileNotFoundError): PlateReader(model_dir=directory,device='cpu')
                constructor.assert_not_called()
                for name in ('inference.json','inference.yml','inference.pdiparams'):
                    (Path(directory)/name).touch()
                PlateReader(model_dir=directory,device='cpu')
                constructor.assert_called_once_with(model_name='PP-OCRv6_medium_rec',model_dir=directory,device='cpu')

    def test_plate_and_marker_device_is_optional_and_forwarded(self):
        from run_video_pipeline.plate_detector import PlateDetector
        from run_video_pipeline.marker_localizer import TounesMarkerLocalizer
        for device in (None,'cpu'):
            detector=PlateDetector(device=device)
            detector._model=Mock(predict=Mock(return_value=[SimpleNamespace(boxes=[])]))
            detector.predict(self.crop.crop)
            marker=TounesMarkerLocalizer(device=device)
            marker._model=Mock(predict=Mock(return_value=[SimpleNamespace(boxes=None)]))
            marker.locate(self.crop.crop)
            for model in (detector._model,marker._model):
                if device is None: self.assertNotIn('device',model.predict.call_args.kwargs)
                else: self.assertEqual(model.predict.call_args.kwargs['device'],'cpu')

    def test_standalone_process_crop_timestamp_and_review_compatible(self):
        pipeline=self.pipeline(Mock(read_result=Mock(return_value=OCRResult('','',0))),OpenRouterPlateAgent(enabled=False))
        response=pipeline.process_crop(self.crop)
        pipeline.store.capture.assert_called_once()
        self.assertNotEqual(response.timestamp,self.receipt.utc.isoformat())
        self.assertEqual(response.status,'unavailable')

    def test_standalone_runner_raw_id_api_compatible(self):
        pipeline=Mock()
        detector=Mock(predict=Mock(return_value=[SMALL]))
        tracker=Mock(update=Mock(return_value=[[30,65,50,75,777]]))
        runner=VideoPrototypeRunner(pipeline,detector,tracker,Mock(),min_candidates=100,minimum_exit_candidates=100)
        try:
            output=runner.process_frame(np.zeros((120,240,3),np.uint8),0,'test')
            self.assertEqual(output,[(777,SMALL[:4])]); self.assertIn(777,runner.tracks)
            runner.finish('test'); self.assertIsNone(runner.tracks[777].final_result)
        finally:
            runner.executor.shutdown(wait=False,cancel_futures=True)


if __name__ == '__main__':
    unittest.main()
