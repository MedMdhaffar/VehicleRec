"""Actual shared consumers with injected inference; cleanup fault regressions."""
import unittest
from unittest.mock import Mock, patch

from vehicle.common.observation_coordinator import ObservationCoordinator


class CleanupTests(unittest.TestCase):
    def setup_closed(self, *, sink=True):
        from vehicle.license_plate_service.run_video_pipeline.tests.test_shared_plate import SharedPlateTests
        helper = SharedPlateTests('test_single_owner_exact_receipt_and_model')
        owner, plate, coord, executor, colour = helper.setup_owner(colour=True)
        colour.agent_enabled = False
        self.addCleanup(plate.shutdown)
        callback = Mock()
        if sink:
            coord.on_completed = callback
        helper.frame(coord, 0)
        owner.finish()  # Stage owner closure without coordinator flush.
        return owner, plate, colour, coord, callback

    def fail_once(self, original, *, after):
        calls = []
        def operation(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                if after:
                    original(*args, **kwargs)
                raise RuntimeError('injected cleanup failure')
            return original(*args, **kwargs)
        return Mock(side_effect=operation)

    def test_classification_acknowledgment_failure_before_and_after_side_effect(self):
        for after in (False, True):
            with self.subTest(after=after):
                owner, plate, colour, coord, sink = self.setup_closed()
                ack=self.fail_once(owner.acknowledge_finalized_observations,after=after)
                with patch.object(owner,'acknowledge_finalized_observations',ack), \
                     patch.object(plate,'finalize_vehicle',wraps=plate.finalize_vehicle) as finalize:
                    with self.assertRaises(RuntimeError):coord.flush_closures()
                    saved=coord._completed[1]
                    coord.flush_closures();coord.flush_closures()
                    self.assertEqual(sink.call_count,1)
                    self.assertIs(sink.call_args.args[0],saved)
                    self.assertEqual(finalize.call_count,1)
                    self.assertEqual((plate._pending,colour._pending),({},{}))
                    self.assertEqual(len(coord._completed),0)

    def test_each_consumer_release_failure_before_and_after_side_effect(self):
        for name in ('colour','plate'):
            for after in (False,True):
                with self.subTest(name=name,after=after):
                    owner,plate,colour,coord,sink=self.setup_closed()
                    consumer={'colour':colour,'plate':plate}[name]
                    release=self.fail_once(consumer.release_finalized,after=after)
                    other=plate if name == 'colour' else colour
                    with patch.object(consumer,'release_finalized',release), \
                         patch.object(plate,'finalize_vehicle',wraps=plate.finalize_vehicle) as finalize, \
                         patch.object(other,'release_finalized',wraps=other.release_finalized) as other_release:
                        with self.assertRaises(RuntimeError):coord.flush_closures()
                        saved=coord._completed[1]
                        self.assertEqual(owner.pending_finalized_observations(owner=coord),())
                        if name == 'plate':self.assertEqual(colour._pending,{})
                        coord.flush_closures();coord.flush_closures()
                        self.assertEqual(sink.call_count,1)
                        self.assertIs(sink.call_args.args[0],saved)
                        self.assertEqual(finalize.call_count,1)
                        self.assertEqual(release.call_count,2)
                        self.assertEqual(other_release.call_count,1)
                        self.assertEqual((plate._pending,colour._pending),({},{}))
                        self.assertEqual(len(coord._completed),0)

    def test_drain_cannot_discard_pending_cleanup(self):
        owner,plate,colour,coord,sink=self.setup_closed(sink=False)
        with patch.object(plate,'release_finalized',side_effect=RuntimeError('injected cleanup failure')):
            with self.assertRaises(RuntimeError):coord.flush_closures()
            saved=coord._completed[1]
            with self.assertRaisesRegex(RuntimeError,'cleanup'):coord.drain_completed()
            self.assertIs(coord._completed[1],saved)
        coord.flush_closures()
        self.assertEqual(coord.drain_completed(),(saved,))
        self.assertEqual(coord.drain_completed(),())

    def test_partial_classification_lifetime_release_retries_without_duplicate_sink(self):
        owner,plate,colour,coord,sink=self.setup_closed()
        lifecycle=owner.pipeline.lifecycle
        release=self.fail_once(lifecycle.release_closed,after=True)
        with patch.object(lifecycle,'release_closed',release):
            with self.assertRaises(RuntimeError):coord.flush_closures()
            self.assertTrue(lifecycle.is_released(1))
            self.assertEqual(len(owner.pending_finalized_observations(owner=coord)),1)
            coord.flush_closures()
        self.assertEqual(sink.call_count,1)
        self.assertEqual(owner.pending_finalized_observations(owner=coord),())
        owner.acknowledge_finalized_observations((1,),owner=coord)
        with self.assertRaises(ValueError):owner.acknowledge_finalized_observations((999,),owner=coord)

    def test_repeated_consumer_release_is_idempotent_and_rejects_unknown(self):
        owner,plate,colour,coord,sink=self.setup_closed()
        coord.flush_closures()
        for consumer in (colour,plate):
            consumer.release_finalized(1,owner=coord)
            consumer.release_finalized(1,owner=coord)
            for unknown in (0,999):
                with self.assertRaises(ValueError):consumer.release_finalized(unknown,owner=coord)
            with self.assertRaises(RuntimeError):consumer.release_finalized(1)

    def test_sink_rejection_retains_evidence_and_blocks_drain(self):
        owner,plate,colour,coord,sink=self.setup_closed()
        sink.side_effect=RuntimeError('sink rejected')
        with self.assertRaises(RuntimeError):coord.flush_closures()
        saved=coord._completed[1]
        with self.assertRaisesRegex(RuntimeError,'cleanup'):coord.drain_completed()
        self.assertEqual(len(owner.pending_finalized_observations(owner=coord)),1)
        sink.side_effect=None
        coord.flush_closures()
        self.assertEqual(sink.call_count,2)
        self.assertIs(sink.call_args.args[0],saved)
        self.assertEqual(len(coord._completed),0)


if __name__ == '__main__':
    unittest.main()
