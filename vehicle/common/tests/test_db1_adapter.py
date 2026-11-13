"""Injected driver/reference tests only; not PostgreSQL integration claims."""
from dataclasses import replace
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, MagicMock, patch
from uuid import UUID

import psycopg
from psycopg.pq import TransactionStatus

from vehicle.common.db1_adapter import (DB1Adapter, TrustedTenant, InvalidAcceptance,
    ReferenceRejected, HistoryMismatch, DeliveryConflict, DatabaseDeliveryError,
    check_manufacturer_reference)
from vehicle.common.db1_outbox import LocalOutbox, _pack
from vehicle.common.db1_rows import FrozenJSON
from vehicle.common.tests.test_db1_outbox import validation
from vehicle.common.tests.test_db1_rows import fixture,NAMESPACE


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.completed,self.res=fixture()
        self.store=LocalOutbox(path=self.temp.name+'/outbox',namespace=NAMESPACE,validation=validation())
        self.addCleanup(self.store.close)
        self.entry=self.store.accept(self.completed,self.res).entry
        self.adapter=DB1Adapter(validation=validation())
        self.tenant=TrustedTenant(self.res.run.org_id,'synthetic authenticated test host')

    def altered(self,index,**fields):
        facts=list(self.entry.construction.facts);facts[index]=replace(facts[index],**fields)
        entry=replace(self.entry,construction=replace(self.entry.construction,facts=tuple(facts)))
        return replace(entry,stored_bytes=len(_pack(entry.logical_identity,entry.construction)))

    def test_exact_prepare_and_immutable_entry(self):
        payload=self.adapter.prepare(self.entry,self.tenant)
        self.assertEqual(payload,_pack(self.entry.logical_identity,self.entry.construction))
        self.assertEqual(self.store.inspect_pending(),(self.entry,))

    def test_no_tenant_or_payload_tenant_as_authorization(self):
        for tenant in (None,self.res.run.org_id,TrustedTenant(UUID(int=2),'other trusted tenant')):
            with self.assertRaises(InvalidAcceptance):self.adapter.prepare(self.entry,tenant)

    def test_reject_invalid_blocked_unsettled_or_lost_delivery(self):
        fact=self.entry.construction.facts[2]
        for entry in (self.altered(2,retry='conflict'),self.altered(2,delivery=None),
                      self.altered(2,metadata=FrozenJSON.of(dict(quarantined=True))),
                      replace(self.entry,acceptance='delivered'),replace(self.entry,stored_bytes=1)):
            with self.assertRaises(InvalidAcceptance):self.adapter.prepare(entry,self.tenant)

    def test_terminal_omission_produces_no_manufacturer_row(self):
        self.assertIsNone(self.entry.construction.facts[-1].sql)
        self.assertEqual(len(self.entry.construction.ready_rows),3)
        self.adapter.prepare(self.entry,self.tenant)

    def registrations(self):
        r=self.res.run;m=self.res.media[0]
        records={('dim_org','org_id',r.org_id):dict(org_id=r.org_id),
            ('dim_camera','camera_key',r.camera.camera_key):dict(camera_key=r.camera.camera_key,org_id=r.org_id,
            camera_id=r.camera.camera_id,timezone=r.camera.timezone,valid_from=r.camera.valid_from,valid_to=r.camera.valid_to),
            ('dim_run','run_key',r.run_key):dict(run_key=r.run_key,org_id=r.org_id,run_id=r.run_id,
            camera_key=r.camera.camera_key,lane_key=r.lane_key,model_key=r.detector.model_key,source_kind=r.source_kind,
            source_media_key=r.source_media_key,started_at=r.started_at,ended_at=None,service_version=r.service_version),
            ('dim_lane','lane_key',r.lane_key):dict(name='vehicle',service='vehicle_service'),
            ('dim_model','model_key',r.detector.model_key):dict(model_key=r.detector.model_key,
            model_name=r.detector.model_name,version=r.detector.version,task='detect'),
            ('dim_class','class_key',11):dict(class_key=11,name='car',class_group='vehicle'),
            ('dim_colour','colour_key',21):dict(colour_key=21,name='silver/gray'),
            ('media_object','media_key',m.media_key):dict(media_key=m.media_key,org_id=m.org_id,
            run_key=m.run_key,camera_key=m.camera_key,ts=m.ts,kind=m.kind,bucket=m.bucket,object_key=m.object_key,
            content_type=m.content_type,bytes=m.bytes,width=m.width,height=m.height,sha256=m.sha256)}
        for key in r.date_keys:records[('dim_date','date_key',key)]=dict(date=date(key//10000,key//100%100,key%100))
        for model in self.res.models:records[('dim_model','model_key',model.key)]=dict(model_key=model.key,**model.identity.policy())
        return records

    def reference_check(self,records):
        cursor=Mock();cursor.fetchall.return_value=[(41,)]
        def lookup(cur,table,key,value):
            if (table,key,value) not in records:raise ReferenceRejected('injected missing registration')
            return records[(table,key,value)]
        with patch('vehicle.common.db1_adapter.one',side_effect=lookup):
            self.adapter.validate_references(cursor,self.entry,self.res)

    def test_matching_reference_logic(self):self.reference_check(self.registrations())

    def test_mismatched_camera_run_model_class_lane_date_colour(self):
        defects=[(('dim_camera','camera_key',41),'timezone','UTC'),
                 (('dim_run','run_key',51),'camera_key',42),
                 (('dim_model','model_key',83),'task','vlm'),
                 (('dim_class','class_key',11),'class_group','person'),
                 (('dim_lane','lane_key',7),'name','face_push'),
                 (('dim_date','date_key',20261008),'date',date(2026,10,9)),
                 (('dim_colour','colour_key',21),'name','silver')]
        for key,field,value in defects:
            with self.subTest(reference=key[0]):
                records=self.registrations();records[key][field]=value
                with self.assertRaises(ReferenceRejected):self.reference_check(records)

    def test_missing_registration(self):
        records=self.registrations();del records[('dim_model','model_key',83)]
        with self.assertRaises(ReferenceRejected):self.reference_check(records)

    def test_media_cross_org_run_camera_wrong_kind_and_metadata(self):
        for field,value in (('org_id',UUID(int=9)),('run_key',99),('camera_key',99),
                            ('kind','plate_crop'),('sha256','b'*64)):
            with self.subTest(field=field):
                records=self.registrations();records[('media_object','media_key',101)][field]=value
                with self.assertRaises(ReferenceRejected):self.reference_check(records)

    def test_camera_ambiguous_or_outside_version(self):
        cursor=Mock();cursor.fetchall.return_value=[(41,),(42,)]
        records=self.registrations()
        with patch('vehicle.common.db1_adapter.one',side_effect=lambda c,t,k,v:records[(t,k,v)]):
            with self.assertRaises(ReferenceRejected):self.adapter.validate_references(cursor,self.entry,self.res)

    def test_supervisor_direct_manufacturer_fk_declared(self):
        import re
        from zipfile import ZipFile
        from vehicle.common.tests.test_db1_rows import ROOT
        with ZipFile(ROOT/'db1_contract.zip') as archive:
            source=archive.read('db1_contract/02_vehicle.sql').decode()
        make=source.split('CREATE TABLE fact_vehicle_make (',1)[1].split(');',1)[0]
        self.assertRegex(make,r'manufacturer_key\s+int\s+REFERENCES\s+dim_manufacturer')

    def test_manufacturer_null_model_checks_direct_registration(self):
        with patch('vehicle.common.db1_adapter.one',side_effect=ReferenceRejected('injected missing manufacturer')) as one:
            with self.assertRaises(ReferenceRejected):check_manufacturer_reference(Mock(),999,None)
            self.assertEqual(one.call_args.args[1:3],('dim_manufacturer','manufacturer_key'))
        with self.assertRaises(ReferenceRejected):check_manufacturer_reference(Mock(),None,999)

    def test_real_integrity_check_uses_binary32_only_after_receipt_equality(self):
        fact=self.entry.construction.facts[0];row=fact.sql.value()
        import struct
        actual=[row[k] for k in row]
        actual[list(row).index('class_conf')]=struct.unpack('!f',struct.pack('!f',float(row['class_conf'])))[0]
        cursor=Mock();cursor.fetchone.side_effect=[tuple(actual),(True,)]
        self.adapter._verify_fact(cursor,fact)
        actual[list(row).index('frames_observed')]=100
        cursor.fetchone.side_effect=[tuple(actual),(True,)]
        with self.assertRaises(HistoryMismatch):self.adapter._verify_fact(cursor,fact)

    def connection(self):
        connection=MagicMock();connection.autocommit=True;connection.closed=False
        connection.info=SimpleNamespace(transaction_status=TransactionStatus.IDLE,server_version=160004)
        cursor=connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.side_effect=[[(False,False)],[(str(i),'table',True,True) for i in range(9)]]
        cursor.fetchone.side_effect=[('read committed',),(self.adapter.compatibility,)]
        return connection,cursor

    def test_transaction_local_context_and_post_commit_result(self):
        connection,cursor=self.connection()
        with patch.object(self.adapter,'validate_references'),patch.object(self.adapter,'_apply',return_value='new'):
            result=self.adapter.deliver(connection,self.entry,tenant=self.tenant,resolutions=self.res)
        self.assertEqual(result.disposition,'new');self.assertEqual(result.fact_count,3)
        self.assertEqual(cursor.execute.call_args_list[0].args,("SELECT set_config('app.org_id', %s, true)",(str(self.tenant.org_id),)))
        connection.transaction.return_value.__exit__.assert_called_once()

    def test_superuser_bypass_or_existing_transaction_rejected(self):
        for role in ((True,False),(False,True)):
            connection,cursor=self.connection();cursor.fetchall.side_effect=[[role]]
            with self.assertRaises(InvalidAcceptance):self.adapter.deliver(connection,self.entry,tenant=self.tenant,resolutions=self.res)
        connection,cursor=self.connection();connection.autocommit=False
        with self.assertRaises(InvalidAcceptance):self.adapter.deliver(connection,self.entry,tenant=self.tenant,resolutions=self.res)

    def test_missing_or_disabled_forced_rls_rejects(self):
        for secured in ([],[('public','table',True,False)]*9):
            connection,cursor=self.connection();cursor.fetchall.side_effect=[[(False,False)],secured]
            with self.assertRaises(InvalidAcceptance):self.adapter.deliver(connection,self.entry,tenant=self.tenant,resolutions=self.res)

    def test_transaction_and_commit_failures_preserve_local_entry(self):
        for commit in (False,True):
            connection,cursor=self.connection()
            if commit:connection.transaction.return_value.__exit__.side_effect=psycopg.OperationalError('injected uncertain commit')
            else:cursor.execute.side_effect=psycopg.OperationalError('injected statement failure')
            with patch.object(self.adapter,'validate_references'),patch.object(self.adapter,'_apply',return_value='new'):
                with self.assertRaises(DatabaseDeliveryError) as rejected:
                    self.adapter.deliver(connection,self.entry,tenant=self.tenant,resolutions=self.res)
            self.assertEqual(rejected.exception.commit_outcome,'unknown' if commit else 'not_committed')
            self.assertEqual(self.store.inspect_pending(),(self.entry,))

    def test_apply_new_parent_and_children_atomically_staged(self):
        cursor=Mock();cursor.fetchone.return_value=None
        payload=self.adapter.prepare(self.entry,self.tenant)
        self.assertEqual(self.adapter._apply(cursor,self.entry,payload),'new')
        statements=[c.args[0] for c in cursor.execute.call_args_list]
        inserts=[q for q in statements if isinstance(q,str) and q.startswith('INSERT INTO db1_delivery')]
        self.assertEqual(len(inserts),4)  # One vehicle receipt plus three row receipts.
        fact_inserts=[q.as_string() for q in statements if not isinstance(q,str) and q.as_string().startswith('INSERT INTO public.')]
        self.assertEqual(len(fact_inserts),3)
        self.assertIn('"fact_vehicle"',fact_inserts[0])
        self.assertFalse(any('DO NOTHING' in q for q in fact_inserts))
        locks=[c.args[1][0] for c in cursor.execute.call_args_list if c.args[0]=='SELECT pg_advisory_xact_lock(%s)']
        self.assertEqual(locks,sorted(set(locks)))

    def test_apply_exact_history_retry_without_insert(self):
        payload=self.adapter.prepare(self.entry,self.tenant)
        from hashlib import sha256
        identity=self.entry.logical_identity.canonical
        receipts=[(identity,f.delivery.canonical,f.sql.canonical,f.delivery.digest,f.sql.digest)
                  for f in self.entry.construction.ready_rows]
        cursor=Mock();cursor.fetchone.side_effect=[(payload,sha256(payload).hexdigest()),*receipts]
        with patch.object(self.adapter,'_verify_fact') as verify:
            self.assertEqual(self.adapter._apply(cursor,self.entry,payload),'identical')
            self.assertEqual(verify.call_count,3)
        self.assertFalse(any(isinstance(c.args[0],str) and c.args[0].startswith('INSERT') for c in cursor.execute.call_args_list))

    def test_apply_changed_or_corrupt_history_rejects(self):
        from hashlib import sha256
        payload=self.adapter.prepare(self.entry,self.tenant)
        for prior,error in [((b'changed',sha256(b'changed').hexdigest()),DeliveryConflict),
                            ((payload,'invalid'),HistoryMismatch)]:
            cursor=Mock();cursor.fetchone.return_value=prior
            with self.assertRaises(error):self.adapter._apply(cursor,self.entry,payload)

    def test_fact_without_history_is_not_identical(self):
        payload=self.adapter.prepare(self.entry,self.tenant)
        cursor=Mock();cursor.fetchone.side_effect=[None,None,(1,)]
        with self.assertRaises(HistoryMismatch):self.adapter._apply(cursor,self.entry,payload)

    def test_closed_connection_and_wrong_server_rejected(self):
        connection,cursor=self.connection();connection.closed=True
        with self.assertRaises(DatabaseDeliveryError):self.adapter.deliver(connection,self.entry,tenant=self.tenant,resolutions=self.res)
        connection,cursor=self.connection();connection.info.server_version=150000
        with self.assertRaises(InvalidAcceptance):self.adapter.deliver(connection,self.entry,tenant=self.tenant,resolutions=self.res)
        connection.transaction.assert_not_called()

    def test_prepare_decimal_differences_are_exact(self):
        completed=replace(self.completed,vehicle=replace(self.completed.vehicle,type_confidence=Decimal('0.12345678901234567890123456781')))
        with LocalOutbox(path=self.temp.name+'/other',namespace=NAMESPACE,validation=validation()) as store:
            other=store.accept(completed,self.res).entry
            self.assertNotEqual(self.adapter.prepare(self.entry,self.tenant),self.adapter.prepare(other,self.tenant))


if __name__=='__main__':unittest.main()
