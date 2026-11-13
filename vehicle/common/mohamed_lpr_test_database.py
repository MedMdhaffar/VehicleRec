"""Explicit disposable fixture host, never production registration provisioning."""
from types import SimpleNamespace
from uuid import UUID
from .real_video.test_database import OwnedDatabase, fixture_module
from .db1_adapter import TrustedTenant
from .db1_consumer import NaturalConsumer
from .tests.test_db1_consumer import supplied_test_registrations


class OwnedDeliveryDatabase(OwnedDatabase):
    def __init__(self, codec, raw, *, source_media=None):
        module=fixture_module()
        module.provisioning_params(__import__('os').environ)
        supplied=supplied_test_registrations(SimpleNamespace(v1=codec),raw)
        from dataclasses import replace
        supplied=replace(supplied,run=replace(supplied.run,lane_key=5))  # Existing P4 supervisor-seeded vehicle lane.
        if supplied.run.source_kind == 'stream':
            # The P4 seed includes a synthetic template media row, but an RTSP
            # run must not claim that unrelated MP4 as its source.
            supplied=replace(supplied,run=replace(supplied.run,source_media_key=None))
        if source_media is not None:
            media=replace(supplied.media[0],**source_media)
            supplied=replace(supplied,media=(media,))
        class Fixture(module.PostgreSQL16Tests):
            @classmethod
            def seed_registrations(cls):
                cls.res=supplied;cls.tenant=TrustedTenant(supplied.run.org_id,'explicit owned Phase 5 fixture')
                cls.other_org=UUID(int=2 if supplied.run.org_id!=UUID(int=2) else 3)
                module.PostgreSQL16Tests.seed_registrations.__func__(cls)
                with cls.setup.cursor() as cur:
                    if supplied.colours:
                        cur.execute('UPDATE public.dim_colour SET name=%s WHERE colour_key=21',(supplied.colours[0].name,))
                    if supplied.plate_categories:
                        cur.execute('UPDATE public.dim_plate_category SET name=%s WHERE plate_category_key=31',(supplied.plate_categories[0].name,))
                    if supplied.classes:
                        cur.execute('UPDATE public.dim_class SET name=%s WHERE class_key=11',(supplied.classes[0].name,))
        self.fixture,self.settings,self.cleanup=Fixture,{},{}
        self.codec=codec

    def consumer(self):
        r=self.fixture.res.run
        return NaturalConsumer(codec=self.codec,tenant=self.fixture.tenant,trusted_run=r.run_id,
            trusted_camera=r.camera.camera_id,resolutions=self.fixture.res,connection_factory=self.fixture.connect,
            environment='test',plate_reader=getattr(self.codec,'plate_reader',None))

    def inspect(self, entry):
        """Actual restricted separate connection; no text leaves this function."""
        from hashlib import sha256
        from psycopg import sql
        expected={f.row_kind:f for f in entry.construction.ready_rows}
        counts={}
        with self.fixture.connect() as conn:
            with conn.transaction(),conn.cursor() as cur:
                cur.execute("SELECT set_config('app.org_id',%s,true)",(str(self.fixture.tenant.org_id),))
                cur.execute('SELECT pg_backend_pid()');pid=cur.fetchone()[0]
                for table in ('fact_vehicle','fact_vehicle_colour','fact_plate_read','fact_vehicle_make'):
                    cur.execute(sql.SQL('SELECT count(*) FROM public.{}').format(sql.Identifier(table)))
                    counts[table]=cur.fetchone()[0]
                    if table in expected and counts[table]:
                        row=expected[table].sql.value()
                        columns=list(row)
                        cur.execute(sql.SQL('SELECT {} FROM public.{} WHERE uid=%s').format(
                            sql.SQL(',').join(map(sql.Identifier,columns)),sql.Identifier(table)),(row['uid'],))
                        actual=cur.fetchone()
                        assert actual is not None
                        # Adapter's canonical receipt validates REAL projection;
                        # compare identity/text/model/timestamp here independently.
                        got=dict(zip(columns,actual))
                        from contracts.db1.reference import parse_timestamp
                        assert got['ts']==parse_timestamp(row['ts'])
                        for key in ('uid','org_id','run_key','camera_key','track_id','model_key','plate_category_key','plate_text'):
                            if key in row:assert str(got[key])==str(row[key])
                cur.execute('SELECT count(*) FROM db1_delivery.acceptance');acceptances=cur.fetchone()[0]
                cur.execute('SELECT count(*) FROM db1_delivery.row_receipt');receipts=cur.fetchone()[0]
                cur.execute('SELECT payload,digest FROM db1_delivery.acceptance');stored=cur.fetchall()
                digests=[r[1] for r in stored]
                for payload,checksum in stored:
                    assert bytes(payload)==self.fixture.adapter.prepare(entry,self.fixture.tenant)
                    assert sha256(bytes(payload)).hexdigest()==checksum
                cur.execute('SELECT row_kind,uid,delivery,projection,delivery_sha256,projection_sha256 FROM db1_delivery.row_receipt')
                for kind,uid,delivery,projection,delivery_sha,projection_sha in cur.fetchall():
                    fact=expected[kind]
                    assert str(uid)==fact.sql.value()['uid']
                    assert bytes(delivery)==fact.delivery.canonical and delivery_sha==fact.delivery.digest
                    assert bytes(projection)==fact.sql.canonical and projection_sha==fact.sql.digest
        return dict(rows=counts,acceptances=acceptances,row_receipts=receipts,acceptance_digests=digests,
                    row_uids={k:f.sql.value()['uid'] for k,f in expected.items()},
                    row_hashes={k:dict(delivery_sha256=f.delivery.digest,projection_sha256=f.sql.digest) for k,f in expected.items()},
                    frame_receipts={k:dict(frame_index=f.diagnostic.value()['frame_index'],ts=f.sql.value()['ts']) for k,f in expected.items()},
                    separate_connection_pid=pid)
