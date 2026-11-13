import sys
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from uuid import UUID

from vehicle.common.db1_context import (
    BIGINT_MAX, CameraRegistration, ModelRegistration, ResolutionInfo, RunContext,
)
from vehicle.common.frame_receipt import (
    ClockEpochInvalid, ClockPolicy, FrameReceipt, FrameReceiptClock,
)
from vehicle.common.vehicle_observation import (
    RunEnd, TrackIdAllocator, TrackIdExhausted, VehicleClosure, VehicleLifecycle,
)
from vehicle.common.plate_association import associate_plate

UTC = timezone.utc
START = datetime(2026, 10, 8, 10, tzinfo=UTC)


def context():
    org = UUID('12345678-1234-4234-8234-123456789012')
    camera = CameraRegistration(org, 'registered-camera', 41, 'Africa/Tunis',
                                START - timedelta(days=1), None)
    detector = ModelRegistration(91, 'explicit-detector', 3, 'detect')
    resolution = ResolutionInfo(51, 7, 'vehicle', detector, 101,
                                'unit-test supplied registration', True)
    return RunContext(org, camera, 'explicit-run', 'file', START, 'test-service',
                      resolution, 'test')


def clock_for(ctx, walls=None, monos=None, tolerance=5):
    wall = iter(walls or [START + timedelta(seconds=i) for i in range(20)])
    mono = iter(monos or range(100, 120))
    return FrameReceiptClock(ctx, utc_clock=lambda: next(wall),
                             monotonic_clock=lambda: next(mono),
                             policy=ClockPolicy(tolerance))


class ContextTests(unittest.TestCase):
    def test_deep_immutable_context(self):
        ctx = context()
        for obj, field, value in [(ctx, 'run_id', 'changed'), (ctx.camera, 'camera_key', 2),
                                  (ctx.resolution, 'run_key', 2),
                                  (ctx.resolution.detector, 'version', 9)]:
            with self.assertRaises(FrozenInstanceError):
                setattr(obj, field, value)

    def test_no_synthetic_production_or_implicit_keys(self):
        ctx = context()
        with self.assertRaisesRegex(ValueError, 'synthetic'):
            replace(ctx, environment='production')
        with self.assertRaises(TypeError):
            RunContext()
        with self.assertRaises(TypeError):
            ResolutionInfo()
        production = replace(ctx, environment='production',
                             resolution=replace(ctx.resolution, synthetic=False,
                                                provenance='explicit upstream registration'))
        self.assertEqual(production.resolution.run_key, 51)

    def test_unprovisioned_development_has_no_numeric_database_keys(self):
        supplied = context()
        dev = replace(supplied, environment='development',
            camera=replace(supplied.camera, camera_key=None),
            resolution=replace(supplied.resolution, run_key=None,lane_key=None,source_media_key=None,
                detector=replace(supplied.resolution.detector,model_key=None),synthetic=False,
                provenance='unprovisioned development; verified local artifact SHA-256'))
        self.assertFalse(dev.database_keys_resolved)
        self.assertEqual(dev.time_basis,'host_replay_receipt')
        for environment in ('test','production'):
            with self.assertRaises(ValueError): replace(dev,environment=environment)
        with self.assertRaises(ValueError): replace(supplied,environment='development')
        with self.assertRaises(ValueError): replace(dev,camera=replace(dev.camera,camera_key=41))

    def test_camera_version_and_org(self):
        ctx = context()
        with self.assertRaisesRegex(ValueError, 'organization'):
            replace(ctx, org_id=UUID(int=1))
        with self.assertRaisesRegex(ValueError, 'version'):
            replace(ctx, started_at=ctx.camera.valid_from - timedelta(microseconds=1))
        camera = replace(ctx.camera, valid_to=START)
        with self.assertRaisesRegex(ValueError, 'version'):
            replace(ctx, camera=camera)
        with self.assertRaises(ValueError):
            replace(ctx.camera, timezone='Unregistered/Zone')

    def test_source_and_resolution_validation(self):
        ctx = context()
        with self.assertRaisesRegex(ValueError, 'input media'):
            replace(ctx, resolution=replace(ctx.resolution, source_media_key=None))
        with self.assertRaises(ValueError):
            replace(ctx.resolution, lane_name='person')
        with self.assertRaises(ValueError):
            replace(ctx.resolution.detector, task='ocr')
        self.assertEqual(ctx.time_basis, 'host_replay_receipt')
        self.assertEqual(replace(ctx, source_kind='rtsp').time_basis, 'host_receipt')


