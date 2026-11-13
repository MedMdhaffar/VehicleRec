"""Real T1 Engine, injected pixels/models, owned Redis and opt-in disposable PG16."""
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from uuid import uuid4

import numpy as np
from vehicle.common.tests.test_colour_aggregator import fixture
from vehicle.common.tests.test_mp4_colour_plate import Vehicles, Plates
from vehicle.common.tests.test_mohamed_lpr_engine import Runtime
from vehicle.common.frame_receipt import FrameReceiptClock, ClockPolicy
from vehicle.common.real_video.input import DecodedFrame
from vehicle.common.real_video.plate_engine import CombinedEngine
from vehicle.common.real_video.plate_local import PlateDetections
from vehicle.common.real_video.rtsp_v3 import V3RTSPSession
from vehicle.common.real_video.rtsp_delivery import projection_hook, consume_owned
from vehicle.common.mohamed_lpr_redis_runtime import OwnedRedis
from vehicle.common.mohamed_lpr_stream_codec import STREAMS
from vehicle.common.mohamed_lpr_streams import pending
from vehicle.common.observation_evidence import RecognitionIdentity
from vehicle.common.vehicle_observation import RunEnd

SERVER=os.environ.get('VEHICLE_REDIS_SERVER','/home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server')


class SessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.owned=OwnedRedis(SERVER).__enter__()
    @classmethod
    def tearDownClass(cls):
        cls.owned.__exit__(None,None,None)
        assert cls.owned.cleaned

    def session(self, category='tunis', text='001T0007', mode='legacy_whole', *, hook=None, no_plates=False):
        owner,_,_=fixture()
        context=replace(owner.lifetime.context,source_kind='rtsp',run_id='rtsp-unit-'+uuid4().hex)
        clock=FrameReceiptClock(context,utc_clock=lambda:datetime.now(timezone.utc),
                                monotonic_clock=time.monotonic,policy=ClockPolicy())
        plates=Plates([],PlateDetections((),()) if no_plates else PlateDetections(
            ((10,40,110,70,.9),),((1,(10,40,110,70,.9)),)))
        engine=CombinedEngine(context,clock,Vehicles([]),plates,{},
            algorithm=RecognitionIdentity('injected-hsv',1,'classify'),quality_algorithm='quality',
            plate_quality_algorithm='plate-quality',estimate=lambda c:SimpleNamespace(label='red',confidence=.8),
            quality_fn=lambda *a:SimpleNamespace(combined=.9),plate_quality_fn=lambda *a:1.0)
        session=V3RTSPSession(context,clock,25,time.monotonic()+20,threading.Event(),assets=None,
            models=Runtime(category,text),owned=self.owned,tunis_mode=mode,max_frames=4,max_seconds=20,max_crops=4,
            engine=engine,delivery_hook=hook)
        self.addCleanup(session.close)
        for i in range(3):
            packet=DecodedFrame(clock.capture(i,media_pts=i*.04),np.ones((100,160,3),dtype=np.uint8))
            session.admit(packet)
        session.finish(RunEnd.CAMERA_OUTAGE,trigger='injected_disconnect')
        return session

    def test_four_categories_both_modes_outage_settlement_parity_and_cleanup(self):
        for mode in ('legacy_whole','marker_first'):
            for category,text in (('tunis','001T0007'),('idara','001 - 002'),('nt','0007'),('shorta','0007')):
                with self.subTest(mode=mode,category=category):
                    s=self.session(category,text,mode)
                    report=s.settle()
                    self.assertEqual(report['accepted'],1)
                    self.assertEqual(report['decisions'][0]['category'],category)
                    self.assertEqual(report['closures'][0]['cause'],'camera_outage')
                    self.assertEqual(report['db1_records'],0)
                    self.assertTrue(report['parity'])
                    cleanup=s.close()
                    self.assertEqual((cleanup['retained_crops'],cleanup['unretired_operations']), (0,0))
                    self.assertTrue(cleanup['complete'])
                    self.assertEqual(cleanup['release_calls'],cleanup['crop_allocations'])
                    self.assertEqual(cleanup['retained_closures'],0)
                    self.assertEqual(report['pending'],dict(plate=0,engine=0,db1=0))

    def test_no_associated_crop_is_incomplete_not_no_plate(self):
        s=self.session(no_plates=True)
        report=s.settle()
        self.assertEqual((report['incomplete'],report['accepted'],report['db1_records']), (1,0,0))
        self.assertEqual(report['incomplete_reason'],'no_associated_crop_evidence')
        self.assertEqual(s.models.calls,{})
        self.assertTrue(s.close()['complete'])

    def test_valid_category_invalid_number_never_becomes_private_delivery(self):
        s=self.session(text='001T00077')
        report=s.settle()
        self.assertEqual((report['accepted'],report['unresolved'],report['db1_records']), (0,1,0))
        self.assertEqual(report['decisions'][0]['category'],'tunis')

    def test_inference_failure_settles_as_unresolved_with_explicit_retirement(self):
        s=self.session();s.models.fail=True
        report=s.settle()
        self.assertEqual((report['accepted'],report['unresolved'],report['db1_records']),(0,1,0))
        cleanup=s.close()
        self.assertEqual(cleanup['unretired_operations'],0)
        self.assertGreater(report['execution_errors'],0)

    def test_old_connection_result_and_duplicate_cannot_execute_or_change_new_seal(self):
        old=self.session();old.settle()
        raw=self.owned.client.xrange(old.live.journal.key(STREAMS['executions']))[0][1][b'json']
        new=self.session();new.settle()
        decision=list(new.record['decisions']);calls=dict(new.models.calls)
        sealed_key=new.live.journal.key(STREAMS['plate_result'])
        sealed_rows=self.owned.client.xrange(sealed_key)
        session=next(iter(new.live.slots))
        sealed_state=new.plate.journal.read_state(session)['result']
        key=new.live.journal.key(STREAMS['executions'])
        self.owned.client.xadd(key,{'json':raw})
        for _ in range(4):new.plate.step();new.peer.step()
        self.assertEqual(new.record['decisions'],decision)
        self.assertEqual(dict(new.models.calls),calls)
        self.assertEqual(pending(self.owned.client,new.plate),0)
        own=self.owned.client.xrange(key)[0][1][b'json']
        self.owned.client.xadd(key,{'json':own})
        for _ in range(4):new.plate.step();new.peer.step()
        self.assertEqual(dict(new.models.calls),calls)
        self.assertEqual(new.plate.journal.read_state(session)['status'],'terminal')
        self.assertEqual(new.plate.journal.read_state(session)['result'],sealed_state)
        self.assertEqual(self.owned.client.xrange(sealed_key),sealed_rows)

    def test_admission_deadline_marks_incomplete_without_fabricated_seal(self):
        s=self.session();s.deadline=time.monotonic()-1
        report=s.settle()
        self.assertEqual(report['accepted'],0)
        self.assertEqual(report['incomplete'],1)
        self.assertEqual(report['incomplete_reason'],'stopped_before_v3_settlement')
        self.assertEqual(s.models.calls,{})
        self.assertTrue(s.close()['complete'])


