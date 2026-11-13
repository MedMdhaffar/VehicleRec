"""Lease budget regressions; injected clients only, no connection or payload logs."""
import unittest
from unittest.mock import Mock
from vehicle.common.redis_scalar.worker import Worker
from vehicle.common.redis_scalar.journal import Journal
from vehicle.common.redis_scalar.synthetic import ORG


class LeaseBudgetTests(unittest.TestCase):
    def worker(self):
        client=Mock();client.xautoclaim.return_value=[b'0-0',[],[]]
        client.xreadgroup.return_value=[]
        w=Worker(client,codec=Mock(org=ORG),namespace='lease-test',role='aggregator_plate',consumer='c')
        w.journal.token=b't';w.journal.renew=Mock();w.journal.repair=Mock(return_value=False)
        w.process=Mock()
        return w,client

    def test_polling_never_blocks_while_holding_partition_lease(self):
        w,client=self.worker();self.assertFalse(w.step())
        self.assertEqual(client.xreadgroup.call_count,3)
        for call in client.xreadgroup.call_args_list:
            self.assertIsNone(call.kwargs.get('block')) # BLOCK 0 would wait forever
        self.assertEqual(client.xautoclaim.call_count,3)

    def test_expiry_after_poll_is_checked_before_processing(self):
        w,client=self.worker()
        client.xautoclaim.return_value=[b'0-0',[(b'1-0',{b'json':b'{}'})],[]]
        w.journal.renew.side_effect=[None,RuntimeError('STALE_OWNER')]
        with self.assertRaisesRegex(RuntimeError,'STALE_OWNER'):w.step()
        w.process.assert_not_called()

    def test_serialization_cannot_carry_expired_owner_into_prepare(self):
        client=Mock();client.hget.return_value=b'0'
        j=Journal(client,namespace='lease-test',trusted_org=ORG,role='plate',consumer='c')
        j.token=b'expired';j.renew=Mock(side_effect=RuntimeError('STALE_OWNER'));j.call=Mock()
        with self.assertRaisesRegex(RuntimeError,'STALE_OWNER'):
            j.handoff(source='engine.evidence.plate',entry_id=b'1-0',marker='test',digest='a'*64)
        j.call.assert_not_called()

    def test_operating_defaults_keep_plate_processing_budget_separate_from_expiry_tests(self):
        for role,expected in [('colour',2000),('aggregator',2000),('plate',60000),('aggregator_plate',60000)]:
            w=Worker(Mock(),codec=Mock(org=ORG),namespace='lease-test',role=role,consumer='c')
            self.assertEqual(w.journal.lease_ms,expected)
            short=Worker(Mock(),codec=Mock(org=ORG),namespace='lease-test',role=role,consumer='c',lease_ms=300)
            self.assertEqual(short.journal.lease_ms,300)
