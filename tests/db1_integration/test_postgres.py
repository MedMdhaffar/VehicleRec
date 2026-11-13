"""Opt-in PG16 tests using only fresh disposable databases and roles."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields,is_dataclass,replace
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
import unittest
from uuid import UUID,uuid4
from zipfile import ZipFile

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from vehicle.common.db1_adapter import (DB1Adapter,TrustedTenant,InvalidAcceptance,
    ReferenceRejected,DeliveryConflict,HistoryMismatch,DatabaseDeliveryError,check_manufacturer_reference)
from vehicle.common.db1_context import RunContext
from vehicle.common.db1_outbox import LocalOutbox
from vehicle.common.db1_rows import Dimension
from vehicle.common.tests.test_db1_rows import fixture,ROOT,NAMESPACE
from vehicle.common.tests.test_db1_outbox import validation


def recontext(value,context):
    if isinstance(value,RunContext):return context
    if is_dataclass(value):return replace(value,**{f.name:recontext(getattr(value,f.name),context) for f in fields(value)})
    if isinstance(value,tuple):return tuple(recontext(v,context) for v in value)
    return value


def provisioning_params(environ):
    isolated=environ.get('DB1_P4_ISOLATED')=='1'
    existing=environ.get('DB1_P4_EXISTING_SERVER')=='1'
    if isolated and existing:
        raise RuntimeError('choose exactly one P4 provisioning mode')
    if not (isolated or existing) or not environ.get('DB1_P4_ADMIN_DSN'):
        raise unittest.SkipTest('explicit P4 provisioning mode and private administrator connection required')
    try:
        params=conninfo_to_dict(environ['DB1_P4_ADMIN_DSN'])
    except Exception:
        raise RuntimeError('invalid private P4 connection parameters; details suppressed') from None
    allowed=('postgres','intern') if existing else ('postgres',)
    if params.get('host') not in ('127.0.0.1','localhost','::1') or params.get('dbname') not in allowed:
        raise RuntimeError('P4 requires local TCP and an allowed provisioning database')
    # Admin session only: suppress statement/parameter logging without changing
    # server configuration. Password-bearing connection options remain private.
    return {**params,'connect_timeout':'5',
            'options':'-c log_statement=none -c log_min_error_statement=panic -c log_parameter_max_length=0 -c log_parameter_max_length_on_error=0'}


class PostgreSQL16Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin_params=provisioning_params(os.environ)
        cls.admin=psycopg.connect(**cls.admin_params,autocommit=True)
        cls.addClassCleanup(cls.admin.close)
        if not 160000<=cls.admin.info.server_version<170000:
            raise RuntimeError('fixture requires PostgreSQL 16')
        with cls.admin.cursor() as cur:
            cur.execute('SELECT rolsuper FROM pg_roles WHERE rolname=current_user')
            if cur.fetchone()!=(True,):raise RuntimeError('fixture administration requires provisioning superuser; application operations use a separate normal role')
        suffix=uuid4().hex
        cls.db,cls.owner_role,cls.app_role=['db1p4_'+label+'_'+suffix for label in ('db','owner','app')]
        cls.created=[];cls.addClassCleanup(cls.cleanup_resources)
        cls.password=secrets.token_urlsafe(32)
        with cls.admin.cursor() as cur:
            cur.execute(sql.SQL('CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS').format(sql.Identifier(cls.owner_role)))
            cls.created.append(('role',cls.owner_role))
            verifier=cls.admin.pgconn.encrypt_password(cls.password.encode(),cls.app_role.encode(),b'scram-sha-256').decode()
            cur.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD {}').format(sql.Identifier(cls.app_role),sql.Literal(verifier)))
            cls.created.append(('role',cls.app_role))
            cur.execute(sql.SQL('CREATE DATABASE {} OWNER {} TEMPLATE template0').format(sql.Identifier(cls.db),sql.Identifier(cls.owner_role)))
            cls.created.append(('database',cls.db))
        cls.setup=psycopg.connect(**{**cls.admin_params,'dbname':cls.db},autocommit=True)
        cls.addClassCleanup(cls.setup.close)
        cls.application_params={**cls.admin_params,'dbname':cls.db,'user':cls.app_role,'password':cls.password}
        # Normal logins cannot set the superuser-only admin logging options.
        cls.application_params.pop('options',None)
        cls.adapter=DB1Adapter(validation=validation())
        with cls.setup.cursor() as cur:
            cur.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(cls.owner_role)))
            with ZipFile(ROOT/'db1_contract.zip') as archive:
                for name in ('01_shared.sql','02_vehicle.sql'):
                    cur.execute(archive.read('db1_contract/'+name).decode())
            # 03_dev_seed is inspected but not applied: unrelated fixed synthetic
            # registrations are replaced by explicit test-only records below.
            cur.execute((Path(__file__).parent/'acceptance_proposal.sql').read_text())
            cur.execute('INSERT INTO db1_delivery.compatibility(metadata) VALUES (%s)',(cls.adapter.compatibility,))
            cur.execute(sql.SQL('GRANT USAGE ON SCHEMA public,db1_delivery TO {}').format(sql.Identifier(cls.app_role)))
            cur.execute(sql.SQL('GRANT SELECT ON ALL TABLES IN SCHEMA public,db1_delivery TO {}').format(sql.Identifier(cls.app_role)))
            cur.execute(sql.SQL('GRANT INSERT ON public.fact_vehicle,public.fact_vehicle_colour,public.fact_plate_read,public.fact_vehicle_make,db1_delivery.acceptance,db1_delivery.row_receipt TO {}').format(sql.Identifier(cls.app_role)))
            cur.execute(sql.SQL('GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {}').format(sql.Identifier(cls.app_role)))
            cur.execute('RESET ROLE')
        cls.completed,cls.res=fixture()
        context=replace(cls.completed.vehicle.lifetime.context,resolution=replace(cls.completed.vehicle.lifetime.context.resolution,lane_key=5))
        cls.completed=recontext(cls.completed,context);cls.res=replace(cls.res,run=replace(cls.res.run,lane_key=5))
        cls.tenant=TrustedTenant(cls.res.run.org_id,'synthetic test authentication authority')
        cls.other_org=UUID(int=2)
        cls.seed_registrations()

    @classmethod
    def cleanup_resources(cls):
        # Only exact random names successfully created by this fixture.
        failed=[]
        for kind,name in reversed(cls.created):
            try:
                with cls.admin.cursor() as cur:
                    if kind=='database':cur.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
                    else:cur.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(name)))
            except psycopg.Error:
                failed.append((kind,name))
        for kind,name in cls.created:
            try:
                with cls.admin.cursor() as cur:
                    cur.execute('SELECT count(*) FROM pg_database WHERE datname=%s' if kind=='database'
                                else 'SELECT count(*) FROM pg_roles WHERE rolname=%s',(name,))
                    if cur.fetchone()!=(0,):failed.append((kind,name))
            except psycopg.Error:
                failed.append((kind,name))
        if failed:
            raise RuntimeError('scoped P4 cleanup failed for '+repr(failed)) from None

    @classmethod
    def seed_registrations(cls):
        r=cls.res.run;c=r.camera;clip=cls.res.media[0]
        with cls.setup.cursor() as cur:
            cur.execute('INSERT INTO public.dim_org(org_id,name) VALUES (%s,%s),(%s,%s)',(r.org_id,'synthetic fixture A',cls.other_org,'synthetic fixture B'))
            cur.execute('INSERT INTO public.dim_camera(camera_key,camera_id,org_id,timezone,valid_from) OVERRIDING SYSTEM VALUE VALUES (%s,%s,%s,%s,%s),(%s,%s,%s,%s,%s)',
                        (41,c.camera_id,r.org_id,c.timezone,c.valid_from,42,'synthetic other camera',cls.other_org,c.timezone,c.valid_from))
            for m in [r.detector,*[type('Model',(),dict(model_key=v.key,model_name=v.identity.model_name,version=v.identity.version,task=v.identity.task)) for v in cls.res.models]]:
                cur.execute('INSERT INTO public.dim_model(model_key,model_name,version,task) OVERRIDING SYSTEM VALUE VALUES (%s,%s,%s,%s)',(m.model_key,m.model_name,m.version,m.task))
            cur.execute("INSERT INTO public.dim_class(class_key,name,class_group) OVERRIDING SYSTEM VALUE VALUES (11,'car','vehicle')")
            cur.execute("INSERT INTO public.dim_colour(colour_key,name) OVERRIDING SYSTEM VALUE VALUES (21,'silver/gray')")
            cur.execute("INSERT INTO public.dim_plate_category(plate_category_key,name) OVERRIDING SYSTEM VALUE VALUES (31,'foreign')")
            cur.execute('INSERT INTO public.media_object(media_key,org_id,camera_key,ts,kind,bucket,object_key,content_type,bytes,width,height,sha256) OVERRIDING SYSTEM VALUE VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                        (clip.media_key,clip.org_id,clip.camera_key,clip.ts,clip.kind,clip.bucket,clip.object_key,clip.content_type,clip.bytes,clip.width,clip.height,clip.sha256))
            cur.execute('INSERT INTO public.dim_run(run_key,run_id,org_id,lane_key,camera_key,model_key,source_kind,source_media_key,started_at,service_version) OVERRIDING SYSTEM VALUE VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                        (r.run_key,r.run_id,r.org_id,r.lane_key,c.camera_key,r.detector.model_key,r.source_kind,r.source_media_key,r.started_at,r.service_version))
            cur.execute("INSERT INTO public.dim_run(run_key,run_id,org_id,lane_key,camera_key,model_key,source_kind,started_at) OVERRIDING SYSTEM VALUE VALUES (52,'synthetic other run',%s,5,41,91,'stream',%s)",(r.org_id,r.started_at))

    def setUp(self):
        with self.setup.cursor() as cur:
            cur.execute('TRUNCATE public.fact_vehicle,public.fact_vehicle_colour,public.fact_plate_read,public.fact_vehicle_make,db1_delivery.acceptance,db1_delivery.row_receipt')
            cur.execute('DELETE FROM public.media_object WHERE media_key=102')
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=LocalOutbox(path=self.temp.name+'/outbox',namespace=NAMESPACE,validation=validation());self.addCleanup(self.store.close)
        self.entry=self.store.accept(self.completed,self.res).entry
        self.connection=self.connect();self.addCleanup(self.connection.close)

    @classmethod
    def connect(cls):return psycopg.connect(**cls.application_params,autocommit=True)

    def deliver(self,entry=None,connection=None,res=None,tenant=None):
        return self.adapter.deliver(connection or self.connection,entry or self.entry,tenant=tenant or self.tenant,resolutions=res or self.res)

    def count(self,table):
        with self.connection.transaction():
            with self.connection.cursor() as cur:
                cur.execute("SELECT set_config('app.org_id',%s,true)",(str(self.tenant.org_id),))
                cur.execute(sql.SQL('SELECT count(*) FROM {}.{}').format(sql.Identifier('db1_delivery' if table in ('acceptance','row_receipt') else 'public'),sql.Identifier(table)))
                return cur.fetchone()[0]

    def test_parent_colour_plate_and_omissions(self):
        self.assertEqual(self.deliver().disposition,'new')
        self.assertEqual([self.count(t) for t in ('fact_vehicle','fact_vehicle_colour','fact_plate_read','fact_vehicle_make')],[1,1,1,0])
        self.assertEqual(self.count('acceptance'),1);self.assertEqual(self.count('row_receipt'),3)

    def test_identical_after_connection_reopen(self):
        self.deliver();self.connection.close();self.connection=self.connect();self.addCleanup(self.connection.close)
        self.assertEqual(self.deliver().disposition,'identical');self.assertEqual(self.count('fact_vehicle'),1)

    def other_entry(self,confidence):
        with LocalOutbox(path=self.temp.name+'/'+uuid4().hex,namespace=NAMESPACE,validation=validation()) as store:
            return store.accept(replace(self.completed,vehicle=replace(self.completed.vehicle,type_confidence=confidence)),self.res).entry

    def test_same_uid_changed_payload_and_real_rounding(self):
        a=self.other_entry(Decimal('0.12345678901234567890123456781'))
        b=self.other_entry(Decimal('0.12345678901234567890123456782'))
        self.deliver(a)
        with self.connection.transaction(),self.connection.cursor() as cur:
            cur.execute("SELECT set_config('app.org_id',%s,true)",(str(self.tenant.org_id),))
            cur.execute('SELECT class_conf FROM public.fact_vehicle')
            actual=cur.fetchone()[0]
            self.assertNotEqual(Decimal(str(actual)),Decimal('0.12345678901234567890123456781'))
        self.assertEqual(self.deliver(a).disposition,'identical')
        with self.assertRaises(DeliveryConflict):self.deliver(b)

    def concurrent(self,a,b):
        barrier=threading.Barrier(2)
        def submit(entry):
            with self.connect() as conn:
                barrier.wait(timeout=5)
                try:return self.deliver(entry,connection=conn).disposition
                except DeliveryConflict:return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(submit,e) for e in (a,b)]
            return sorted(f.result(timeout=20) for f in futures)

    def test_concurrent_identical_first_delivery(self):
        self.assertEqual(self.concurrent(self.entry,self.entry),['identical','new'])
        self.assertEqual(self.count('acceptance'),1)

    def test_concurrent_conflicting_first_delivery(self):
        self.assertEqual(self.concurrent(self.entry,self.other_entry(Decimal('.7'))),['conflict','new'])
        self.assertEqual(self.count('acceptance'),1)

    def test_missing_context_cross_tenant_and_connection_reuse(self):
        with self.assertRaises(InvalidAcceptance):self.adapter.deliver(self.connection,self.entry,tenant=None,resolutions=self.res)
        with self.assertRaises(InvalidAcceptance):self.deliver(tenant=TrustedTenant(self.other_org,'other test authority'))
        self.deliver()
        with self.connection.cursor() as cur:
            for table in ('public.fact_vehicle','db1_delivery.acceptance','db1_delivery.row_receipt'):
                cur.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(*table.split('.'))))
                self.assertEqual(cur.fetchone()[0],0)
        with self.connection.transaction(),self.connection.cursor() as cur:
            cur.execute("SELECT set_config('app.org_id',%s,true)",(str(self.other_org),))
            for table in ('public.fact_vehicle','db1_delivery.acceptance','db1_delivery.row_receipt'):
                cur.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(*table.split('.'))))
                self.assertEqual(cur.fetchone()[0],0)
        with self.connection.transaction(),self.connection.cursor() as cur:
            cur.execute("SELECT set_config('app.org_id',%s,true)",(str(self.tenant.org_id),))
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with self.connection.transaction():
                    cur.execute('INSERT INTO db1_delivery.acceptance(org_id,logical_identity,payload,digest) VALUES (%s,%s,%s,%s)',
                                (self.other_org,b'forbidden synthetic identity',b'synthetic payload','0'*64))
        self.assertEqual(self.deliver().disposition,'identical')
        with self.connection.cursor() as cur:
            cur.execute("SELECT NULLIF(current_setting('app.org_id',true),'')");self.assertIsNone(cur.fetchone()[0])

    def test_reference_mismatches(self):
        # Real registration changes by administrative fixture only, restored
        # before each subcase; delivery and count assertions use normal role.
        cases=[('dim_camera','camera_key',41,'timezone','UTC'),('dim_run','run_key',51,'camera_key',42),
               ('dim_model','model_key',83,'task','vlm'),('dim_date','date_key',20261008,'date',None),
               ('dim_class','class_key',11,'class_group','person'),('dim_colour','colour_key',21,'name','silver')]
        # Cross-org run/camera update is forbidden by supervisor FK; use a wrong
        # supplied camera binding instead while leaving actual registrations valid.
        with self.assertRaises(ReferenceRejected):self.deliver(res=replace(self.res,run=replace(self.res.run,camera=replace(self.res.run.camera,camera_key=42))))
        for table,key,value,field,new in [c for c in cases if c[0] not in ('dim_run','dim_date')]:
            with self.subTest(table=table):
                with self.setup.cursor() as cur:
                    cur.execute(sql.SQL('SELECT {} FROM public.{} WHERE {}=%s').format(sql.Identifier(field),sql.Identifier(table),sql.Identifier(key)),(value,));old=cur.fetchone()[0]
                    cur.execute(sql.SQL('UPDATE public.{} SET {}=%s WHERE {}=%s').format(sql.Identifier(table),sql.Identifier(field),sql.Identifier(key)),(new,value))
                try:
                    with self.assertRaises(ReferenceRejected):self.deliver()
                    self.assertEqual(self.count('acceptance'),0)
                finally:
                    with self.setup.cursor() as cur:cur.execute(sql.SQL('UPDATE public.{} SET {}=%s WHERE {}=%s').format(sql.Identifier(table),sql.Identifier(field),sql.Identifier(key)),(old,value))
        with self.setup.cursor() as cur:cur.execute('DELETE FROM public.dim_date WHERE date_key=20261008')
        try:
            with self.assertRaises(ReferenceRejected):self.deliver()
        finally:
            with self.setup.cursor() as cur:cur.execute("INSERT INTO public.dim_date VALUES (20261008,'2026-10-08',4,41,10,4,2026,false,false)")

    def test_wrong_media_org_run_camera_kind(self):
        clip=self.res.media[0]
        for field,new,old in [('org_id',self.other_org,clip.org_id),('camera_key',42,41),('run_key',52,None),('kind','plate_crop','clip')]:
            with self.subTest(field=field):
                with self.setup.cursor() as cur:cur.execute(sql.SQL('UPDATE public.media_object SET {}=%s WHERE media_key=101').format(sql.Identifier(field)),(new,))
                try:
                    with self.assertRaises(ReferenceRejected):self.deliver()
                finally:
                    with self.setup.cursor() as cur:cur.execute(sql.SQL('UPDATE public.media_object SET {}=%s WHERE media_key=101').format(sql.Identifier(field)),(old,))

    def test_missing_media_model_and_category(self):
        plate=dict(self.completed.consumer_evidence)['plate'];attempt=plate.ledger[0]
        result=json.loads(attempt.result_json);result['plate_category']='foreign'
        plate=replace(plate,ledger=(replace(attempt,result_json=json.dumps(result).encode()),))
        completed=replace(self.completed,consumer_evidence=tuple((n,plate if n=='plate' else e) for n,e in self.completed.consumer_evidence))
        res=replace(self.res,plate_categories=(Dimension('foreign',31,True),))
        with LocalOutbox(path=self.temp.name+'/category',namespace=NAMESPACE,validation=validation()) as store:entry=store.accept(completed,res).entry
        with self.setup.cursor() as cur:cur.execute('DELETE FROM public.dim_plate_category WHERE plate_category_key=31')
        try:
            with self.assertRaises(ReferenceRejected):self.deliver(entry,res=res)
        finally:
            with self.setup.cursor() as cur:cur.execute("INSERT INTO public.dim_plate_category(plate_category_key,name) OVERRIDING SYSTEM VALUE VALUES (31,'foreign')")
        with self.assertRaises(ReferenceRejected):self.deliver(res=replace(self.res,media=()))
        with self.assertRaises(ReferenceRejected):self.deliver(res=replace(self.res,models=()))

    def test_rollback_child_constraint_failure_preserves_local_entry(self):
        with self.setup.cursor() as cur:cur.execute('ALTER TABLE public.fact_vehicle_colour ADD CONSTRAINT injected_test_failure CHECK (samples<0)')
        try:
            with self.assertRaises(DatabaseDeliveryError):self.deliver()
            self.assertEqual(self.count('acceptance'),0);self.assertEqual(self.count('fact_vehicle'),0)
            self.assertEqual(self.store.inspect_pending(),(self.entry,))
        finally:
            with self.setup.cursor() as cur:cur.execute('ALTER TABLE public.fact_vehicle_colour DROP CONSTRAINT injected_test_failure')

    def test_fact_without_history_is_not_identical(self):
        row=self.entry.construction.facts[0].sql.value()
        with self.connection.transaction(),self.connection.cursor() as cur:
            cur.execute("SELECT set_config('app.org_id',%s,true)",(str(self.tenant.org_id),))
            cur.execute(sql.SQL('INSERT INTO public.fact_vehicle ({}) VALUES ({})').format(sql.SQL(',').join(map(sql.Identifier,row)),sql.SQL(',').join(sql.Placeholder() for _ in row)),tuple(row.values()))
        with self.assertRaises(HistoryMismatch):self.deliver()
        self.assertEqual(self.count('acceptance'),0)

    def crop_entry(self):
        plate=dict(self.completed.consumer_evidence)['plate'];crop=plate.selected.crop
        media=replace(self.res.media[0],media_key=102,run_key=self.res.run.run_key,kind='plate_crop',
                      ts=crop.receipt.utc,object_key='synthetic-tests/selected-crop',content_type='image/jpeg',
                      bytes=12,sha256='c'*64,service_track_id=1,frame_index=crop.receipt.frame_index,
                      crop_sha256=sha256(crop.pixels).hexdigest())
        res=replace(self.res,media=(*self.res.media,media))
        with self.setup.cursor() as cur:
            cur.execute('INSERT INTO public.media_object(media_key,org_id,run_key,camera_key,ts,kind,bucket,object_key,content_type,bytes,width,height,sha256) OVERRIDING SYSTEM VALUE VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                        (media.media_key,media.org_id,media.run_key,media.camera_key,media.ts,media.kind,media.bucket,media.object_key,media.content_type,media.bytes,media.width,media.height,media.sha256))
        with LocalOutbox(path=self.temp.name+'/crop',namespace=NAMESPACE,validation=validation()) as store:
            entry=store.accept(self.completed,res).entry
        return entry,res

    def test_registered_selected_crop(self):
        entry,res=self.crop_entry()
        self.assertEqual(self.deliver(entry,res=res).disposition,'new')
        self.assertEqual(self.deliver(entry,res=res).disposition,'identical')

    def test_cross_run_wrong_kind_and_missing_selected_media(self):
        entry,res=self.crop_entry()
        for field,new,old in [('run_key',52,51),('kind','vehicle_crop','plate_crop')]:
            with self.subTest(field=field):
                with self.setup.cursor() as cur:cur.execute(sql.SQL('UPDATE public.media_object SET {}=%s WHERE media_key=102').format(sql.Identifier(field)),(new,))
                try:
                    with self.assertRaises(ReferenceRejected):self.deliver(entry,res=res)
                finally:
                    with self.setup.cursor() as cur:cur.execute(sql.SQL('UPDATE public.media_object SET {}=%s WHERE media_key=102').format(sql.Identifier(field)),(old,))
        with self.setup.cursor() as cur:cur.execute('DELETE FROM public.media_object WHERE media_key=102')
        with self.assertRaises(ReferenceRejected):self.deliver(entry,res=res)

    def test_nonexistent_manufacturer_null_model_fk_and_adapter_guard(self):
        self.deliver();parent=self.entry.construction.facts[0].sql.value()
        with self.connection.transaction(),self.connection.cursor() as cur:
            cur.execute("SELECT set_config('app.org_id',%s,true)",(str(self.tenant.org_id),))
            # Structural SQL only; make production remains unsupported. The
            # savepoint restores the transaction after expected FK rejection.
            with self.assertRaises(ReferenceRejected):check_manufacturer_reference(cur,2147483647,None)
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                with self.connection.transaction():
                    cur.execute('INSERT INTO public.fact_vehicle_make(org_id,run_key,camera_key,date_key,ts,uid,track_id,model_key,manufacturer_key,vehicle_model_key,observations) VALUES (%s,%s,%s,%s,%s,%s,%s,81,2147483647,NULL,1)',
                                (parent['org_id'],parent['run_key'],parent['camera_key'],parent['date_key'],parent['ts'],uuid4(),parent['track_id']))

    def test_normal_role_and_database_error_keeps_outbox_pending(self):
        with self.connection.cursor() as cur:
            cur.execute('SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user');self.assertEqual(cur.fetchone(),(False,False))
            cur.execute('SELECT current_user,session_user');self.assertEqual(cur.fetchone(),(self.app_role,self.app_role))
            cur.execute('SELECT rolcanlogin,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=%s',(self.owner_role,))
            self.assertEqual(cur.fetchone(),(False,False,False))
            for table in ('public.fact_vehicle','public.fact_vehicle_colour','public.fact_plate_read','public.fact_vehicle_make',
                          'db1_delivery.acceptance','db1_delivery.row_receipt'):
                cur.execute("SELECT has_table_privilege(current_user,%s,'SELECT'),has_table_privilege(current_user,%s,'INSERT'),has_table_privilege(current_user,%s,'UPDATE'),has_table_privilege(current_user,%s,'DELETE'),has_table_privilege(current_user,%s,'TRUNCATE')",(table,)*5)
                self.assertEqual(cur.fetchone(),(True,True,False,False,False),table)
            cur.execute("SELECT has_table_privilege(current_user,'db1_delivery.compatibility','INSERT'),has_schema_privilege(current_user,'public','CREATE'),has_schema_privilege(current_user,'db1_delivery','CREATE')")
            self.assertEqual(cur.fetchone(),(False,False,False))
        self.connection.close()
        with self.assertRaises(DatabaseDeliveryError):self.deliver()
        self.assertEqual(self.store.inspect_pending(),(self.entry,))


if __name__=='__main__':unittest.main()
