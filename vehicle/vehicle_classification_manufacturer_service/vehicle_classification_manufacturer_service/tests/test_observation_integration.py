"""Decode/observation integration tests; no models, network or database."""
import contextlib
import io
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock

from vehicle.common.tests.test_foundations import START, context
from vehicle.common.frame_receipt import ClockEpochInvalid, ClockPolicy, FrameReceiptClock
from vehicle.common.vehicle_observation import RunEnd, VehicleClosure
from vehicle.vehicle_classification_manufacturer_service.vehicle_classification_manufacturer_service.service import VehicleAnalysisService
from vehicle.vehicle_classification_manufacturer_service.vehicle_classification_manufacturer_service.config import LOST_FRAMES


def row(raw=7, class_id=2, confidence=.9):
    return [0, 0, 100, 100, raw, confidence, class_id]


class Capture:
    def __init__(self, frames, trace=None, error=None, opened=True, release_error=None):
        self.frames = iter(frames)
        self.trace = trace if trace is not None else []
        self.error = error
        self.opened = opened
        self.release_error = release_error
        self.releases = 0

    def isOpened(self):
        return self.opened

    def read(self):
        self.trace.append('decode')
        frame = next(self.frames, None)
        if frame is None and self.error:
            raise self.error
        return (frame is not None), frame

    def release(self):
        self.releases += 1
        if self.release_error:
            raise self.release_error


