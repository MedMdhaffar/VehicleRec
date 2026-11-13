"""Native tracker source-time aging and strict sampling/timeout configuration."""
from pathlib import Path
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from vehicle.common.console_sampling import advance_tracker, sampling_interval, timeout_seconds


class SamplingTests(unittest.TestCase):
    def test_decoder_receipt_preserves_capture_time_and_rejects_clock_reversal(self):
        from vehicle.common.tests.test_colour_aggregator import fixture
        from vehicle.common.frame_receipt import FrameReceiptClock, ClockPolicy, ClockEpochInvalid
        owner,_,_=fixture()
        def unexpected():raise AssertionError('decoder timestamp must not be replaced at inference')
        clock=FrameReceiptClock(owner.lifetime.context,utc_clock=unexpected,monotonic_clock=unexpected,policy=ClockPolicy())
        utc=datetime(2026,11,13,tzinfo=timezone.utc)
        first=clock.capture(0,media_pts=0,sampled_at=(utc,100))
        later=clock.capture(15,media_pts=1.5,sampled_at=(utc+timedelta(seconds=1.5),101.5))
        self.assertEqual((later.frame_index,later.utc,later.monotonic,later.media_pts),(15,utc+timedelta(seconds=1.5),101.5,1.5))
        self.assertEqual(first.utc,utc)
        with self.assertRaises(ClockEpochInvalid):clock.capture(20,sampled_at=(utc,102))

    def test_interval_defaults_and_strict_integer_validation(self):
        self.assertEqual(sampling_interval(None,'mp4'),1)
        self.assertEqual(sampling_interval(None,'rtsp'),5)
        self.assertEqual(sampling_interval('21','mp4'),21)
        for value in (0,-1,True,False,1.2,'1.0','',[],'auto'):
            with self.assertRaises(ValueError):sampling_interval(value,'mp4')
        self.assertIsNone(timeout_seconds(None))
        self.assertIsNone(timeout_seconds(''))
        for value in (True,0,-1,'nan','inf'):
            with self.assertRaises(ValueError):timeout_seconds(value)

    def test_installed_bytetrack_ages_source_gaps_without_extra_model_calls(self):
        import yaml
        from ultralytics.trackers.byte_tracker import BYTETracker
        from vehicle.license_plate_service.run_video_pipeline.crop_geometry import TrackerDetections
        root=Path(__file__).resolve().parents[3]
        settings=yaml.safe_load((root/'vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service/trackers/bytetrack_vehicles.yaml').read_text())
        tracker=BYTETracker(SimpleNamespace(**settings),frame_rate=25)
        detections=TrackerDetections.from_tuples([(10,10,80,80,.95)])
        empty=TrackerDetections.from_tuples(())
        first=tracker.update(detections,None)
        first_id=int(first[0][4])
        advance_tracker(tracker,5,empty,None)
        second=tracker.update(detections,None)
        self.assertEqual(tracker.frame_id,6)
        self.assertEqual(int(second[0][4]),first_id)
        calls=[]
        original=tracker.update
        def observed(*args):calls.append(1);return original(*args)
        advance_tracker(tracker,1000000,empty,None,update=observed)
        self.assertLessEqual(len(calls),max(getattr(tracker,'max_frames_lost',0),getattr(tracker,'max_time_lost',0))+2)
        self.assertEqual(tracker.frame_id,1000005)
        tracker.update(detections,None)
        self.assertNotIn(first_id,[int(t.track_id) for t in tracker.tracked_stracks])
