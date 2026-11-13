"""Same-track public projections and controlled manufacturer cancellation."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import numpy as np
from contracts.db1.reference import canonical_json_bytes
from vehicle.common.console_projection import project_track
from vehicle.common.real_video.engine import ClosedScalars
from vehicle.common.manufacturer.engine import ManufacturerLedger
from vehicle.common.manufacturer.local import LocalRuntime
from vehicle.common.observation_evidence import VehicleIdentity
from vehicle.common.redis_scalar.worker import load_codec
from vehicle.common.redis_scalar.synthetic import ORG, IDENTITY_NAMESPACE
from vehicle.common.tests.test_colour_aggregator import fixture
from vehicle.common.tests.test_manufacturer import Runtime
from vehicle.common.tests.test_plate_metadata import transcript
from vehicle.common.plate_metadata import PlateMetadata


class ProjectionTests(unittest.TestCase):
    def test_review_ack_distinguishes_unread_and_published_but_unacked_messages(self):
        from uuid import uuid4
        from vehicle.common.console_runs import metadata_turn, review_acknowledgments
        from vehicle.common.manufacturer.streams import ReviewCodec, ReviewWorker, SOURCE, RESULT, publish
        from vehicle.common.mohamed_lpr_redis_runtime import OwnedRedis
        from vehicle.common.tests.test_manufacturer import SERVER
        owner,measurements,classes=fixture()
        with tempfile.TemporaryDirectory() as tmp, OwnedRedis(SERVER) as owned:
            runtime=Runtime();ledger=ManufacturerLedger(enabled=True,runtime=runtime,journal=Path(tmp)/'ledger')
            self.addCleanup(ledger.close)
            codec=ReviewCodec(load_codec(ORG,IDENTITY_NAMESPACE))
            worker=ReviewWorker(owned.client,codec=codec,namespace='ack-'+uuid4().hex[:16])
            worker.setup();requests={}
            for sid in (1,2):
                life=replace(owner.lifetime,service_track_id=sid,raw_track_id=100+sid)
                identity=VehicleIdentity.from_lifetime(life)
                ledger.observe(life,life.first_receipt,(0,0,64,64),np.zeros((64,64,3),np.uint8),'f'*64)
                evidence=ledger.terminal(life)
                closed=ClosedScalars(replace(owner,lifetime=life),tuple(replace(c,identity=identity) for c in classes),
                    tuple(replace(m,identity=identity) for m in measurements),
                    manufacturer_json=canonical_json_bytes(evidence).decode())
                publish(owned.client,worker.journal,codec.encode(closed),f'{sid}-0')
                requests[sid]=(f'{sid}-0',evidence['request_id'])
            metadata_turn(worker)
            self.assertEqual(owned.client.xpending(worker.journal.key(SOURCE),worker.journal.group)['pending'],0)
            self.assertEqual(review_acknowledgments(owned.client,worker.journal,requests),
                             {1:'acknowledged',2:'pending'})  # Track 2 is still unread.
            def crash(phase):
                if phase=='published':raise RuntimeError('injected failure before ACK')
            worker.journal.hook=crash
            with self.assertRaises(RuntimeError):metadata_turn(worker)
            self.assertEqual(owned.client.xlen(worker.journal.key(RESULT)),2)
            self.assertEqual(review_acknowledgments(owned.client,worker.journal,requests),
                             {1:'acknowledged',2:'pending'})  # Result exists, source still pending.
            worker.journal.hook=lambda phase:None
            metadata_turn(worker)  # Repairs the original intent and confirms source ACK.
            self.assertEqual(review_acknowledgments(owned.client,worker.journal,requests),
                             {1:'acknowledged',2:'acknowledged'})
            self.assertEqual(runtime.calls,2)
        self.assertTrue(owned.cleaned)

    def test_metadata_turn_releases_lease_before_engine_work_and_rejects_real_errors(self):
        from unittest.mock import Mock
        from redis.exceptions import ResponseError
        from vehicle.common.console_runs import metadata_turn
        worker=Mock();worker.journal.token=b'owned';worker.step.return_value=True
        self.assertTrue(metadata_turn(worker));worker.journal.release.assert_called_once()
        worker.step.side_effect=ResponseError('STALE_OWNER')
        self.assertFalse(metadata_turn(worker));self.assertIsNone(worker.journal.token)
        worker.step.side_effect=ResponseError('PAYLOAD_CONFLICT')
        with self.assertRaises(ResponseError):metadata_turn(worker)
    def test_two_tracks_join_real_policy_fields_without_confidence_or_plate_text(self):
        owner,measurements,classes=fixture()
        codec=load_codec(ORG,IDENTITY_NAMESPACE)
        with tempfile.TemporaryDirectory() as tmp:
            runtime=Runtime();ledger=ManufacturerLedger(enabled=True,runtime=runtime,journal=Path(tmp)/'ledger')
            self.addCleanup(ledger.close)
            for sid,brand in ((1,'Toyota'),(2,'unknown')):
                life=replace(owner.lifetime,service_track_id=sid,raw_track_id=100+sid)
                identity=VehicleIdentity.from_lifetime(life)
                runtime.text=json.dumps(dict(manufacturer=brand,vehicle_type='bus',color='blue'))
                ledger.observe(life,life.first_receipt,(0,0,64,64),np.zeros((64,64,3),np.uint8),'f'*64)
                closed=ClosedScalars(replace(owner,lifetime=life),tuple(replace(c,identity=identity) for c in classes),
                    tuple(replace(m,identity=identity) for m in measurements),
                    manufacturer_json=canonical_json_bytes(ledger.terminal(life)).decode())
                plate=PlateMetadata().evaluate(life,transcript(closed.owner)).result
                row,_=project_track(closed,codec,plate=plate)
                self.assertEqual((row['track_id'],row['vehicle_type'],row['colour']['label']),(sid,'car','red'))
                self.assertEqual(row['manufacturer']['make'],'toyota' if sid==1 else None)
                self.assertEqual(row['manufacturer']['frame'],0)
                self.assertEqual(row['plate']['status'],'read')
                self.assertNotIn('confidence',json.dumps(row))
                self.assertNotIn('plate_text',json.dumps(row))
                self.assertEqual(row['delivery']['redis_ack'],'not_attempted')

    def test_disabled_is_a_distinct_terminal_and_stop_prevents_new_attempts(self):
        owner,measurements,classes=fixture();stop=threading.Event();stop.set()
        ledger=ManufacturerLedger()
        closed=ClosedScalars(owner,classes,measurements,manufacturer_json=canonical_json_bytes(ledger.terminal(owner.lifetime)).decode())
        row,_=project_track(closed,load_codec(ORG,IDENTITY_NAMESPACE))
        self.assertEqual(row['manufacturer']['status'],'disabled')
        with tempfile.TemporaryDirectory() as tmp:
            runtime=Runtime();ledger=ManufacturerLedger(enabled=True,runtime=runtime,journal=Path(tmp)/'ledger',stop=stop)
            self.addCleanup(ledger.close)
            ledger.observe(owner.lifetime,owner.lifetime.first_receipt,(0,0,64,64),np.zeros((64,64,3),np.uint8),'f'*64)
            self.assertEqual(runtime.calls,0)

    def test_stop_retires_only_the_owned_manufacturer_process(self):
        import subprocess,sys,time
        runtime=LocalRuntime.__new__(LocalRuntime)
        runtime.timeout,runtime.code,runtime.children=30,{},[]
        runtime.lock={'runtime':'/usr','artifacts':{'python':{'path':sys.executable}}}
        runtime.lock_path=Path('/unused');runtime.stop=threading.Event()
        original=subprocess.Popen
        def spawn(*args,**kwargs):return original([sys.executable,'-c','import time;time.sleep(60)'],**kwargs)
        timer=threading.Timer(.1,runtime.stop.set);timer.start()
        started=time.monotonic()
        try:
            with patch('vehicle.common.manufacturer.local.subprocess.Popen',side_effect=spawn):
                with self.assertRaises(RuntimeError):runtime.infer(np.zeros((64,64,3),np.uint8))
        finally:timer.join()
        self.assertLess(time.monotonic()-started,3)
        self.assertTrue(runtime.cleaned)