class ReceiptTests(unittest.TestCase):
    def test_capture_clocks_once_and_normalize_utc(self):
        ctx = context()
        wall = Mock(return_value=START.astimezone(timezone(timedelta(hours=1))))
        mono = Mock(return_value=100.0)
        clock = FrameReceiptClock(ctx, utc_clock=wall, monotonic_clock=mono, policy=ClockPolicy())
        receipt = clock.capture(0, 123.4)
        self.assertIs(receipt.utc.tzinfo, UTC)
        self.assertEqual(receipt.utc, START)
        self.assertEqual(receipt.media_pts, 123.4)
        with self.assertRaises(FrozenInstanceError):
            receipt.utc = START
        with self.assertRaises(ValueError):
            clock.capture(0)
        wall.assert_called_once()
        mono.assert_called_once()

    def test_faults_latch_and_preserve_last_good_record(self):
        cases = [([START, START-timedelta(seconds=1)], [100,101]),
                 ([START, START+timedelta(seconds=1)], [100,99]),
                 ([START, START+timedelta(seconds=7)], [100,101]),
                 ([START, START.replace(tzinfo=None)], [100,101]),
                 ([START, START+timedelta(seconds=1)], [100,float('nan')]),
                 ([START, START+timedelta(seconds=1)], [100,float('inf')])]
        for walls, monos in cases:
            with self.subTest(walls=walls, monos=monos):
                clock = clock_for(context(), walls, monos)
                good = clock.capture(0)
                with self.assertRaises(ClockEpochInvalid):
                    clock.capture(1)
                self.assertIs(clock.last_valid_receipt, good)
                with self.assertRaises(ClockEpochInvalid):
                    clock.capture(2)

    def test_first_sample_failure_and_explicit_clock_step(self):
        clock = clock_for(context(), [START.replace(tzinfo=None)], [100])
        with self.assertRaises(ClockEpochInvalid):
            clock.capture(0)
        self.assertIsNone(clock.last_valid_receipt)
        clock = clock_for(context())
        clock.invalidate('OS clock-step notification')
        with self.assertRaises(ClockEpochInvalid):
            clock.capture(0)

    def test_tolerance_boundary_fixed_and_pts_not_utc(self):
        clock = clock_for(context(), [START, START+timedelta(seconds=3)], [100,101], 2)
        clock.capture(0, 10)
        self.assertEqual(clock.capture(1, -10).utc, START+timedelta(seconds=3))
        with self.assertRaises(FrozenInstanceError):
            clock.policy.tolerance_seconds = 50
        with self.assertRaises(AttributeError):
            clock.policy = ClockPolicy(50)
        with self.assertRaises(ValueError):
            ClockPolicy(-1)
        with self.assertRaises(ValueError):
            ClockPolicy(float('nan'))


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.ctx = context()
        self.clock = clock_for(self.ctx)
        self.life = VehicleLifecycle(self.ctx, self.clock)

    def test_shared_frame_and_repeated_sightings(self):
        receipt = self.clock.capture(0)
        a = self.life.observe(7, receipt)
        b = self.life.observe(8, receipt)
        self.assertIs(a.first_receipt, b.first_receipt)
        same = self.life.observe(7, receipt)
        self.assertEqual(same.frames_observed, 1)
        next_receipt = self.clock.capture(1)
        updated = self.life.observe(7, next_receipt)
        self.assertEqual(updated.service_track_id, a.service_track_id)
        self.assertEqual(updated.frames_observed, 2)
        self.assertIs(updated.first_receipt, receipt)
        self.assertIs(updated.last_receipt, next_receipt)

    def test_loss_raw_reuse_and_close_idempotence(self):
        receipt = self.clock.capture(0)
        old = self.life.observe(7, receipt)
        closed = self.life.lose(7)
        self.assertIsNone(self.life.lose(7))
        fresh = self.life.observe(7, self.clock.capture(1))
        self.assertNotEqual(fresh.service_track_id, old.service_track_id)
        self.assertEqual(fresh.lifetime_sequence, 2)
        self.assertIs(self.life.close(old.service_track_id, VehicleClosure.SERVICE_STOP), closed)
        # Re-closing the old snapshot must not remove the new raw-ID lifetime.
        updated = self.life.observe(7, self.clock.capture(2))
        self.assertEqual(updated.service_track_id, fresh.service_track_id)

    def test_reset_allocates_new_identity(self):
        old = self.life.observe(7, self.clock.capture(0))
        closed = self.life.reset()
        self.assertEqual(closed[0].closure, VehicleClosure.TRACKER_RESET)
        fresh = self.life.observe(7, self.clock.capture(1))
        self.assertEqual(fresh.tracker_epoch, 1)
        self.assertNotEqual(fresh.service_track_id, old.service_track_id)
        self.assertIsNone(self.life.end_reason)

    def test_reconnect_preserves_run(self):
        self.life.observe(7, self.clock.capture(0))
        closed = self.life.reconnect()
        self.assertEqual(closed[0].closure, VehicleClosure.CAMERA_OUTAGE)
        self.assertEqual(self.life.context.run_id, 'explicit-run')
        self.assertIs(self.life.context, self.ctx)
        self.assertIsNone(self.life.end_reason)
        self.assertEqual(self.life.tracker_epoch, 1)
        self.assertEqual(self.life.observe(7, self.clock.capture(1)).service_track_id, 2)

    def test_all_run_end_causes_close_once(self):
        for cause in RunEnd:
            with self.subTest(cause=cause):
                ctx = context(); clock = clock_for(ctx); life = VehicleLifecycle(ctx, clock)
                life.observe(1, clock.capture(0))
                closed = life.end(cause)
                self.assertEqual(closed[0].closure.value, cause.value)
                self.assertEqual(life.end(RunEnd.SERVICE_STOP), ())
                self.assertEqual(life.end_reason, cause)
                with self.assertRaises(RuntimeError):
                    life.observe(1, clock.last_valid_receipt)
                with self.assertRaises(RuntimeError):
                    life.reconnect()

    def test_clock_fault_quarantines_last_valid_without_fabrication(self):
        last = self.clock.capture(0)
        self.life.observe(7, last)
        self.clock.invalidate('regressed clock')
        closed = self.life.clock_fault()
        self.assertIs(closed[0].last_receipt, last)
        self.assertTrue(closed[0].quarantined)
        self.assertEqual(self.life.end_reason, RunEnd.CLOCK_DISCONTINUITY)
        self.assertEqual(self.life.clock_fault(), ())
        with self.assertRaises(ClockEpochInvalid):
            self.clock.capture(1)

    def test_observation_after_fault_closes_run(self):
        receipt = self.clock.capture(0)
        self.life.observe(7, receipt)
        self.clock.invalidate('clock fault')
        with self.assertRaises(ClockEpochInvalid):
            self.life.observe(7, receipt)
        self.assertEqual(self.life.end_reason, RunEnd.CLOCK_DISCONTINUITY)
        self.assertTrue(self.life.lifetimes[0].quarantined)

    def test_allocator_exhaustion_atomic(self):
        life = VehicleLifecycle(self.ctx, self.clock, allocator_start=BIGINT_MAX)
        receipt = self.clock.capture(0)
        self.assertEqual(life.observe(1, receipt).service_track_id, BIGINT_MAX)
        with self.assertRaises(TrackIdExhausted):
            life.observe(2, receipt)
        self.assertEqual(len(life.lifetimes), 1)
        self.assertEqual(life.observe(1, receipt).service_track_id, BIGINT_MAX)
        for invalid in [0, -1, BIGINT_MAX+1, True, 1.5]:
            with self.assertRaises(ValueError):
                TrackIdAllocator(start=invalid)

    def test_foreign_and_stale_receipts_rejected(self):
        old = self.clock.capture(0)
        current = self.clock.capture(1)
        for receipt in [old, replace(current)]:
            with self.assertRaises(ValueError):
                self.life.observe(7, receipt)
        with self.assertRaises(ValueError):
            VehicleLifecycle(replace(self.ctx), self.clock)

    def test_shared_policy_identity(self):
        from contracts.db1.association import associate_plate as policy
        self.assertIs(associate_plate, policy)
        self.assertNotIn('contracts.db1.validate_fixtures', sys.modules)
        result = associate_plate([40,60,60,70], [{'track_id':1,'bbox':[0,0,100,100]}])
        self.assertEqual(result['selected_track_id'], 1)

    def test_released_identity_query_uses_spent_run_ids(self):
        receipt=self.clock.capture(0)
        lifetime=self.life.observe(7,receipt)
        self.assertFalse(self.life.is_released(lifetime.service_track_id))
        self.life.close(lifetime.service_track_id,VehicleClosure.TRACK_LOSS)
        self.assertFalse(self.life.is_released(lifetime.service_track_id))
        self.life.release_closed((lifetime.service_track_id,))
        self.assertTrue(self.life.is_released(lifetime.service_track_id))
        self.assertFalse(self.life.is_released(999))
        self.assertEqual(self.life.lifetimes,())
        with self.assertRaises(KeyError):self.life.close(lifetime.service_track_id,VehicleClosure.TRACK_LOSS)

    def test_released_identity_query_respects_nondefault_allocator_start(self):
        life=VehicleLifecycle(self.ctx,self.clock,allocator_start=BIGINT_MAX)
        old=life.observe(7,self.clock.capture(0))
        self.assertFalse(life.is_released(1))
        life.close(old.service_track_id,VehicleClosure.TRACK_LOSS)
        life.release_closed((old.service_track_id,))
        self.assertTrue(life.is_released(BIGINT_MAX))
        self.assertFalse(life.is_released(BIGINT_MAX-1))
        for invalid in (0,True,BIGINT_MAX+1):
            with self.assertRaises(ValueError):life.is_released(invalid)


if __name__ == '__main__':
    unittest.main()
