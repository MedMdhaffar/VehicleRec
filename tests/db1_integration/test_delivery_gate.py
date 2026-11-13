"""T1 boundary evidence: real PG16 transactions and owned native Redis ACKs.

Recognition is injected. Provisioning and cleanup reuse the reviewed P4 fixture.
Faults occur after actual SQL work/commit or at the actual Redis transport call.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql

import test_natural_consumer as native
from vehicle.common.db1_adapter import DatabaseDeliveryError, DeliveryConflict, TrustedTenant
from vehicle.common.db1_consumer import NaturalConsumer, RedisDB1Consumer
from vehicle.common.db1_context import CameraRegistration, RunContext
from vehicle.common.real_video.db_delivery import TargetDeliveries
from vehicle.common.real_video.runtime import PrivateRuntime
from vehicle.common.redis_scalar.journal import Journal
from vehicle.common.redis_scalar.plate_codec import join_bodies
from vehicle.common.redis_scalar.synthetic import ORG, IDENTITY_NAMESPACE
from vehicle.common.redis_scalar.worker import load_request_codec
from vehicle.common.tests.test_db1_consumer import delivery_fixture, supplied_test_registrations
from vehicle.common.tests.test_db1_runner_ack import FaultClient


TABLES = ('public.fact_vehicle', 'public.fact_vehicle_colour', 'public.fact_plate_read',
          'public.fact_vehicle_make', 'db1_delivery.acceptance', 'db1_delivery.row_receipt')


class DeliveryGateTests(unittest.TestCase):
    setUpClass = classmethod(native.NaturalPostgresTests.setUpClass.__func__)
    cleanup_resources = classmethod(native.NaturalPostgresTests.cleanup_resources.__func__)
    seed_registrations = classmethod(native.NaturalPostgresTests.seed_registrations.__func__)
    connect = classmethod(native.NaturalPostgresTests.connect.__func__)
    setUp = native.NaturalPostgresTests.setUp

    @contextmanager
    def broker(self, raws=None, mode='none'):
        binary = os.environ.get('VEHICLE_REDIS_SERVER')
        if not binary or not Path(binary).is_file():
            self.skipTest('explicit existing native Redis binary required')
        ns = 't1gate' + uuid4().hex[:16]
        with tempfile.TemporaryDirectory(prefix='waycon-t1-gate-') as out:
            runtime = PrivateRuntime(binary, Path(out), namespace=ns, org=ORG,
                                     identity_namespace=IDENTITY_NAMESPACE, delivery_version='v2')
            runtime.roles = ()  # This boundary fixture seeds only final scalar deliveries.
            with runtime:
                journal = Journal(None, namespace=ns, trusted_org=ORG,
                                  role='plate_requests', consumer='native-t1')
                key = journal.key('db1.delivery.v1')
                for raw in raws if raws is not None else (self.raw,):
                    runtime.client.xadd(key, {'json': raw})
                db = RedisDB1Consumer(runtime.client, journal, self.consumer, name='restricted-t1')
                db.client = FaultClient(runtime.client, mode)
                yield runtime, db, key, runtime.client.xrange(key)
            self.assertFalse(runtime.cleanup['live_pids'])
            self.assertTrue(runtime.cleanup['temporary_directory_removed'])

    def snapshot(self, org=None):
        # Independent normal-role connection; only counts/hashes enter evidence.
        result = {}
        with self.connect() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("SELECT set_config('app.org_id',%s,true)", (str(org or self.tenant.org_id),))
            for table in TABLES:
                cur.execute(sql.SQL("SELECT count(*),md5(COALESCE(string_agg(j,'|' ORDER BY j),'')) "
                                    "FROM (SELECT to_jsonb(t)::text AS j FROM {} t) s").format(
                                        sql.Identifier(*table.split('.'))))
                result[table] = list(cur.fetchone())
        return result

    def counts(self, expected):
        self.assertEqual([v[0] for v in self.snapshot().values()], expected)

    def evidence(self, case, **data):
        print('T1_NATIVE ' + json.dumps(dict(case=case, real_postgresql=True,
              recognition='injected', **data), sort_keys=True), flush=True)

    def test_success_visible_on_separate_connection_before_real_xack(self):
        boundaries = []
        with self.broker() as (rt, db, key, rows):
            def committed(phase):
                if phase != 'committed':
                    return
                state = self.snapshot()
                self.assertEqual([v[0] for v in state.values()], [1, 1, 1, 0, 1, 3])
                self.assertEqual(rt.client.xpending(key, db.group)['pending'], 1)
                self.assertEqual(db.client.ack_executed, [])
                boundaries.append(dict(independent_connection=state, pending=1, xack_executed=False))
            self.consumer.hook = committed
            self.assertEqual(db.step().disposition, 'new')
            self.assertEqual(rt.client.xpending(key, db.group)['pending'], 0)
            self.assertEqual(len(db.client.ack_executed), 1)
            before = self.snapshot()
            with self.connect() as conn, conn.transaction(), conn.cursor() as cur:
                cur.execute("SELECT set_config('app.org_id',%s,true)", (str(self.tenant.org_id),))
                for table in TABLES[-2:]:
                    for operation in ('UPDATE', 'DELETE'):
                        with self.assertRaises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                            query = ('UPDATE {} SET org_id=org_id' if operation == 'UPDATE' else 'DELETE FROM {}')
                            cur.execute(sql.SQL(query).format(sql.Identifier(*table.split('.'))))
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(len(boundaries), 1)
            self.evidence('success', boundaries=boundaries, final_pending=0, immutable_receipts=True)

    def test_real_sql_rollback_leaves_no_facts_receipts_and_delivery_pending(self):
        original = self.consumer.adapter._apply
        def fail(cursor, entry, payload):
            original(cursor, entry, payload)
            cursor.execute('SELECT 1/0')
        with self.broker() as (rt, db, key, rows):
            with patch.object(self.consumer.adapter, '_apply', side_effect=fail):
                with self.assertRaises(DatabaseDeliveryError):
                    db.step()
            self.counts([0, 0, 0, 0, 0, 0])
            self.assertEqual(rt.client.xpending(key, db.group)['pending'], 1)
            self.assertEqual(db.client.ack_executed, [])
            self.evidence('rollback', independent_connection=self.snapshot(), pending=1, xack_executed=False)
            self.assertEqual(db.step().disposition, 'new')
            self.assertEqual(rt.client.xpending(key, db.group)['pending'], 0)

    def test_lost_real_commit_reply_retries_history_before_real_ack(self):
        adapter = self.consumer.adapter
        class LostReply:
            prepare = adapter.prepare
            failed = False
            def deliver(proxy, *args, **kwargs):
                result = adapter.deliver(*args, **kwargs)
                if not proxy.failed:
                    proxy.failed = True
                    raise DatabaseDeliveryError(None, commit_outcome='unknown')
                return result
        self.consumer.adapter = LostReply()
        with self.broker() as (rt, db, key, rows):
            ledger = TargetDeliveries(db, rows)
            ledger.turn()
            self.counts([1, 1, 1, 0, 1, 3])
            before = self.snapshot()
            self.assertEqual(rt.client.xpending(key, db.group)['pending'], 1)
            self.assertEqual(db.client.ack_executed, [])
            self.assertFalse(ledger.commits)
            self.assertFalse(ledger.done)
            ledger.turn()
            self.assertTrue(ledger.done)
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(ledger.status()['unknown_commits'], 1)
            self.assertEqual(ledger.status()['identical'], 1)
            self.assertEqual(rt.client.xpending(key, db.group)['pending'], 0)
            self.evidence('commit_retry', rows=before, **ledger.status())

    def test_lost_xack_reply_completes_exact_target_without_another_transaction(self):
        connections = []
        def connect():
            connections.append(True)
            return self.connect()
        self.consumer.connection_factory = connect
        with self.broker(raws=(self.raw, self.raw), mode='after_ack') as (rt, db, key, rows):
            ledger = TargetDeliveries(db, rows)
            ledger.turn()
            self.assertEqual(len(connections), 1)
            self.assertEqual(ledger.status()['completed'], 1)
            self.assertEqual(ledger.status()['waiting'], 1)  # second physical input remains unread
            self.assertEqual(rt.client.xpending(key, db.group)['pending'], 0)
            ledger.reconcile()
            self.assertEqual(len(connections), 1)
            self.assertNotIn(rows[1][0], ledger.commits)
            self.assertNotIn(rows[1][0], ledger.completed)
            ledger.turn()
            self.assertTrue(ledger.done)
            self.assertEqual(len(connections), 2)
            self.assertEqual((ledger.status()['new'], ledger.status()['identical']), (1, 1))
            self.assertEqual(ledger.status()['acknowledgements_reconciled'], 1)
            self.counts([1, 1, 1, 0, 1, 3])
            self.evidence('ack_retry', database_connection_scopes=len(connections), **ledger.status())

    def test_pending_and_failed_next_read_cannot_reuse_another_commit(self):
        with self.broker(raws=(self.raw, self.raw), mode='before_next_read') as (rt, db, key, rows):
            ledger = TargetDeliveries(db, rows)
            ledger.turn()
            ledger.turn()
            self.assertIsNone(db.last_delivery)
            self.assertEqual(ledger.status()['transactions_confirmed'], 1)
            self.assertNotIn(rows[1][0], ledger.commits)
            db.client.mode = 'before_ack'
            db.client.failed = False
            ledger.turn()
            self.assertFalse(ledger.done)
            self.assertEqual(ledger.observed[rows[1][0]], 'pending')
            self.assertEqual(rt.client.xpending(key, db.group)['pending'], 1)
            before = self.snapshot()
            ledger.turn()
            self.assertTrue(ledger.done)
            self.assertEqual(self.snapshot(), before)
            self.evidence('pending_binding', **ledger.status())

    def test_identical_then_changed_payload_preserves_original_and_does_not_ack(self):
        body = deepcopy(self.rc.v1.decode(self.raw).body)
        body['closed']['classes'][0]['confidence'] = .81
        bundle = join_bodies(self.rc.v1, body['closed'], body['colour'], body['plate'])
        body.update(bundle=bundle.payload.value(), bundle_sha256=bundle.digest)
        changed = self.rc.v1.make('db1.delivery.v1', body)
        with self.broker(raws=(self.raw, self.raw, changed)) as (rt, db, key, rows):
            self.assertEqual(db.step().disposition, 'new')
            before = self.snapshot()
            self.assertEqual(db.step().disposition, 'identical')
            self.assertEqual(self.snapshot(), before)
            with self.assertRaises(DeliveryConflict):
                db.step()
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(len(db.client.ack_executed), 2)
            pending = rt.client.xpending_range(key, db.group, '-', '+', 10)
            self.assertEqual([p['message_id'] for p in pending], [rows[2][0]])
            self.evidence('conflict', unchanged_rows=before, pending=1, successful_xacks=2)

    def test_two_actual_tenants_restricted_login_forced_rls_and_cross_writes(self):
        rc = load_request_codec(self.other_org, IDENTITY_NAMESPACE)
        # The supervisor schema makes camera_id/valid_from globally unique.
        # Give tenant B its own injected camera before deriving canonical identities.
        with patch('vehicle.common.redis_scalar.synthetic.CameraRegistration',
                   side_effect=lambda org, camera_id, *args: CameraRegistration(org, 't1-tenant-b-camera', *args)), \
             patch('vehicle.common.redis_scalar.synthetic.RunContext',
                   side_effect=lambda org, camera, run_id, *args: RunContext(org, camera, 't1-tenant-b-run', *args)):
            raw = delivery_fixture(rc)
        res = supplied_test_registrations(rc, raw)
        res = replace(res, run=replace(res.run, run_key=53, lane_key=5,
                      camera=replace(res.run.camera, camera_key=42), source_media_key=103),
                      media=tuple(replace(m, media_key=103, camera_key=42,
                                         object_key='tenant-b/input.mp4') for m in res.media))
        r = res.run
        # Explicit B registrations, only in this fixture database. Models and labels are shared.
        with self.setup.cursor() as cur:
            cur.execute('UPDATE public.dim_camera SET camera_id=%s WHERE camera_key=42', (r.camera.camera_id,))
            m = res.media[0]
            cur.execute('INSERT INTO public.media_object(media_key,org_id,camera_key,ts,kind,bucket,object_key,content_type,bytes,width,height,sha256) OVERRIDING SYSTEM VALUE VALUES (%s,%s,42,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                        (103, self.other_org, m.ts, m.kind, m.bucket, m.object_key, m.content_type, m.bytes, m.width, m.height, m.sha256))
            cur.execute('INSERT INTO public.dim_run(run_key,run_id,org_id,lane_key,camera_key,model_key,source_kind,source_media_key,started_at,service_version) OVERRIDING SYSTEM VALUE VALUES (53,%s,%s,5,42,91,%s,103,%s,%s)',
                        (r.run_id, self.other_org, r.source_kind, r.started_at, r.service_version))
        c = rc.v1.decode(raw).life.context
        other = NaturalConsumer(codec=rc.v1, tenant=TrustedTenant(self.other_org, 'fixture tenant B'),
                 trusted_run=c.run_id, trusted_camera=c.camera.camera_id, resolutions=res,
                 connection_factory=self.connect, environment='test')
        self.consumer.process(self.raw, lambda: None)
        other.process(raw, lambda: None)
        policies = []
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT current_user,session_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user')
            self.assertEqual(cur.fetchone(), (self.app_role, self.app_role, False, False))
            cur.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('public','db1_delivery') AND c.relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)")
            self.assertEqual(cur.fetchone(), (0,))
            for table in ('public.dim_camera', 'public.dim_run', 'public.media_object', *TABLES):
                cur.execute('SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass', (table,))
                self.assertEqual(cur.fetchone(), (True, True))
                schema, name = table.split('.')
                cur.execute('SELECT policyname,qual,with_check FROM pg_policies WHERE schemaname=%s AND tablename=%s', (schema, name))
                records = cur.fetchall()
                self.assertEqual(len(records), 1)
                name, using, check = records[0]
                self.assertEqual(name, 'org_isolation')
                self.assertIn('app.org_id', using)
                self.assertIn('app.org_id', check)
                self.assertIn('org_id', using)
                policies.append(dict(table=table, forced=True, using=using, with_check=check))
            for org, foreign in ((self.tenant.org_id, self.other_org), (self.other_org, self.tenant.org_id)):
                with conn.transaction():
                    cur.execute("SELECT set_config('app.org_id',%s,true)", (str(org),))
                    for table, expected in zip(TABLES, (1, 1, 1, 0, 1, 3)):
                        cur.execute(sql.SQL('SELECT count(*),count(*) FILTER (WHERE org_id<>%s) FROM {}').format(sql.Identifier(*table.split('.'))), (org,))
                        self.assertEqual(cur.fetchone(), (expected, 0))
                    row = (self.consumer if org == self.tenant.org_id else other).prepare(self.raw if org == self.tenant.org_id else raw).construction.facts[0].sql.value()
                    row.update(uid=uuid4(), track_id=999)
                    cur.execute("SELECT set_config('app.org_id',%s,true)", (str(foreign),))
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                        cur.execute(sql.SQL('INSERT INTO public.fact_vehicle ({}) VALUES ({})').format(
                            sql.SQL(',').join(map(sql.Identifier, row)), sql.SQL(',').join(sql.Placeholder() for _ in row)), tuple(row.values()))
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                        cur.execute('INSERT INTO db1_delivery.acceptance(org_id,logical_identity,payload,digest) VALUES (%s,%s,%s,%s)', (org, b'forbidden', b'fixture', '0' * 64))
        self.assertEqual([v[0] for v in self.snapshot().values()], [1, 1, 1, 0, 1, 3])
        self.assertEqual([v[0] for v in self.snapshot(self.other_org).values()], [1, 1, 1, 0, 1, 3])
        self.evidence('tenant_isolation', tenants=2, effective_login_is_application_role=True,
                      superuser=False, bypassrls=False, table_owner=False, policies=policies,
                      own_rows_visible=True, foreign_rows_visible=0, cross_writes_rejected=True)


if __name__ == '__main__':
    unittest.main()
