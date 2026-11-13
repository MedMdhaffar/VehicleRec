"""Gated natural Redis→PG16 consumer validation using the owned P4 fixture.

Absent explicit disposable target configuration: SkipTest BEFORE any connection.
The supplied admin DSN provisions only; analytical operations use normal logins.
"""
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import os,unittest
from pathlib import Path

import test_postgres as fixture_module
from vehicle.common.db1_consumer import NaturalConsumer,RedisDB1Consumer
from vehicle.common.db1_adapter import TrustedTenant,DB1Adapter,DatabaseDeliveryError,DeliveryConflict
from vehicle.common.redis_scalar.worker import load_request_codec
from vehicle.common.redis_scalar.synthetic import ORG,IDENTITY_NAMESPACE
from vehicle.common.tests.test_db1_consumer import delivery_fixture,supplied_test_registrations


class NaturalPostgresTests(unittest.TestCase):
    setUpClass=classmethod(fixture_module.PostgreSQL16Tests.setUpClass.__func__)
    cleanup_resources=classmethod(fixture_module.PostgreSQL16Tests.cleanup_resources.__func__)
    connect=classmethod(fixture_module.PostgreSQL16Tests.connect.__func__)
    count=fixture_module.PostgreSQL16Tests.count

    @classmethod
    def seed_registrations(cls):
        cls.rc=load_request_codec(ORG,IDENTITY_NAMESPACE);cls.raw=delivery_fixture(cls.rc)
        cls.res=supplied_test_registrations(cls.rc,cls.raw)
        cls.res=replace(cls.res,run=replace(cls.res.run,lane_key=5))
        cls.tenant=TrustedTenant(ORG,'owned disposable normal-role consumer fixture')
        fixture_module.PostgreSQL16Tests.seed_registrations.__func__(cls)
        with cls.setup.cursor() as cur:
            cur.execute('UPDATE public.dim_colour SET name=%s WHERE colour_key=21',('red',))
            cur.execute('UPDATE public.dim_plate_category SET name=%s WHERE plate_category_key=31',('private',))

    def setUp(self):
        with self.setup.cursor() as cur:
            cur.execute('TRUNCATE public.fact_vehicle,public.fact_vehicle_colour,public.fact_plate_read,public.fact_vehicle_make,db1_delivery.acceptance,db1_delivery.row_receipt')
        self.connection=self.connect();self.addCleanup(self.connection.close)
        c=self.rc.v1.decode(self.raw).life.context
        self.consumer=NaturalConsumer(codec=self.rc.v1,tenant=self.tenant,trusted_run=c.run_id,trusted_camera=c.camera.camera_id,
            resolutions=self.res,connection_factory=self.connect,environment='test')

    def test_commit_then_worker_death_before_xack_and_reconnect(self):
        ack=[]
        def death(phase):
            if phase=='committed':raise RuntimeError('injected process exit before XACK')
        self.consumer.hook=death
        with self.assertRaises(RuntimeError):self.consumer.process(self.raw,lambda:ack.append(True))
        self.assertEqual(ack,[]);self.assertEqual(self.count('acceptance'),1)
        self.consumer.hook=lambda phase:None
        self.assertEqual(self.consumer.process(self.raw,lambda:ack.append(True)).disposition,'identical')
        self.assertEqual(len(ack),1);self.assertEqual(self.count('fact_plate_read'),1)

    def test_commit_then_lost_reply_reconciles_database_history(self):
        adapter=self.consumer.adapter
        class LostReply:
            prepare=adapter.prepare
            failed=False
            def deliver(proxy,*args,**kwargs):
                result=adapter.deliver(*args,**kwargs)
                if not proxy.failed:proxy.failed=True;raise DatabaseDeliveryError(None,commit_outcome='unknown')
                return result
        self.consumer.adapter=LostReply();ack=[]
        with self.assertRaises(DatabaseDeliveryError):self.consumer.process(self.raw,lambda:ack.append(True))
        self.assertEqual(ack,[])
        self.assertEqual(self.consumer.process(self.raw,lambda:ack.append(True)).disposition,'identical')
        self.assertEqual(self.count('row_receipt'),3)

    def test_concurrent_identical_retries_parent_child_atomicity_and_normal_rls(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.consumer.process(self.raw,lambda:None).disposition,range(2)))
        self.assertEqual(sorted(results),['identical','new'])
        self.assertEqual([self.count(t) for t in ('fact_vehicle','fact_vehicle_colour','fact_plate_read','acceptance','row_receipt')],[1,1,1,1,3])
        with self.connection.cursor() as cur:
            cur.execute('SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user')
            self.assertEqual(cur.fetchone(),(False,False))
            cur.execute("SELECT set_config('app.org_id',%s,false)",(str(self.other_org),))
            for table in ('fact_vehicle','fact_vehicle_colour','fact_plate_read','db1_delivery.acceptance','db1_delivery.row_receipt'):
                cur.execute('SELECT count(*) FROM '+table);self.assertEqual(cur.fetchone(),(0,))

    def test_native_mp4_request_loop_redis_delivery_xack_after_real_commit(self):
        from vehicle.common.real_video.runtime import PrivateRuntime
        from vehicle.common.redis_scalar.journal import Journal
        from vehicle.common.real_video.plate_host import drive_video
        from vehicle.common.plate_requests.host import V2Publisher
        from vehicle.common.tests import test_mp4_colour_plate as injected
        from vehicle.common.tests.test_redis_scalar_integration import eventually
        from psycopg import sql
        import tempfile
        binary=os.environ.get('VEHICLE_REDIS_SERVER')
        if not binary or not Path(binary).is_file():self.skipTest('explicit existing native Redis binary required')
        owner=injected.CombinedVideoTests('runTest');owner.setUpClass();owner.setUp();self.addCleanup(owner.doCleanups)
        engine=owner.engine();ns='pgconsumer'+uuid4().hex[:12]
        with tempfile.TemporaryDirectory() as out:
            with PrivateRuntime(binary,Path(out),namespace=ns,org=ORG,identity_namespace=IDENTITY_NAMESPACE,delivery_version='v2') as runtime:
                journal=Journal(None,namespace=ns,trusted_org=ORG,role='plate_requests',consumer='pg-consumer')
                publisher=V2Publisher(runtime.client,journal,self.rc,engine)
                drive_video(owner.source(3),engine,publisher)
                eventually(lambda:runtime.client.xlen(journal.key('db1.delivery.v1'))==1)
                raw=runtime.client.xrange(journal.key('db1.delivery.v1'))[0][1][b'json']
                res=supplied_test_registrations(self.rc,raw)
                res=replace(res,run=replace(res.run,lane_key=5),models=tuple(replace(v,key=201+i) for i,v in enumerate(res.models)))
                # Explicit synthetic registrations, only inside this fixture's
                # disposable database. No model identity is invented by consumer.
                with self.setup.cursor() as cur:
                    for model in res.models:
                        cur.execute('INSERT INTO public.dim_model(model_key,model_name,version,task) OVERRIDING SYSTEM VALUE VALUES (%s,%s,%s,%s)',
                            (model.key,model.identity.model_name,model.identity.version,model.identity.task))
                c=self.rc.v1.decode(raw).life.context
                consumer=NaturalConsumer(codec=self.rc.v1,tenant=self.tenant,trusted_run=c.run_id,trusted_camera=c.camera.camera_id,
                    resolutions=res,connection_factory=self.connect,environment='test')
                db=RedisDB1Consumer(runtime.client,journal,consumer,name='normal-role')
                def committed(phase):
                    if phase=='committed':self.assertEqual(runtime.client.xpending(journal.key('db1.delivery.v1'),db.group)['pending'],1)
                consumer.hook=committed
                counts=dict(engine.audit.calls);result=db.step()
                self.assertEqual(runtime.client.xpending(journal.key('db1.delivery.v1'),db.group)['pending'],0)
                self.assertEqual(self.count('fact_plate_read'),1)
                self.assertEqual(engine.audit.calls,counts)
                self.assertEqual(owner.open_count,1)
            self.assertFalse(runtime.cleanup['live_pids']);self.assertTrue(runtime.cleanup['temporary_directory_removed'])

    def test_consumer_rollback_and_missing_registration_do_not_ack_or_write(self):
        from vehicle.common.db1_adapter import ReferenceRejected
        from vehicle.common.db1_rows.core import Blocked
        from unittest.mock import patch
        import psycopg
        ack=[];original=self.consumer.adapter._apply
        def rollback(cursor,entry,payload):
            original(cursor,entry,payload)
            cursor.execute('SELECT 1/0') # actual PG error after parent/children/receipts
        with patch.object(self.consumer.adapter,'_apply',side_effect=rollback):
            with self.assertRaises(DatabaseDeliveryError):self.consumer.process(self.raw,lambda:ack.append(True))
        self.assertEqual(ack,[])
        for table in ('fact_vehicle','fact_vehicle_colour','fact_plate_read','acceptance','row_receipt'):self.assertEqual(self.count(table),0)
        self.consumer.resolutions=replace(self.res,models=())
        with self.assertRaises(Blocked):self.consumer.process(self.raw,lambda:ack.append(True))
        self.assertEqual(ack,[])

    def test_changed_accepted_provenance_and_normal_role_cross_org_write(self):
        from copy import deepcopy
        from psycopg import sql
        import psycopg
        from vehicle.common.redis_scalar.plate_codec import unpack_transcript,claims,join_bodies
        from vehicle.common.plate_metadata import PlateMetadata
        m=self.rc.v1.decode(self.raw);body=deepcopy(m.body)
        self.consumer.process(self.raw,lambda:None)
        body['plate']['transcript']['candidates'][0]['detector_confidence']=.85
        result=PlateMetadata().evaluate(m.life,unpack_transcript(body['plate']['transcript'],m.life)).result
        body['plate'].update(claims=claims(result),provenance_sha256=result.provenance.digest)
        bundle=join_bodies(self.rc.v1,body['closed'],body['colour'],body['plate'])
        self.assertEqual(bundle.payload.value()['facts'],m.body['bundle']['facts'])
        body.update(bundle=bundle.payload.value(),bundle_sha256=bundle.digest)
        with self.assertRaises(DeliveryConflict):self.consumer.process(self.rc.v1.make('db1.delivery.v1',body),lambda:self.fail('conflict must not ACK'))
        row=self.consumer.prepare(self.raw).construction.facts[0].sql.value();row.update(uid=uuid4(),track_id=999)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with self.connection.transaction():
                with self.connection.cursor() as cur:
                    cur.execute("SELECT set_config('app.org_id',%s,true)",(str(self.other_org),))
                    cur.execute(sql.SQL('INSERT INTO public.fact_vehicle ({}) VALUES ({})').format(
                        sql.SQL(',').join(map(sql.Identifier,row)),sql.SQL(',').join(sql.Placeholder() for _ in row)),tuple(row.values()))
        self.assertEqual(self.count('fact_vehicle'),1)

    def test_commit_before_ack_survives_owned_redis_sigkill_restart(self):
        from vehicle.common.real_video.persistent import PersistentRuntime
        from vehicle.common.redis_scalar.journal import Journal
        from vehicle.common.tests.test_redis_scalar_integration import eventually
        import tempfile
        binary=os.environ.get('VEHICLE_REDIS_SERVER')
        if not binary or not Path(binary).is_file():self.skipTest('explicit existing native Redis binary required')
        ns='pgrestart'+uuid4().hex[:16]
        with tempfile.TemporaryDirectory() as out:
            with PersistentRuntime(binary,Path(out),namespace=ns,org=ORG,identity_namespace=IDENTITY_NAMESPACE,workers=False) as runtime:
                journal=Journal(None,namespace=ns,trusted_org=ORG,role='plate_requests',consumer='db')
                runtime.client.xadd(journal.key('db1.delivery.v1'),{'json':self.raw})
                db=RedisDB1Consumer(runtime.client,journal,self.consumer,name='normal-role')
                def death(phase):
                    if phase=='committed':runtime.stop_server(abrupt=True);raise RuntimeError('injected death after actual PG commit before XACK')
                self.consumer.hook=death
                with self.assertRaises(RuntimeError):db.step()
                self.assertEqual(self.count('acceptance'),1)
                runtime.start_server();self.consumer.hook=lambda phase:None
                self.assertEqual(runtime.client.xpending(journal.key('db1.delivery.v1'),db.group)['pending'],1)
                self.assertEqual(db.step().disposition,'identical')
                self.assertEqual(runtime.client.xpending(journal.key('db1.delivery.v1'),db.group)['pending'],0)
                self.assertEqual([self.count(t) for t in ('fact_vehicle','fact_vehicle_colour','fact_plate_read','acceptance','row_receipt')],[1,1,1,1,3])
                runtime.declare_complete()
            self.assertTrue(runtime.cleanup['data_directory_removed'])
