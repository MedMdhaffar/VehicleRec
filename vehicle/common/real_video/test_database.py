"""Opt-in disposable PG16 runner provisioning through the existing P4 fixture.

No environment/default connection fallback. Missing isolation configuration stops
before connection. Supervisor SQL and transactional delivery remain canonical.
"""
from dataclasses import replace
from datetime import timedelta
import importlib.util,os
from pathlib import Path
from uuid import UUID
from ..db1_context import ModelRegistration
from ..db1_rows import Resolutions,RunResolution,ResolvedModel,Dimension,MediaResolution
from ..observation_evidence import RecognitionIdentity
from ..db1_adapter import TrustedTenant
from ..db1_rows.core import require
from contracts.db1.reference import derive_date_key

ROOT=Path(__file__).resolve().parents[3]


def fixture_module():
    spec=importlib.util.spec_from_file_location('waycon_owned_p4_fixture',ROOT/'tests/db1_integration/test_postgres.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def require_test_target():
    # This canonical guard parses privately, suppresses errors and opens nothing.
    return fixture_module().provisioning_params(os.environ)


def registrations(video):
    c=video.context
    camera=replace(c.camera,camera_key=41)
    detector=replace(c.resolution.detector,model_key=91)
    dates=tuple(sorted({derive_date_key((c.started_at+timedelta(days=d)).isoformat(),camera.timezone) for d in (0,1)}))
    run=RunResolution(c.org_id,c.run_id,51,camera,5,'vehicle',detector,'file',101,c.started_at,c.service_version,dates,
        'explicit isolated injected runner registration',True)
    models=(ResolvedModel(video.engine.colour.algorithm,81),ResolvedModel(RecognitionIdentity(**video.bindings['ocr'].identity.model.policy()),82))
    clip=MediaResolution(101,c.org_id,None,41,c.run_id,c.camera.camera_id,c.started_at,'clip','synthetic-runner',
        'injected/source.mp4','video/mp4',0,160,100,'a'*64,None,None,None)
    return Resolutions(run,(Dimension('car',11,True,'vehicle'),),(Dimension('red',21,True),),
        (Dimension('private',31,True),),models,(clip,))


class OwnedDatabase:
    def __init__(self,video):
        self.module=fixture_module();self.module.provisioning_params(os.environ)
        supplied=registrations(video);module=self.module
        class Fixture(module.PostgreSQL16Tests):
            @classmethod
            def seed_registrations(cls):
                cls.res=supplied;cls.tenant=TrustedTenant(supplied.run.org_id,'owned isolated runner fixture')
                cls.other_org=UUID(int=2 if supplied.run.org_id!=UUID(int=2) else 3)
                module.PostgreSQL16Tests.seed_registrations.__func__(cls)
                with cls.setup.cursor() as cur:
                    cur.execute('UPDATE public.dim_colour SET name=%s WHERE colour_key=21',('red',))
                    cur.execute('UPDATE public.dim_plate_category SET name=%s WHERE plate_category_key=31',('private',))
        self.fixture=Fixture;self.settings={};self.cleanup={}

    def __enter__(self):
        try:
            self.fixture.setUpClass()
            with self.fixture.connect() as conn:
                with conn.cursor() as cur:
                    cur.execute('SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname IN (current_user,session_user)')
                    roles=cur.fetchall();require(roles and all(r==(False,False) for r in roles),'DB_ROLE','normal NOBYPASSRLS roles required')
                    cur.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON c.relnamespace=n.oid JOIN pg_roles r ON c.relowner=r.oid WHERE n.nspname IN ('public','db1_delivery') AND r.rolname=current_user")
                    require(cur.fetchone()==(0,),'DB_OWNER','application role must not own tables')
                    cur.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON c.relnamespace=n.oid WHERE c.relname=ANY(%s) AND c.relrowsecurity AND c.relforcerowsecurity",(['dim_camera','dim_run','media_object','fact_vehicle','fact_vehicle_colour','fact_plate_read','fact_vehicle_make','acceptance','row_receipt'],))
                    require(cur.fetchone()==(9,),'DB_RLS','all tenant tables must force RLS')
            self.settings=dict(version=self.fixture.admin.info.server_version,normal_role=True,nobypassrls=True,application_table_owner=False,forced_rls_tables=9,
                target='owned disposable P4 database',registrations='synthetic-test-only')
            return self
        except BaseException:self.close();raise

    def consumer(self,codec,video):
        from ..db1_consumer import NaturalConsumer
        return NaturalConsumer(codec=codec.v1,tenant=self.fixture.tenant,trusted_run=video.context.run_id,trusted_camera=video.context.camera.camera_id,
            resolutions=self.fixture.res,connection_factory=self.fixture.connect,environment='test')

    def receipts(self):
        result=[]
        with self.fixture.connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT set_config('app.org_id',%s,true)",(str(self.fixture.tenant.org_id),))
                    cur.execute('SELECT digest FROM db1_delivery.acceptance ORDER BY digest')
                    result=[row[0] for row in cur.fetchall()]
        return result

    def close(self):
        self.fixture.doClassCleanups()
        require(not self.fixture.tearDown_exceptions,'DB_CLEANUP','owned database/role cleanup failed')
        self.cleanup=dict(owned_database_and_roles_removed=True)
    def __exit__(self,*_):self.close()