class PostgreSQLTests(SessionTests):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('DB1_P4_EXISTING_SERVER')!='1':
            raise unittest.SkipTest('explicit disposable PostgreSQL fixture configuration required')
        super().setUpClass()

    def test_rtsp_run_registration_and_commit_visible_before_ack(self):
        with tempfile.TemporaryDirectory(prefix='rtsp-owned-control-') as name:
            control=Path(name);errors=[];results=[]
            def host():
                try:
                    deadline=time.monotonic()+20
                    while not list(control.glob('ready-*.json')):
                        if time.monotonic()>deadline:raise RuntimeError('test_host_deadline')
                        time.sleep(.01)
                    ready=next(control.glob('ready-*.json'))
                    result=consume_owned(json.loads(ready.read_text()));results.append(result)
                    ready.with_name(ready.name.replace('ready-','done-')).write_text(json.dumps(result))
                except BaseException as error:errors.append(type(error).__name__)
            thread=threading.Thread(target=host);thread.start()
            s=self.session(hook=lambda **kw:projection_hook(control,threading.Event(),time.monotonic()+25,**kw))
            report=s.settle();thread.join(3)
            self.assertFalse(errors)
            self.assertFalse(thread.is_alive())
            self.assertEqual(report['db1_records'],3)
            self.assertEqual(results[0]['db1_pending'],0)
            self.assertEqual(results[0]['confirmed'][0]['before_ack']['rows']['fact_plate_read'],1)
            self.assertEqual(results[0]['confirmed'][0]['ack_reply'],1)
            self.assertEqual(results[0]['confirmed'][0]['source_kind'],'stream')
            self.assertIsNone(results[0]['confirmed'][0]['source_media_key'])
            self.assertEqual(results[0]['confirmed'][0]['time_basis'],'host_receipt')
            self.assertEqual(results[0]['confirmed'][0]['run_id'],s.context.run_id)
            self.assertEqual(results[0]['confirmed'][0]['before_ack']['row_receipts'],3)
            self.assertTrue(all(x['owned_database_and_roles_removed'] for x in results[0]['cleanup']))
            print('RTSP_COMMIT_EVIDENCE '+json.dumps(results[0]),flush=True)


if __name__=='__main__':unittest.main()
