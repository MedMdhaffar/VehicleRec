"""Manufacturer binding, crash admission, policy and native Redis recovery."""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

import numpy as np

from contracts.db1.reference import canonical_json_bytes
from vehicle.common.db1_rows.core import Blocked
from vehicle.common.manufacturer.engine import ManufacturerLedger, hashed, vehicle_binding
from vehicle.common.manufacturer.local import LocalRuntime
from vehicle.common.manufacturer.policy import evaluate
from vehicle.common.manufacturer.streams import ReviewCodec, ReviewWorker, SOURCE, RESULT, publish
from vehicle.common.mohamed_lpr_redis_runtime import OwnedRedis
from vehicle.common.real_video.engine import ClosedScalars, Engine
from vehicle.common.real_video.input import DecodedFrame
from vehicle.common.frame_receipt import FrameReceiptClock, ClockPolicy
from vehicle.common.observation_evidence import RecognitionIdentity
from vehicle.common.redis_scalar.worker import load_codec
from vehicle.common.redis_scalar.synthetic import ORG, IDENTITY_NAMESPACE
from vehicle.common.tests.test_colour_aggregator import fixture
from vehicle.common.vehicle_observation import RunEnd

SERVER = Path('/home/intern/part-time/aziz/redis-native-validation/redis-8.2.11/src/redis-server')


class Runtime:
    provenance = 'a'*64
    def __init__(self, text='{"manufacturer":"VW","vehicle_type":"bus","color":"blue"}', error=None):
        self.text, self.error, self.calls = text, error, 0
    def infer(self, crop):
        self.calls += 1
        if self.error:
            raise self.error
        return 'completed', self.text


class ManufacturerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.owner, self.measurements, self.classes = fixture()
        self.life = self.owner.lifetime
        self.codec = ReviewCodec(load_codec(ORG, IDENTITY_NAMESPACE))

    def ledger(self, runtime=None, name='journal.sqlite'):
        ledger = ManufacturerLedger(enabled=True, runtime=runtime or Runtime(),
                                    journal=Path(self.directory.name)/name)
        self.addCleanup(ledger.close)
        return ledger

    def observe(self, ledger, life=None):
        life = life or self.life
        ledger.observe(life, life.first_receipt, (0,0,64,64),
                       np.zeros((64,64,3), dtype=np.uint8), 'f'*64)
        return ledger.terminal(life)

    def closed(self, evidence):
        return ClosedScalars(self.owner, self.classes, self.measurements,
                             manufacturer_json=canonical_json_bytes(evidence).decode())

    def test_two_vehicles_join_correct_track_and_keep_type_colour(self):
        runtime = Runtime()
        ledger = self.ledger(runtime)
        first = self.observe(ledger)
        other = replace(self.life, service_track_id=2, raw_track_id=20)
        runtime.text = '{"manufacturer":"Renault","vehicle_type":"suv","color":"black"}'
        second = self.observe(ledger, other)
        self.assertEqual(runtime.calls, 2)
        self.assertEqual((evaluate(self.life,first).make, evaluate(other,second).make), ('volkswagen','renault'))
        with self.assertRaises(Blocked):
            evaluate(self.life,second)
        joined = self.codec.join(self.codec.encode(self.closed(first)))
        facts = joined.delivery.payload.value()['facts']
        self.assertEqual(joined.manufacturer_review.make, 'volkswagen')
        self.assertEqual(facts[0]['delivery']['class']['name'], 'car')
        self.assertEqual(facts[1]['delivery']['colour'], 'red')
        self.assertEqual(len(facts), 2)  # explicit review-only DB1 boundary
        self.assertIsNone(joined.manufacturer_review.confidence)
        self.assertIsNone(joined.manufacturer_review.vehicle_model)

    def test_unknown_malformed_duplicate_keys_and_no_verbal_confidence(self):
        for index, (text, expected) in enumerate((
            ('{"manufacturer":"unknown","vehicle_type":"sedan","color":"white"}', 'unknown'),
            ('{"manufacturer":"probably Toyota","vehicle_type":"sedan","color":"white"}', 'unknown'),
            ('{"manufacturer":"Toyota Corolla","vehicle_type":"sedan","color":"white"}', 'unknown'),
            ('{"manufacturer":"new_unsupported_brand","vehicle_type":"sedan","color":"white"}', 'unknown'),
            ('broken', 'malformed'),
            ('{"manufacturer":"toyota","manufacturer":"ford","vehicle_type":"sedan","color":"white"}', 'malformed'),
            ('{"manufacturer":"toyota","vehicle_type":"sedan","color":"white","confidence":0.99}', 'malformed'),
        )):
            with self.subTest(text=text):
                evidence = self.observe(self.ledger(Runtime(text),name=f'{index}.sqlite'))
                decision = evaluate(self.life,evidence)
                self.assertEqual(decision.status, expected)
                self.assertEqual(self.codec.join(self.codec.encode(self.closed(evidence))).state, 'ready')

    def test_duplicate_crop_redelivery_and_restart_never_invoke_again(self):
        runtime = Runtime()
        ledger = self.ledger(runtime)
        evidence = self.observe(ledger)
        self.assertEqual(self.observe(ledger), evidence)
        again = self.ledger(runtime)
        self.assertEqual(self.observe(again), evidence)
        self.assertEqual(runtime.calls,1)

    def test_unknown_start_is_not_retried(self):
        class Crash(BaseException): pass
        class Crashing(Runtime):
            def infer(self,crop):
                self.calls += 1
                raise Crash()
        runtime = Crashing()
        ledger = self.ledger(runtime)
        with self.assertRaises(Crash):
            self.observe(ledger)
        restarted = self.ledger(runtime)
        evidence = self.observe(restarted)
        self.assertEqual((runtime.calls,evidence['outcome']), (1,'execution_uncertain'))
        self.assertEqual(self.codec.join(self.codec.encode(self.closed(evidence))).state,'ready')

    def test_timeout_and_failure_settle_and_are_not_retried(self):
        for index, (error, outcome) in enumerate(((TimeoutError(),'timed_out'),(RuntimeError(),'failed'))):
            runtime = Runtime(error=error)
            ledger = self.ledger(runtime,name=f'{index}.sqlite')
            evidence = self.observe(ledger)
            self.assertEqual(evaluate(self.life,evidence).status,outcome)
            self.assertEqual(self.observe(ledger),evidence)
            self.assertEqual(runtime.calls,1)
            self.assertEqual(self.codec.join(self.codec.encode(self.closed(evidence))).state,'ready')

    def test_disabled_opens_no_journal_and_settles_without_native_call(self):
        ledger = ManufacturerLedger()
        evidence = ledger.terminal(self.life)
        self.assertEqual((ledger.calls,ledger.db), (0,None))
        joined = self.codec.join(self.codec.encode(self.closed(evidence)))
        self.assertEqual((joined.state,joined.manufacturer_review.status),('ready','disabled'))

    def test_stale_run_frame_crop_hash_and_tampered_policy_are_rejected(self):
        evidence = self.observe(self.ledger())
        old_context = replace(self.life.context, run_id='old-run')
        with self.assertRaises(Blocked):
            evaluate(replace(self.life,context=old_context), evidence)
        for field, value in (('crop_sha256','b'*64),('receipt',{**evidence['binding']['receipt'],'frame_index':99})):
            bad = json.loads(json.dumps(evidence));bad['binding'][field]=value
            if field == 'receipt':bad['request_id']=hashed(bad['binding'])
            with self.assertRaises(Blocked):evaluate(self.life,bad)
        decision = evaluate(self.life,evidence)
        with self.assertRaises(Blocked):replace(decision,make='ford').validate(self.life)

    def test_missing_eligible_crop_is_unknown_with_zero_calls(self):
        runtime = Runtime();ledger = self.ledger(runtime)
        ledger.observe(self.life,self.life.first_receipt,(0,0,16,16),np.zeros((16,16,3),np.uint8),'f'*64)
        self.assertEqual((runtime.calls,evaluate(self.life,ledger.terminal(self.life)).status),(0,'unknown'))

    def test_failed_manufacturer_preserves_enabled_plate_terminal(self):
        from vehicle.common.plate_metadata import PlateMetadata
        from vehicle.common.tests.test_plate_metadata import transcript
        plate = PlateMetadata().evaluate(self.life,transcript(self.owner)).result
        evidence = self.observe(self.ledger(Runtime(error=TimeoutError())))
        joined = self.codec.join(self.codec.encode(self.closed(evidence)),plate=plate)
        self.assertEqual(joined.state,'ready')
        self.assertEqual(joined.manufacturer_review.status,'timed_out')
        self.assertEqual([f['row_kind'] for f in joined.delivery.payload.value()['facts']],
                         ['fact_vehicle','fact_vehicle_colour','fact_plate_read'])

    def test_combined_engine_serializes_and_counts_manufacturer_native_calls(self):
        from vehicle.common.real_video.plate_engine import CombinedEngine
        from vehicle.common.real_video.plate_local import PlateDetections
        runtime = Runtime();ledger = self.ledger(runtime)
        class Vehicles:
            def process(self,pixels):return np.array([[0,0,64,64,7,.9,2,0]])
            def close(self):pass
        class Plates:
            def process(self,pixels):return PlateDetections((),())
            def close(self):pass
        clock = FrameReceiptClock(self.life.context,utc_clock=lambda:self.life.first_receipt.utc,
                                  monotonic_clock=lambda:100.,policy=ClockPolicy())
        engine = CombinedEngine(self.life.context,clock,Vehicles(),Plates(),{},
            algorithm=RecognitionIdentity('test-hsv',1,'classify'),quality_algorithm='test-quality',
            plate_quality_algorithm='test-plate-quality',estimate=lambda c:SimpleNamespace(label='red',confidence=.8),
            quality_fn=lambda *a:SimpleNamespace(combined=.9),manufacturer=ledger)
        self.addCleanup(engine.close)
        engine.process(DecodedFrame(clock.capture(0,0.),np.zeros((64,64,3),np.uint8)))
        engine.finish(RunEnd.SERVICE_STOP)
        self.assertEqual(engine.audit.calls,{'vehicle':1,'manufacturer':1,'plate':1})
        self.assertEqual(engine.audit.max_active,1)

    def test_real_engine_shares_existing_two_vehicle_crops(self):
        runtime = Runtime();ledger = self.ledger(runtime)
        class Detector:
            calls = 0
            def process(self,pixels):
                self.calls += 1
                return np.array([[0,0,64,64,7,.9,2,0],[64,0,128,64,8,.9,2,0]])
            def close(self): pass
        detector = Detector();receipts = [s.receipt for s in self.classes]
        clock = FrameReceiptClock(self.life.context,utc_clock=lambda:receipts[0].utc,
                                  monotonic_clock=lambda:receipts[0].monotonic,policy=ClockPolicy())
        engine = Engine(self.life.context,clock,detector,algorithm=RecognitionIdentity('test-hsv',1,'classify'),
                        quality_algorithm='test-quality',estimate=lambda c:SimpleNamespace(label='red',confidence=.8),
                        quality=lambda *a:SimpleNamespace(combined=.9),manufacturer=ledger)
        self.addCleanup(engine.close)
        for original in receipts[:2]:
            receipt = clock.capture(original.frame_index, original.media_pts)
            engine.process(DecodedFrame(receipt,np.zeros((64,128,3),np.uint8)))
        engine.finish(RunEnd.SERVICE_STOP)
        self.assertEqual((detector.calls,runtime.calls,len(engine.pending)),(2,2,2))
        self.assertEqual({self.codec.join(self.codec.encode(c)).manufacturer_review.identity.service_track_id
                          for c in engine.pending.values()},set(engine.pending))

    def test_owned_process_timeout_confirms_death(self):
        runtime = LocalRuntime.__new__(LocalRuntime)
        runtime.timeout, runtime.code, runtime.children = .1, {}, []
        runtime.lock = {'runtime':'/usr', 'artifacts':{'python':{'path':sys.executable}}}
        runtime.lock_path = Path(self.directory.name)/'unused.json'
        popen = subprocess.Popen
        def sleepy(*args,**kwargs):
            return popen([sys.executable,'-c','import time; time.sleep(60)'],**kwargs)
        with patch('vehicle.common.manufacturer.local.subprocess.Popen',side_effect=sleepy):
            with self.assertRaises(TimeoutError):runtime.infer(np.zeros((64,64,3),np.uint8))
        self.assertTrue(runtime.cleaned)


