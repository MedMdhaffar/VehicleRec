"""Shared-colour tests use no models, databases, network or real video decoder."""
import contextlib
import io
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from vehicle.common.tests.test_foundations import START, context
from vehicle.common.frame_receipt import ClockEpochInvalid, ClockPolicy, FrameReceiptClock
from vehicle.common.observation_coordinator import ObservationCoordinator, PendingResultsFull
from vehicle.common.vehicle_observation import RunEnd, VehicleClosure
from vehicle.common.vehicle_frame import VehicleFrame
from vehicle.vehicle_classification_manufacturer_service.vehicle_classification_manufacturer_service.service import VehicleAnalysisService
from vehicle.vehicle_classification_manufacturer_service.vehicle_classification_manufacturer_service.config import LOST_FRAMES
from vehicle.vehicle_classification_manufacturer_service.vehicle_classification_manufacturer_service.tests.test_observation_integration import Capture, row
from vehicle.vehicle_color_service.vehicle_color_service import config
from vehicle.vehicle_color_service.vehicle_color_service.detector import ColourPipeline, _public_profile
from vehicle.vehicle_color_service.vehicle_color_service.shared import SharedColourPipeline
from vehicle.vehicle_color_service.vehicle_color_service.temporal import CropQuality


class SharedColourTests(unittest.TestCase):
    def setUp(self):
        self.agent_disabled = patch.object(config, 'COLOR_AGENT_ENABLED', False)
        self.agent_disabled.start()
        self.addCleanup(self.agent_disabled.stop)

    def setup_owner(self, outputs, *, labels=None, agent=None, walls=None, capability='enabled', max_pending=128, extra_consumers=None, on_completed=None, local_model=None, agent_model=None):
        ctx = context()
        walls = iter(walls if walls is not None else [START+timedelta(seconds=i) for i in range(200)])
        monos = iter(range(100,300))
        clock = FrameReceiptClock(ctx, utc_clock=lambda: next(walls),
                                  monotonic_clock=lambda: next(monos), policy=ClockPolicy())
        detector = Mock()
        detector.process.side_effect = outputs
        owner = VehicleAnalysisService(enable_roi=False, _detector_tracker=detector, observation_clock=clock)
        values = iter(labels if labels is not None else [('red',.9)]*200)

        def estimate(crop, **kwargs):
            answer = next(values)
            if isinstance(answer, Exception):
                raise answer
            return SimpleNamespace(label=answer[0], confidence=answer[1], diagnostics=lambda: {})

        inference = Mock(side_effect=estimate)
        quality = Mock(return_value=CropQuality(1,1,1,1,1,100,0,0))
        colour = SharedColourPipeline(ctx, estimate_colour=inference, crop_quality=quality,
                                      agent_colour=agent, capability=capability, local_model=local_model, agent_model=agent_model)
        coordinator = ObservationCoordinator(owner, {'colour': colour, **(extra_consumers or {})}, max_pending=max_pending, on_completed=on_completed)
        return owner, colour, coordinator, inference

    def frame(self, coordinator, index, image=None):
        receipt = coordinator.capture_receipt(index)
        image = np.full((120,120,3), 23, dtype=np.uint8) if image is None else image
        return coordinator.process_frame(image, index, receipt=receipt)

    def result(self, coordinator):
        completed = coordinator.drain_completed()
        self.assertEqual(len(completed), 1)
        return completed[0].consumer_evidence[0][1]

    def video(self, coordinator, capture, **kwargs):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            path = Path(directory)/'clip.mp4'; path.touch()
            return coordinator.process_video(path, _capture_factory=lambda source: capture,
                                             _media_pts_reader=lambda c: 1.5, **kwargs)

    def test_no_second_decode_or_detector(self):
        owner, colour, coord, infer = self.setup_owner([[row()]]*3)
        capture = Capture([np.zeros((120,120,3),dtype=np.uint8) for _ in range(3)])
        with patch('cv2.VideoCapture', side_effect=AssertionError('second decoder')):
            self.video(coord, capture)
        self.assertEqual(capture.trace.count('decode'), 4)  # Three frames plus EOF probe.
        self.assertEqual(capture.releases, 1)
        self.assertEqual(owner.pipeline.detector_tracker.process.call_count, 3)
        self.assertEqual(infer.call_count, 3)
        self.assertFalse(hasattr(colour, 'model'))
        result = self.result(coord)
        self.assertEqual(result.identity.context, owner.pipeline.lifecycle.context)
        self.assertEqual(result.identity.service_track_id, 1)
        self.assertEqual(result.identity.raw_track_id, 7)
        self.assertEqual(result.samples[0].receipt.media_pts, 1.5)

    def test_explicit_agent_disable_overrides_environment_and_injection(self):
        agent=Mock(side_effect=AssertionError('provider must remain disabled'))
        _,colour,coord,_=self.setup_owner([[row()]],agent=agent,labels=[('unknown',0)])
        colour.agent_enabled=False
        with patch.object(config,'COLOR_AGENT_ENABLED',True),patch.object(config,'COLOR_AGENT_API_KEY','injected-unit-test'):
            self.frame(coord,0);coord.finish()
        evidence=self.result(coord)
        agent.assert_not_called()
        self.assertEqual(evidence.agent_attempts,0)
        self.assertIsNone(evidence.agent_crop)

    def test_absent_vehicles_get_no_samples(self):
        owner, colour, coord, infer = self.setup_owner([[row(7),row(8)], [row(8)]])
        self.frame(coord, 0); update = self.frame(coord, 1)
        self.assertEqual([v.raw_track_id for v in update.observation.vehicles], [8])
        coord.finish()
        results = {e.identity.raw_track_id:e for completed in coord.drain_completed()
                   for _,e in completed.consumer_evidence}
        self.assertEqual(results[7].attempts, 1)
        self.assertEqual(results[8].attempts, 2)
        self.assertEqual(len(results[7].samples),1)
        self.assertEqual(infer.call_count,3)

    def test_loss_then_raw_reuse_does_not_inherit_colour(self):
        owner, colour, coord, _ = self.setup_owner([[row()], [], [row()]], labels=[('red',.9),('blue',.9)])
        self.frame(coord,0)
        self.frame(coord,LOST_FRAMES+1)
        old = self.result(coord)
        self.assertEqual(old.closure, VehicleClosure.TRACK_LOSS)
        self.frame(coord,LOST_FRAMES+2)
        coord.stop()
        fresh = self.result(coord)
        self.assertEqual(old.identity.raw_track_id, fresh.identity.raw_track_id)
        self.assertNotEqual(old.identity.service_track_id, fresh.identity.service_track_id)
        self.assertEqual([s.label for s in fresh.samples], ['blue'])
        self.assertEqual(fresh.attempts, 1)
        self.assertEqual(fresh.identity.lifetime_sequence, 2)

    def test_reset_then_raw_reuse_is_separate(self):
        owner, colour, coord, _ = self.setup_owner([[row()],[row()]])
        self.frame(coord,0); coord.reset_tracking()
        old = self.result(coord)
        self.frame(coord,1); coord.finish()
        fresh = self.result(coord)
        self.assertEqual(old.closure,VehicleClosure.TRACKER_RESET)
        self.assertEqual(fresh.identity.tracker_epoch,1)
        self.assertNotEqual(old.identity.service_track_id,fresh.identity.service_track_id)
        self.assertEqual(fresh.attempts,1)

    def test_frame_lifetime_and_consumed_receipts_are_immutable(self):
        owner, colour, coord, _ = self.setup_owner([[row()]]*3)
        first = self.frame(coord,0)
        old_lifetime = first.observation.vehicles[0].lifetime
        self.frame(coord,1); self.frame(coord,2); coord.finish()
        evidence = self.result(coord)
        self.assertIs(evidence.samples[0].receipt,first.observation.receipt)
        self.assertEqual(old_lifetime.frames_observed,1)
        self.assertEqual(evidence.last_vehicle_receipt.frame_index,2)
        with self.assertRaises(FrozenInstanceError):
            old_lifetime.frames_observed = 999
        with self.assertRaises(FrozenInstanceError):
            evidence.identity.raw_track_id = 99
        self.assertFalse(hasattr(evidence.identity,'lifetime'))

    def test_crop_owns_bytes_and_image_is_not_modified(self):
        owner, colour, coord, _ = self.setup_owner([[row()]]*3)
        buffer = np.full((120,120,3),42,dtype=np.uint8)
        self.frame(coord,0,buffer)
        self.assertTrue(np.all(buffer==42))
        buffer[:] = 99
        self.frame(coord,1,buffer); self.frame(coord,2,buffer); coord.finish()
        evidence = self.result(coord)
        self.assertEqual(evidence.best_crop.receipt.frame_index,0)
        self.assertTrue(np.all(evidence.best_crop.array()==42))
        mutable = evidence.best_crop.array(); mutable[:] = 0
        self.assertTrue(np.all(evidence.best_crop.array()==42))
        self.assertIsInstance(evidence.best_crop.pixels,bytes)

    def test_injected_inference_cannot_mutate_shared_buffer(self):
        owner, colour, coord, infer = self.setup_owner([[row()]])
        def mutate(crop,**kwargs):
            crop[:] = 0
            return SimpleNamespace(label='red',confidence=.9,diagnostics=lambda:{})
        infer.side_effect = mutate
        image = np.full((120,120,3),88,dtype=np.uint8)
        self.frame(coord,0,image); coord.finish()
        self.assertTrue(np.all(image==88))
        self.assertTrue(np.all(self.result(coord).best_crop.array()==88))

    def test_local_timestamp_last_contributing_valid_sample(self):
        owner, colour, coord, _ = self.setup_owner([[row()]]*4, labels=[('red',.9)]*3+[('unknown',.2)])
        for i in range(4): self.frame(coord,i)
        coord.finish(); evidence = self.result(coord)
        self.assertEqual(evidence.state,'decided')
        self.assertEqual(evidence.source,'local_hsv')
        self.assertEqual(evidence.support_receipt.frame_index,2)
        self.assertEqual(evidence.attempts,4)
        self.assertEqual(evidence.valid_sample_count,3)
        self.assertEqual(evidence.confidence,1)

    def test_early_lock_stops_attempts_but_keeps_last_sample_timestamp(self):
        count = config.MIN_VALID_COLOR_SAMPLES+3
        owner, colour, coord, infer = self.setup_owner([[row()]]*count)
        for i in range(count): self.frame(coord,i)
        coord.finish(); evidence = self.result(coord)
        self.assertEqual(evidence.attempts,config.MIN_VALID_COLOR_SAMPLES)
        self.assertEqual(infer.call_count,config.MIN_VALID_COLOR_SAMPLES)
        self.assertEqual(evidence.support_receipt.frame_index,config.MIN_VALID_COLOR_SAMPLES-1)
        self.assertEqual(evidence.last_vehicle_receipt.frame_index,count-1)

    def test_agent_support_is_actual_older_best_crop(self):
        agent = Mock(return_value=('blue',.95))
        owner, colour, coord, _ = self.setup_owner([[row()]]*3, labels=[('unknown',.1)]*3, agent=agent)
        for i in range(3): self.frame(coord,i,np.full((120,120,3),10+i,dtype=np.uint8))
        coord.finish(); evidence = self.result(coord)
        self.assertEqual(evidence.state,'decided')
        self.assertEqual(evidence.source,'agent_override')
        self.assertEqual(evidence.support_receipt.frame_index,0)
        self.assertEqual(evidence.agent_crop.receipt.frame_index,0)
        self.assertTrue(np.all(agent.call_args.args[0]==evidence.agent_crop.array()))
        self.assertEqual(evidence.agent_attempts,1)
        self.assertEqual(evidence.valid_sample_count,0)
        self.assertEqual(evidence.attempts,3)

    def test_agent_crop_not_replaced_by_later_best_crop(self):
        agent = Mock(return_value=('purple',.9))
        count = config.MAX_VALID_COLOR_SAMPLES
        outputs = [[row()]]*count + [[0,0,120,120,7,.9,2]]
        outputs[-1] = [outputs[-1]]
        labels = [('red',.9) if i%2==0 else ('blue',.9) for i in range(count)]
        owner, colour, coord, _ = self.setup_owner(outputs, labels=labels, agent=agent)
        for i in range(count+1): self.frame(coord,i,np.full((120,120,3),30+i,dtype=np.uint8))
        coord.finish(); evidence=self.result(coord)
        self.assertEqual(agent.call_count,1)
        self.assertEqual(evidence.agent_crop.receipt.frame_index,0)
        self.assertEqual(evidence.best_crop.receipt.frame_index,count)
        self.assertEqual(evidence.support_receipt.frame_index,0)
        self.assertEqual(evidence.attempts,count)

    def test_agent_exception_preserves_usable_local_result(self):
        agent=Mock(side_effect=RuntimeError('agent offline'))
        owner,colour,coord,_=self.setup_owner([[row()]]*4, labels=[('red',.9)]*3+[('blue',.9)],agent=agent)
        for i in range(4): self.frame(coord,i)
        coord.finish(); evidence=self.result(coord)
        self.assertEqual(evidence.state,'decided')
        self.assertEqual(evidence.label,'red')
        self.assertEqual(evidence.confidence,.75)
        self.assertEqual(evidence.source,'local_hsv')
        self.assertEqual(evidence.support_receipt.frame_index,3)
        self.assertEqual(evidence.agent_error,'agent_failure')
        self.assertEqual(evidence.agent_attempts,1)

    def test_rejected_agent_result_preserves_local(self):
        for answer in [('not-a-colour',.9),('blue',float('nan')),{},('blue','score')]:
            with self.subTest(answer=answer):
                owner,colour,coord,_=self.setup_owner([[row()]]*4,labels=[('red',.9)]*3+[('blue',.9)],agent=Mock(return_value=answer))
                for i in range(4): self.frame(coord,i)
                coord.finish(); evidence=self.result(coord)
                self.assertEqual(evidence.label,'red')
                self.assertEqual(evidence.source,'local_hsv')
                self.assertEqual(evidence.agent_error,'agent_rejected_result')

    def test_attempted_undecided_uses_last_attempt_receipt(self):
        owner,colour,coord,_=self.setup_owner([[row()]]*3,labels=[('unknown',.1)]*3)
        for i in range(3): self.frame(coord,i)
        coord.finish(); evidence=self.result(coord)
        self.assertEqual(evidence.state,'undecided')
        self.assertIsNone(evidence.label)
        self.assertIsNone(evidence.confidence)
        self.assertEqual(evidence.attempts,3)
        self.assertEqual(evidence.valid_sample_count,0)
        self.assertEqual(evidence.support_receipt.frame_index,2)

    def test_operation_failure_counts_attempt_and_support(self):
        owner,colour,coord,_=self.setup_owner([[row()]],labels=[RuntimeError('local colour failed')])
        with self.assertRaisesRegex(RuntimeError,'local colour'):
            self.frame(coord,0)
        evidence=self.result(coord)
        self.assertEqual(evidence.state,'error')
        self.assertEqual(evidence.local_errors,1)
        self.assertEqual(evidence.attempts,1)
        self.assertEqual(evidence.support_receipt.frame_index,0)
        self.assertEqual(evidence.closure,VehicleClosure.PROCESS_ERROR)
        self.assertEqual(colour._tracks,{})

    def test_omissions_unavailable_disabled_and_no_crop(self):
        for capability, box, reason in [('unavailable',row(),'unsupported_capability'),('disabled',row(),'disabled_capability'),('enabled',row(),'rejected_crop')]:
            with self.subTest(capability=capability):
                if reason=='rejected_crop': box=[200,200,210,210,7,.9,2]
                owner,colour,coord,infer=self.setup_owner([[box]],capability=capability)
                self.frame(coord,0); coord.finish(); evidence=self.result(coord)
                self.assertEqual(evidence.state,'omitted')
                self.assertEqual(evidence.omission_reason,reason)
                self.assertEqual(evidence.attempts,0)
                self.assertEqual(evidence.samples,())
                self.assertIsNone(evidence.support_receipt)
                infer.assert_not_called()

    def test_eof_limit_stop_and_process_error_closures(self):
        for mode in ('eof','limit','stop','error'):
            with self.subTest(mode=mode):
                owner,colour,coord,_=self.setup_owner([[row()]]*2)
                image=np.zeros((120,120,3),dtype=np.uint8)
                capture=Capture([image],error=RuntimeError('decode failed') if mode=='error' else None)
                kwargs={}
                if mode=='limit': kwargs['max_frames']=1
                if mode=='stop':
                    checks=iter([False,True]); kwargs['stop_requested']=lambda:next(checks)
                if mode=='error':
                    with self.assertRaises(RuntimeError): self.video(coord,capture,**kwargs)
                else: self.video(coord,capture,**kwargs)
                evidence=self.result(coord)
                expected={'eof':VehicleClosure.END_OF_INPUT,'limit':VehicleClosure.SERVICE_STOP,
                          'stop':VehicleClosure.SERVICE_STOP,'error':VehicleClosure.PROCESS_ERROR}[mode]
                self.assertEqual(evidence.closure,expected)
                coord.finish(); coord.stop(); coord.flush_closures()
                self.assertEqual(coord.drain_completed(),())
                self.assertEqual(colour.drain_finalized(owner=coord),())

    def test_clock_fault_transfers_quarantine_without_next_observation(self):
        owner,colour,coord,_=self.setup_owner([[row()],[row()]],walls=[START,START-timedelta(seconds=1)])
        first=self.frame(coord,0)
        with self.assertRaises(ClockEpochInvalid): coord.capture_receipt(1)
        evidence=self.result(coord)
        self.assertTrue(evidence.quarantined)
        self.assertEqual(evidence.closure,VehicleClosure.CLOCK_DISCONTINUITY)
        self.assertIs(evidence.support_receipt,first.observation.receipt)
        self.assertEqual(owner.pipeline.detector_tracker.process.call_count,1)
        self.assertEqual(colour._tracks,{})

    def test_explicit_clock_fault_transfers_immediately(self):
        owner,colour,coord,_=self.setup_owner([[row()]])
        self.frame(coord,0); coord.clock_fault('OS clock step')
        self.assertTrue(self.result(coord).quarantined)
        coord.clock_fault('repeat notification')
        self.assertEqual(coord.drain_completed(),())

    def test_coordinator_reserves_closure_delivery(self):
        owner,colour,coord,_=self.setup_owner([[row()]])
        with self.assertRaises(RuntimeError): owner.drain_finalized_observations()
        with self.assertRaises(RuntimeError): owner.pipeline.drain_finalized_observations()
        with self.assertRaises(RuntimeError): ObservationCoordinator(owner,{'colour':colour})
        self.frame(coord,0); owner.stop()  # A bypass caller must explicitly flush.
        with self.assertRaises(RuntimeError): owner.pending_finalized_observations()
        coord.flush_closures()
        self.assertEqual(len(coord.drain_completed()),1)

    def test_multiple_consumers_receive_same_closure_and_can_retry(self):
        second=Mock(context=context())
        second.finalize_vehicle.side_effect=[RuntimeError('temporary consumer fault'),'immutable-result']
        owner,colour,coord,_=self.setup_owner([[row()]],extra_consumers={'second':second})
        self.frame(coord,0); owner.stop()
        with self.assertRaisesRegex(RuntimeError,'temporary'): coord.flush_closures()
        self.assertEqual(len(owner.pending_finalized_observations(owner=coord)),1)
        self.assertEqual(len(colour._pending),1)
        coord.flush_closures()
        completed=coord.drain_completed()[0]
        self.assertEqual([name for name,_ in completed.consumer_evidence],['colour','second'])
        self.assertIs(second.finalize_vehicle.call_args.args[0],completed.vehicle)
        self.assertEqual(colour._pending,{})
        self.assertEqual(owner.pipeline.lifecycle.lifetimes,())

    def test_pending_capacity_never_discards_closures(self):
        owner,colour,coord,_=self.setup_owner([[row(7),row(8)]],max_pending=1)
        self.frame(coord,0)
        with self.assertRaises(PendingResultsFull): coord.finish()
        first=coord.drain_completed()
        self.assertEqual(len(first),1)
        self.assertEqual(len(owner.pending_finalized_observations(owner=coord)),1)
        self.assertEqual(len(colour._tracks),1)
        coord.flush_closures(); second=coord.drain_completed()
        self.assertEqual(len(second),1)
        self.assertNotEqual(first[0].vehicle.service_track_id,second[0].vehicle.service_track_id)
        self.assertEqual(colour._tracks,{})
        self.assertEqual(owner.pipeline.lifecycle.lifetimes,())

    def test_synchronous_completed_sink_allows_bounded_file_delivery(self):
        received=[]
        owner,colour,coord,_=self.setup_owner([[row(7),row(8)]],max_pending=1,on_completed=received.append)
        self.video(coord,Capture([np.zeros((120,120,3),dtype=np.uint8)]))
        self.assertEqual(len(received),2)
        self.assertEqual(coord.drain_completed(),())
        self.assertEqual(colour._pending,{})
        self.assertEqual(owner.pipeline.lifecycle.lifetimes,())

    def test_failed_completed_sink_retains_result_for_retry(self):
        received=[]
        def accept(completed):
            if not received:
                received.append('failure')
                raise RuntimeError('sink not ready')
            received.append(completed)
        owner,colour,coord,_=self.setup_owner([[row()]],on_completed=accept)
        self.frame(coord,0); owner.stop()
        with self.assertRaisesRegex(RuntimeError,'sink not ready'): coord.flush_closures()
        self.assertEqual(len(colour._pending),1)
        self.assertEqual(len(owner.pending_finalized_observations(owner=coord)),1)
        saved=coord._completed[1]
        coord.flush_closures()
        self.assertIs(received[-1],saved)
        self.assertEqual(coord.drain_completed(),())
        self.assertEqual(colour._pending,{})

    def test_draining_releases_state_without_new_finalization(self):
        owner,colour,coord,_=self.setup_owner([[row()]]*40)
        for i in range(40):
            self.frame(coord,i); coord.reset_tracking()
            completed=coord.drain_completed()
            self.assertEqual(len(completed),1)
            self.assertEqual(colour._tracks,{})
            self.assertEqual(colour._pending,{})
            self.assertEqual(colour._closed_ranges,[])
            self.assertEqual(owner.pipeline.lifecycle.lifetimes,())
            self.assertEqual(owner.pipeline.finalized_profiles,[])
            self.assertIsNone(colour.finalize_vehicle(completed[0].vehicle, owner=coord))
        self.assertEqual(colour._closed_through,40)
        coord.finish(); self.assertEqual(coord.drain_completed(),())

    def test_out_of_order_closures_preserve_older_active_ids(self):
        owner,colour,coord,_=self.setup_owner([[row(7),row(8)],[row(7)]])
        self.frame(coord,0); self.frame(coord,LOST_FRAMES+1)
        second=self.result(coord)
        self.assertEqual(second.identity.service_track_id,2)
        coord.stop(); first=self.result(coord)
        self.assertEqual(first.identity.service_track_id,1)
        self.assertEqual(colour._closed_through,2)
        self.assertEqual(colour._closed_ranges,[])

    def test_closed_id_intervals_remain_small_behind_long_lived_vehicle(self):
        outputs=[]
        for i in range(40): outputs.extend([[row(7),row(8)], [row(7)]])
        owner,colour,coord,_=self.setup_owner(outputs)
        index=0
        for i in range(40):
            self.frame(coord,index)
            index+=LOST_FRAMES+1
            self.frame(coord,index)
            index+=1
            coord.drain_completed()
        self.assertEqual(colour._closed_ranges,[(2,41)])
        self.assertEqual(len(colour._tracks),1)
        coord.stop(); coord.drain_completed()
        self.assertEqual(colour._closed_ranges,[])
        self.assertEqual(colour._closed_through,41)

    def test_colour_result_drain_is_reserved_when_coordinated(self):
        owner,colour,coord,_=self.setup_owner([[row()]])
        self.frame(coord,0)
        with self.assertRaises(RuntimeError): colour.drain_finalized()
        with self.assertRaises(RuntimeError): colour.claim_result_delivery(object())
        coord.stop()
        self.assertEqual(len(coord.drain_completed()),1)

    def test_standalone_output_and_temporal_rules_compatible(self):
        boxes=[]
        for raw in (7,):
            boxes.append(SimpleNamespace(id=SimpleNamespace(item=lambda:raw),xyxy=[np.array([0,0,100,100])],
                         cls=SimpleNamespace(item=lambda:2),conf=SimpleNamespace(item=lambda:.9)))
        box_collection=type('Boxes',(list,),{'id':True})(boxes)
        model=Mock(); model.track.return_value=[SimpleNamespace(boxes=box_collection)]
        with patch('vehicle.vehicle_color_service.vehicle_color_service.detector.resolve_device',return_value='cpu'):
            standalone=ColourPipeline(_model=model)
        estimate=Mock(return_value=SimpleNamespace(label='red',confidence=.9,diagnostics=lambda:{}))
        quality=Mock(return_value=CropQuality(1,1,1,1,1,100,0,0))
        module='vehicle.vehicle_color_service.vehicle_color_service.detector'
        with patch(module+'.estimate_colour_detailed',estimate),patch(module+'.compute_colour_crop_quality_details',quality):
            for i in range(3): standalone.process_frame(np.zeros((120,120,3),dtype=np.uint8))
            legacy=_public_profile(standalone.finalize_all()[0])
        self.assertEqual(legacy['track_id'],7)
        self.assertEqual(legacy['colour'],'red')
        self.assertEqual(legacy['colour_confidence'],1)
        self.assertEqual(legacy['colour_samples'],3)
        self.assertEqual(set(legacy),{'track_id','bbox','type','type_confidence','colour','colour_confidence',
                                    'colour_state','colour_source','colour_agent_error','colour_samples','status'})
        self.assertEqual(model.track.call_count,3)
        owner,colour,coord,_=self.setup_owner([[row()]]*3)
        for i in range(3): self.frame(coord,i)
        coord.finish(); evidence=self.result(coord)
        self.assertEqual(evidence.label,legacy['colour'])
        self.assertEqual(evidence.confidence,legacy['colour_confidence'])
        self.assertEqual(evidence.valid_sample_count,legacy['colour_samples'])

    def test_wrong_run_duplicate_frame_and_stale_vehicle_rejected(self):
        owner,colour,coord,_=self.setup_owner([[row()]])
        receipt=coord.capture_receipt(0)
        update=owner.process_frame(np.zeros((120,120,3),dtype=np.uint8),0,receipt=receipt)
        vehicle=update.observation.vehicles[0]
        for altered in [replace(update.observation,vehicles=(vehicle,vehicle)),
                        replace(update.observation,vehicles=(replace(vehicle,lifetime=replace(vehicle.lifetime,context=replace(context(),run_id='other-run'))),)),
                        replace(update.observation,vehicles=(replace(vehicle,lifetime=replace(vehicle.lifetime,last_receipt=replace(receipt))),))]:
            with self.assertRaises(ValueError): colour.process_vehicle_frame(np.zeros((120,120,3),dtype=np.uint8),altered)
        colour.process_vehicle_frame(np.zeros((120,120,3),dtype=np.uint8),update.observation)
        with self.assertRaises(ValueError): colour.process_vehicle_frame(np.zeros((120,120,3),dtype=np.uint8),update.observation)
        with self.assertRaises(RuntimeError): colour.process_frame(np.zeros((120,120,3),dtype=np.uint8))
        with self.assertRaises(RuntimeError): colour.finalize_all()

    def test_optional_colour_model_provenance_tracks_actual_supporting_route(self):
        from vehicle.common.observation_evidence import RecognitionIdentity
        local=RecognitionIdentity('injected-hsv-config-sha',2,'classify')
        agent_model=RecognitionIdentity('injected-agent-prompt-sha',4,'vlm')
        for agent, labels, expected in ((None,[('red',.9)]*3,local),
                                        (Mock(return_value=('blue',.95)),[('unknown',.1)]*3,agent_model)):
            _,colour,coord,_=self.setup_owner([[row()]]*3,labels=labels,agent=agent,local_model=local,agent_model=agent_model)
            for i in range(3):self.frame(coord,i)
            coord.finish();evidence=self.result(coord)
            self.assertEqual(evidence.executed_model,expected)
            self.assertEqual(evidence.last_attempt_receipt.frame_index,2)
            self.assertEqual(evidence.support_receipt.frame_index,2 if expected is local else 0)

    def test_colour_constructor_model_identity_is_optional_and_route_checked(self):
        from vehicle.common.observation_evidence import RecognitionIdentity
        local=RecognitionIdentity('injected-hsv-config-sha',2,'classify')
        colour=SharedColourPipeline(context(),local_model=local,agent_enabled=False)
        self.assertIs(colour.local_model,local)
        self.assertIsNone(colour.agent_model)
        with self.assertRaises(AttributeError):colour.local_model=RecognitionIdentity('other',3,'classify')
        with self.assertRaises(ValueError):SharedColourPipeline(context(),agent_model=local)
        with self.assertRaises(ValueError):SharedColourPipeline(context(),local_model=object())


if __name__=='__main__': unittest.main()
