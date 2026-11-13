"""Opt-in native console consumer proof, no video/model execution."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from vehicle.common.console_runs import atomic_json
from vehicle.common.console_stack import database_probe
from vehicle.common.mohamed_lpr_redis_runtime import OwnedRedis
from vehicle.common.real_video.test_database import require_test_target
from vehicle.common.redis_scalar.worker import load_request_codec
from vehicle.common.redis_scalar.synthetic import ORG, IDENTITY_NAMESPACE
from vehicle.common.redis_scalar.journal import Journal
from vehicle.common.tests.test_db1_consumer import delivery_fixture
from scripts.vehicle_console_db1 import consume


class ConsoleDB1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_test_target()
        database_probe()

    def test_existing_consumer_commits_receipts_before_real_ack_and_removes_fixture(self):
        executable = os.environ.get('T1_REDIS_SERVER', Path(__file__).resolve().parents[4]/'redis-native-validation/redis-8.2.11/src/redis-server')
        codec = load_request_codec(ORG, IDENTITY_NAMESPACE)
        raw = delivery_fixture(codec)
        with tempfile.TemporaryDirectory() as name, OwnedRedis(executable) as owned:
            directory = Path(name)
            journal = Journal(owned.client, namespace='console-native-test', trusted_org=ORG,
                              role='aggregator_plate', consumer='test')
            owned.client.xadd(journal.key('db1.delivery.v1'), {'json': raw}, id='1-0')
            atomic_json(directory/'db1-control.json', dict(org=str(ORG), identity_namespace=str(IDENTITY_NAMESPACE),
                redis_socket=str(owned.socket), namespace='console-native-test', deliveries=1,
                source_media=dict(sha256='b'*64, bytes=17, width=160, height=100)))
            consume(directory)
            progress = json.loads((directory/'db1-progress.json').read_text())
            self.assertEqual(progress['status'], 'completed')
            self.assertEqual(progress['pending'], 0)
            self.assertEqual(progress['lag'], 0)
            evidence, = progress['evidence']
            self.assertTrue(evidence['exact_entry_pending'])
            self.assertEqual(evidence['before_ack']['acceptances'], 1)
            self.assertEqual(evidence['before_ack']['row_receipts'], 3)
            self.assertEqual(evidence['ack_reply'], 1)
            self.assertEqual(evidence['before_ack']['source_media'], dict(sha256='b'*64, bytes=17, width=160, height=100))
            self.assertEqual(progress['cleanup'], [{'owned_database_and_roles_removed': True}])
            self.assertEqual(list(progress['tracks'].values()), [dict(postgres_commit='committed', redis_ack='acknowledged')])

    def test_failed_independent_check_keeps_confirmed_commit_and_does_not_ack(self):
        executable = os.environ.get('T1_REDIS_SERVER', Path(__file__).resolve().parents[4]/'redis-native-validation/redis-8.2.11/src/redis-server')
        codec = load_request_codec(ORG, IDENTITY_NAMESPACE)
        raw = delivery_fixture(codec)
        with tempfile.TemporaryDirectory() as name, OwnedRedis(executable) as owned:
            directory = Path(name)
            journal = Journal(owned.client, namespace='console-failed-check', trusted_org=ORG,
                              role='aggregator_plate', consumer='test')
            owned.client.xadd(journal.key('db1.delivery.v1'), {'json': raw}, id='1-0')
            atomic_json(directory/'db1-control.json', dict(org=str(ORG), identity_namespace=str(IDENTITY_NAMESPACE),
                redis_socket=str(owned.socket), namespace='console-failed-check', deliveries=1))
            with patch('vehicle.common.mohamed_lpr_test_database.OwnedDeliveryDatabase.inspect',
                       side_effect=RuntimeError('injected independent check failure')):
                with self.assertRaises(RuntimeError):consume(directory)
            progress = json.loads((directory/'db1-progress.json').read_text())
            self.assertEqual(progress['status'], 'failed')
            self.assertEqual(list(progress['tracks'].values()), [dict(postgres_commit='committed', redis_ack='pending')])
            self.assertEqual(owned.client.xpending(journal.key('db1.delivery.v1'), 'db1-natural-v1')['pending'], 1)
            self.assertEqual(next(directory.glob('db1-wire-*.json')).read_bytes(), raw)