class ManufacturerRedisTests(unittest.TestCase):
    setUp = ManufacturerTests.setUp
    ledger = ManufacturerTests.ledger
    observe = ManufacturerTests.observe
    closed = ManufacturerTests.closed
    def test_native_redis_duplicate_and_repaired_publication(self):
        evidence = self.observe(self.ledger())
        raw = self.codec.encode(self.closed(evidence))
        with OwnedRedis(SERVER) as owned:
            worker = ReviewWorker(owned.client,codec=self.codec,namespace='make-'+uuid4().hex[:16])
            worker.setup()
            publish(owned.client,worker.journal,raw,'1-0')
            def crash(phase):
                if phase == 'published':raise RuntimeError('injected reply loss')
            worker.journal.hook = crash
            with self.assertRaises(RuntimeError):worker.step()
            self.assertEqual(owned.client.xlen(worker.journal.key(RESULT)),1)
            self.assertEqual(owned.client.xpending(worker.journal.key(SOURCE),worker.journal.group)['pending'],1)
            worker.journal.hook = lambda phase:None
            worker.step()
            publish(owned.client,worker.journal,raw,'2-0')
            worker.step()
            self.assertEqual(owned.client.xlen(worker.journal.key(RESULT)),1)
            self.assertEqual(owned.client.xpending(worker.journal.key(SOURCE),worker.journal.group)['pending'],0)
            output = json.loads(owned.client.xrange(worker.journal.key(RESULT))[0][1][b'json'])
            self.assertEqual((output['make'],output['db1_status']),('volkswagen','review_only'))
            changed = json.loads(raw)
            changed['body']['evidence']['text'] = '{"manufacturer":"ford","vehicle_type":"bus","color":"blue"}'
            changed['body_sha256'] = sha256(canonical_json_bytes(changed['body'])).hexdigest()
            publish(owned.client,worker.journal,canonical_json_bytes(changed),'3-0')
            with self.assertRaises(Blocked):worker.step()
            self.assertEqual(owned.client.xlen(worker.journal.key(RESULT)),1)
            # Rejected changed delivery remains pending; no made-up terminal ACK.
            self.assertEqual(owned.client.xpending(worker.journal.key(SOURCE),worker.journal.group)['pending'],1)
            worker.journal.release()
        self.assertTrue(owned.cleaned)