class IntegrationTests(unittest.TestCase):
    def make_service(self, outputs, *, trace=None, walls=None, integrated=True):
        trace = trace if trace is not None else []
        output_iterator = iter(outputs)

        def infer(frame):
            trace.append('infer')
            output = next(output_iterator)
            if isinstance(output, BaseException):
                raise output
            return output

        detector = Mock(process=Mock(side_effect=infer))
        clock = None
        if integrated:
            ctx = context()
            wall_iterator = iter(walls) if walls is not None else None
            reading = iter(range(100, 10000))

            def utc():
                trace.append('utc')
                return next(wall_iterator) if wall_iterator is not None else START + timedelta(seconds=len([x for x in trace if x == 'utc'])-1)

            def mono():
                trace.append('mono')
                return next(reading)

            clock = FrameReceiptClock(ctx, utc_clock=utc, monotonic_clock=mono, policy=ClockPolicy())
        return VehicleAnalysisService(enable_roi=False, _detector_tracker=detector,
                                      observation_clock=clock)

    def process(self, service, index, frame=None):
        receipt = service.capture_receipt(index)
        return service.process_frame(object() if frame is None else frame, index, receipt=receipt)

    def video(self, service, capture, **kwargs):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            source = Path(directory) / 'clip.mp4'
            source.touch()
            return service.process_video(source, _capture_factory=lambda source: capture,
                                         _media_pts_reader=kwargs.pop('_media_pts_reader', lambda c: 1.25), **kwargs)

    def test_decode_receipt_pts_inference_order_and_shared_frame(self):
        trace = []
        image = object()
        service = self.make_service([[row(7), row(11)]], trace=trace)
        capture = Capture([image], trace)
        observed = []

        def pts(capture):
            trace.append('pts')
            return 12.5

        result = self.video(service, capture, _media_pts_reader=pts,
                            on_observation=lambda image, observation: observed.append((image, observation)))
        self.assertEqual(trace[:5], ['decode', 'utc', 'mono', 'pts', 'infer'])
        self.assertEqual(trace.count('utc'), 1)
        self.assertEqual(service.pipeline.detector_tracker.process.call_count, 1)
        frame = observed[0][1]
        self.assertIs(observed[0][0], image)
        self.assertEqual(frame.receipt.media_pts, 12.5)
        self.assertEqual(frame.receipt.utc, START)
        self.assertEqual(frame.receipt.monotonic, 100)
        self.assertIs(frame.vehicles[0].lifetime.first_receipt, frame.receipt)
        self.assertIs(frame.vehicles[1].lifetime.last_receipt, frame.receipt)
        self.assertEqual([v.raw_track_id for v in frame.vehicles], [7,11])
        self.assertEqual([v.service_track_id for v in frame.vehicles], [1,2])
        self.assertEqual([v['track_id'] for v in result['vehicles']], [7,11])
        with self.assertRaises(FrozenInstanceError):
            frame.vehicles[0].bbox = (1,2,3,4)

    def test_default_decoder_pts_reader_keeps_pts_separate(self):
        for position, expected in ((1500, 1.5), (float('nan'), None)):
            service = self.make_service([[row()]])
            capture = Capture([object()])
            capture.get = Mock(return_value=position)
            observed = []
            self.video(service, capture, _media_pts_reader=None,
                       on_observation=lambda f,o: observed.append(o))
            self.assertEqual(observed[0].receipt.media_pts, expected)
            self.assertEqual(observed[0].receipt.utc, START)
            capture.get.assert_called_once()

    def test_repeated_sightings_and_unchanged_class_consensus(self):
        service = self.make_service([[row()], [row()]])
        first = self.process(service, 0)
        second = self.process(service, 1)
        a, b = first.observation.vehicles[0], second.observation.vehicles[0]
        self.assertEqual(a.service_track_id, b.service_track_id)
        self.assertEqual(b.lifetime.frames_observed, 2)
        self.assertIs(b.lifetime.first_receipt, first.observation.receipt)
        self.assertIs(b.lifetime.last_receipt, second.observation.receipt)
        self.assertEqual(b.vehicle_type, 'car')
        self.assertEqual(b.type_confidence, .9)
        standalone = self.make_service([[row()], [row()]], integrated=False)
        standalone.process_frame(object(), 0)
        legacy = standalone.process_frame(object(), 1)
        self.assertEqual(second.to_dict(), legacy.to_dict())
        copied = second.to_dict()
        copied['active'][0]['track_id'] = -1
        self.assertEqual(second.active[0]['track_id'], 7)

    def test_loss_and_same_epoch_raw_reuse(self):
        service = self.make_service([[row()], [], [row()]])
        old = self.process(service, 0).observation.vehicles[0]
        lost = self.process(service, LOST_FRAMES+1)
        self.assertEqual(lost.observation.vehicles, ())
        self.assertEqual(lost.finalized[0]['track_id'], 7)
        closed = service.drain_finalized_observations()[0]
        self.assertEqual(closed.lifetime.closure, VehicleClosure.TRACK_LOSS)
        fresh = self.process(service, LOST_FRAMES+2).observation.vehicles[0]
        self.assertNotEqual(fresh.service_track_id, old.service_track_id)
        self.assertEqual(fresh.lifetime.lifetime_sequence, 2)
        self.assertEqual(fresh.raw_track_id, old.raw_track_id)

    def test_same_frame_interface_excludes_stale_active_boxes(self):
        service = self.make_service([[row(7), row(8)], [row(8)]])
        self.process(service, 0)
        update = self.process(service, 1)
        self.assertEqual(len(update.active), 2)
        self.assertEqual([v.raw_track_id for v in update.observation.vehicles], [8])

    def test_explicit_reset_preserves_run_and_allocates_fresh_id(self):
        service = self.make_service([[row()], [row()]])
        old = self.process(service, 0).observation.vehicles[0]
        self.assertEqual(service.reset_tracking()[0].track_id, 7)
        closed = service.drain_finalized_observations()[0]
        self.assertEqual(closed.lifetime.closure, VehicleClosure.TRACKER_RESET)
        fresh = self.process(service, 1).observation.vehicles[0]
        self.assertNotEqual(old.service_track_id, fresh.service_track_id)
        self.assertEqual(fresh.lifetime.tracker_epoch, 1)
        self.assertIs(old.lifetime.context, fresh.lifetime.context)
        self.assertIsNone(service.pipeline.lifecycle.end_reason)

    def test_eof_limit_and_explicit_stop(self):
        for scenario in ('eof', 'limit', 'stop'):
            with self.subTest(scenario=scenario):
                service = self.make_service([[row()], [row()]])
                capture = Capture([object(), object()] if scenario != 'eof' else [object()])
                kwargs = {}
                if scenario == 'limit':
                    kwargs['max_frames'] = 1
                if scenario == 'stop':
                    checks = iter([False, True])
                    kwargs['stop_requested'] = lambda: next(checks)
                self.video(service, capture, **kwargs)
                expected = RunEnd.END_OF_INPUT if scenario == 'eof' else RunEnd.SERVICE_STOP
                self.assertEqual(service.pipeline.lifecycle.end_reason, expected)
                self.assertEqual(service.pipeline.end_trigger,
                                 {'eof':'end_of_input', 'limit':'frame_limit', 'stop':'explicit_stop'}[scenario])
                finalized = service.drain_finalized_observations()
                self.assertEqual(len(finalized), 1)
                self.assertEqual(finalized[0].lifetime.closure.value, expected.value)
                self.assertEqual(service.drain_finalized_observations(), ())
                service.finish(); service.stop()
                self.assertEqual(service.drain_finalized_observations(), ())
                self.assertEqual(capture.releases, 1)

    def test_empty_input_and_zero_limit(self):
        for limit in (None, 0):
            service = self.make_service([])
            self.video(service, Capture([]), max_frames=limit)
            self.assertEqual(service.drain_finalized_observations(), ())
            self.assertEqual(service.pipeline.lifecycle.end_reason,
                             RunEnd.END_OF_INPUT if limit is None else RunEnd.SERVICE_STOP)
            service.pipeline.detector_tracker.process.assert_not_called()

    def test_stop_method_between_frames_is_idempotent(self):
        service = self.make_service([[row()]])
        self.process(service, 0)
        service.stop(); service.finish(); service.stop()
        self.assertEqual(service.pipeline.lifecycle.end_reason, RunEnd.SERVICE_STOP)
        self.assertEqual(len(service.drain_finalized_observations()), 1)
        self.assertEqual(service.drain_finalized_observations(), ())

    def test_inference_and_decode_errors_close_last_sighting(self):
        for mode in ('inference', 'decode'):
            with self.subTest(mode=mode):
                service = self.make_service([[row()], RuntimeError('inference error')])
                capture = Capture([object(), object()] if mode == 'inference' else [object()],
                                  error=RuntimeError('decode error') if mode == 'decode' else None)
                with self.assertRaisesRegex(RuntimeError, mode):
                    self.video(service, capture)
                finalized = service.drain_finalized_observations()
                self.assertEqual(finalized[0].lifetime.last_receipt.frame_index, 0)
                self.assertEqual(finalized[0].lifetime.closure, VehicleClosure.PROCESS_ERROR)
                self.assertEqual(capture.releases, 1)
                service.finish()
                self.assertEqual(service.drain_finalized_observations(), ())

    def test_consumer_error_and_cleanup_error(self):
        for mode in ('consumer', 'release'):
            with self.subTest(mode=mode):
                service = self.make_service([[row()]])
                capture = Capture([object()], release_error=RuntimeError('release error') if mode == 'release' else None)
                callback = Mock(side_effect=RuntimeError('consumer error')) if mode == 'consumer' else None
                with self.assertRaisesRegex(RuntimeError, mode):
                    self.video(service, capture, on_observation=callback)
                closed = service.drain_finalized_observations()[0]
                self.assertEqual(closed.lifetime.closure, VehicleClosure.PROCESS_ERROR)
                self.assertEqual(capture.releases, 1)

    def test_failed_open_releases_and_ends_without_observations(self):
        service = self.make_service([])
        capture = Capture([], opened=False)
        with self.assertRaisesRegex(RuntimeError, 'open input'):
            self.video(service, capture)
        self.assertEqual(capture.releases, 1)
        self.assertEqual(service.pipeline.lifecycle.end_reason, RunEnd.PROCESS_ERROR)
        self.assertEqual(service.drain_finalized_observations(), ())

    def test_clock_fault_at_decode_closes_without_next_inference(self):
        service = self.make_service([[row()], [row()]],
                                    walls=[START, START-timedelta(seconds=1)])
        capture = Capture([object(), object()])
        with self.assertRaises(ClockEpochInvalid):
            self.video(service, capture)
        self.assertEqual(service.pipeline.detector_tracker.process.call_count, 1)
        closed = service.drain_finalized_observations()[0]
        self.assertEqual(closed.lifetime.closure, VehicleClosure.CLOCK_DISCONTINUITY)
        self.assertTrue(closed.lifetime.quarantined)
        self.assertEqual(closed.lifetime.last_receipt.utc, START)
        self.assertEqual(capture.releases, 1)
        service.finish(); service.stop()
        self.assertEqual(service.drain_finalized_observations(), ())
        with self.assertRaises(RuntimeError):
            service.capture_receipt(2)

    def test_explicit_clock_fault_closes_without_next_frame(self):
        service = self.make_service([[row()]])
        update = self.process(service, 0)
        service.clock_fault('OS clock step')
        closed = service.drain_finalized_observations()[0]
        self.assertIs(closed.lifetime.last_receipt, update.observation.receipt)
        self.assertTrue(closed.lifetime.quarantined)
        self.assertEqual(service.pipeline.lifecycle.end_reason, RunEnd.CLOCK_DISCONTINUITY)
        self.assertIsNone(service.pipeline.observation_frame)

    def test_initial_clock_fault_publishes_no_receipt(self):
        service = self.make_service([[row()]], walls=[START.replace(tzinfo=None)])
        capture = Capture([object()])
        with self.assertRaises(ClockEpochInvalid):
            self.video(service, capture)
        self.assertIsNone(service.pipeline.observation_clock.last_valid_receipt)
        self.assertIsNone(service.pipeline.observation_frame)
        self.assertEqual(service.pipeline.lifecycle.end_reason, RunEnd.CLOCK_DISCONTINUITY)
        self.assertEqual(service.drain_finalized_observations(), ())
        service.pipeline.detector_tracker.process.assert_not_called()

    def test_external_decoder_pts_failure_finalizes_without_video_loop(self):
        service = self.make_service([[row()]])
        self.process(service, 0)
        with self.assertRaises(ValueError):
            service.capture_receipt(1, media_pts_reader=lambda: float('nan'))
        self.assertEqual(service.pipeline.lifecycle.end_reason, RunEnd.PROCESS_ERROR)
        self.assertEqual(len(service.drain_finalized_observations()), 1)

    def test_reset_after_invalid_epoch_closes_profiles_immediately(self):
        service = self.make_service([[row()]])
        self.process(service, 0)
        service.pipeline.observation_clock.invalidate('clock step')
        with self.assertRaises(ClockEpochInvalid):
            service.reset_tracking()
        self.assertEqual(service.pipeline.active_profiles, {})
        self.assertTrue(service.drain_finalized_observations()[0].lifetime.quarantined)

    def test_pts_failure_is_process_error_not_clock_recovery(self):
        service = self.make_service([[row()], [row()]])
        capture = Capture([object(), object()])
        readings = iter([1.0, float('nan')])
        with self.assertRaises(ValueError):
            self.video(service, capture, _media_pts_reader=lambda c: next(readings))
        self.assertIsNone(service.pipeline.observation_clock.invalid_reason)
        self.assertEqual(service.pipeline.lifecycle.end_reason, RunEnd.PROCESS_ERROR)
        self.assertEqual(service.drain_finalized_observations()[0].lifetime.last_receipt.media_pts, 1.0)

    def test_missing_wrong_or_duplicate_receipt_never_runs_inference(self):
        for mode in ('missing', 'copy', 'wrong_index', 'duplicate'):
            service = self.make_service([[row()], [row()]])
            receipt = service.capture_receipt(0)
            if mode == 'duplicate':
                service.process_frame(object(), 0, receipt=receipt)
            with self.assertRaises(ValueError):
                service.process_frame(object(), 1 if mode == 'wrong_index' else 0,
                                      receipt=None if mode == 'missing' else replace(receipt) if mode == 'copy' else receipt)
            self.assertEqual(service.pipeline.detector_tracker.process.call_count, 1 if mode == 'duplicate' else 0)
            self.assertEqual(service.pipeline.lifecycle.end_reason, RunEnd.PROCESS_ERROR)

    def test_standalone_frame_and_video_compatibility(self):
        service = self.make_service([[row()], [row()]], integrated=False)
        first = service.process_frame(object(), 0)
        self.assertIsNone(first.observation)
        self.assertIsNone(service.pipeline.lifecycle)
        self.assertEqual(set(first.to_dict()), {'frame_index','active','finalized','events'})
        self.assertEqual(first.active[0]['track_id'], 7)
        service.finish()
        self.assertEqual(service.drain_finalized_observations(), ())
        service = self.make_service([[row()], [row()]], integrated=False)
        result = self.video(service, Capture([object(),object()]))
        self.assertEqual(set(result), {'video','frames_processed','tracks_detected','processing_seconds','vehicles','events','performance'})
        self.assertEqual(result['vehicles'][0]['track_id'], 7)
        self.assertEqual(result['vehicles'][0]['vehicle_type'], 'car')
        self.assertNotIn('service_track_id', result['vehicles'][0])

    def test_no_context_cannot_expose_observations(self):
        service = self.make_service([], integrated=False)
        with self.assertRaises(ValueError):
            service.capture_receipt(0)
        with self.assertRaises(ValueError):
            self.video(service, Capture([]), on_observation=lambda f,o: None)
        with self.assertRaises(ValueError):
            VehicleAnalysisService(enable_roi=False, _detector_tracker=Mock(), observation_clock=object())

    def test_drain_releases_history_and_preserves_allocator(self):
        service = self.make_service([[row()] for _ in range(30)])
        for index in range(30):
            current = self.process(service, index).observation.vehicles[0]
            self.assertEqual(current.service_track_id, index+1)
            service.reset_tracking()
            finalized = service.drain_finalized_observations()
            self.assertEqual(len(finalized), 1)
            self.assertEqual(service.pipeline.lifecycle.lifetimes, ())
            self.assertIsNone(service.pipeline.finalized_profiles[0]._lifetime)
            self.assertEqual(len(service.drain_finalized_profiles()), 1)
            self.assertEqual(service.pipeline.finalized_profiles, [])
            self.assertEqual(service.drain_finalized_observations(), ())
            service.pipeline.detector_tracker.process.assert_called()
        service.finish(); service.finish()
        self.assertEqual(service.drain_finalized_observations(), ())

    def test_release_active_snapshot_rejected_atomically(self):
        service = self.make_service([[row(7),row(8)]])
        update = self.process(service, 0)
        service.pipeline.lifecycle.close(1, VehicleClosure.TRACK_LOSS)
        with self.assertRaises(ValueError):
            service.pipeline.lifecycle.release_closed((1,2))
        self.assertEqual(len(service.pipeline.lifecycle.lifetimes), 2)

    def test_keyboard_interrupt_maps_service_stop(self):
        service = self.make_service([[row()], KeyboardInterrupt()])
        capture = Capture([object(),object()])
        with self.assertRaises(KeyboardInterrupt):
            self.video(service, capture)
        self.assertEqual(service.drain_finalized_observations()[0].lifetime.closure, VehicleClosure.SERVICE_STOP)
        self.assertEqual(capture.releases, 1)


if __name__ == '__main__':
    unittest.main()
