"""Injected decode/detector tests; real image/model execution is a separate smoke."""
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
import os
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from vehicle.common.tests.test_colour_aggregator import fixture
from vehicle.common.frame_receipt import FrameReceiptClock,ClockPolicy
from vehicle.common.real_video.input import MP4Input
from vehicle.common.real_video.engine import Engine
from vehicle.common.real_video.host import Publisher,run_capture,reference_bundle
from vehicle.common.redis_scalar.worker import load_codec
from vehicle.common.redis_scalar.synthetic import ORG,IDENTITY_NAMESPACE
from vehicle.common.redis_scalar.journal import Journal
from vehicle.common.observation_evidence import RecognitionIdentity


class Capture:
    def __init__(self,frames,events):self.frames=list(frames);self.total_frames=len(self.frames);self.events=events;self.released=False;self.reads=0
    def isOpened(self):return True
    def read(self):
        self.events.append('decode');self.reads+=1
        return (True,self.frames.pop(0)) if self.frames else (False,None)
    def get(self,key):return self.total_frames if key==7 else self.reads*40 if key==0 else 25.0
    def release(self):self.released=True


class Detector:
    def __init__(self,events):self.events=events;self.calls=0;self.closed=False
    def process(self,frame):
        self.events.append('detect');self.calls+=1
        return np.array([[0,0,8,8,7,.9,2,0]],dtype=float)
    def close(self):self.closed=True


class VideoBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path=Path(self.directory.name)/'input.mp4';self.path.write_bytes(b'fake test capture')
        owner,_,_=fixture()
        self.context=owner.lifetime.context
        self.events=[]
        self.count=0
        def utc():self.events.append('utc');return self.context.started_at+timedelta(seconds=self.count)
        def mono():self.events.append('monotonic');self.count+=1;return 100+self.count-1
        self.clock=FrameReceiptClock(self.context,utc_clock=utc,monotonic_clock=mono,policy=ClockPolicy())
        self.detector=Detector(self.events)
        self.engine=Engine(self.context,self.clock,self.detector,algorithm=RecognitionIdentity('unit-local-hsv',1,'classify'),
            quality_algorithm='unit-quality',estimate=lambda c:SimpleNamespace(label='red',confidence=.8),
            quality=lambda c,s,b:SimpleNamespace(combined=.9))
        self.codec=load_codec(ORG,IDENTITY_NAMESPACE)

    def source(self,count):
        self.capture=Capture([np.ones((16,16,3),dtype=np.uint8) for _ in range(count)],self.events)
        self.opens=0
        def factory(path):self.opens+=1;return self.capture
        return MP4Input(self.path,self.clock,capture_factory=factory)

    def test_eof_one_decode_pass_receipt_before_inference_and_resource_release(self):
        source=self.source(3);evidence=[];receipts=[]
        result=run_capture(source,self.engine,evidence.append,max_frames=12,max_seconds=20,
            on_frame=lambda packet:receipts.append(packet.receipt))
        self.assertEqual((result['reason'],source.decoded,self.opens,self.detector.calls),('eof',3,1,3))
        self.assertEqual(self.events[:4],['decode','utc','monotonic','detect'])
        self.assertEqual(self.count,3)  # EOF does not create a replacement receipt.
        self.assertTrue(self.capture.released and self.detector.closed)
        e=evidence[0]
        self.assertEqual(e.owner.lifetime.closure.value,'end_of_input')
        for i,(c,m) in enumerate(zip(e.classes,e.measurements)):
            self.assertIs(c.receipt,receipts[i]);self.assertIs(m.receipt,receipts[i])
            self.assertEqual(m.receipt.media_pts,(i+1)*.04)
        reference_bundle(self.codec,e)
        self.assertEqual(self.engine.pending,{})

    def test_frame_limit_and_controlled_stop_are_truthful(self):
        source=self.source(10);closed=[]
        result=run_capture(source,self.engine,closed.append,max_frames=2,max_seconds=20)
        self.assertEqual((result['reason'],source.decoded),('frame_limit',2))
        self.assertEqual(closed[0].owner.lifetime.closure.value,'service_stop')
        self.assertEqual(self.capture.reads,2)

    def test_decode_failure_before_declared_end_is_not_eof(self):
        source=self.source(1)
        self.capture.total_frames=21
        with source:
            self.assertIsNotNone(source.read())
            with self.assertRaisesRegex(RuntimeError,'mp4_decode_failed_before_declared_eof'):
                source.read()
        self.assertEqual(source.decoded,1)
        self.assertTrue(self.capture.released)

    def test_controlled_stop_closes_after_last_captured_receipt(self):
        source=self.source(10);closed=[]
        result=run_capture(source,self.engine,closed.append,max_frames=12,max_seconds=20,
                           stop_requested=lambda:self.detector.calls==1)
        self.assertEqual((result['reason'],source.decoded),('controlled_stop',1))
        self.assertEqual(closed[0].owner.lifetime.closure.value,'service_stop')
        self.assertTrue(self.detector.closed and self.capture.released)

    def test_wall_window_stops_without_decoding_or_fabricating_evidence(self):
        source=self.source(10);closed=[];ticks=iter([0,2,3])
        result=run_capture(source,self.engine,closed.append,max_frames=12,max_seconds=1,monotonic=lambda:next(ticks))
        self.assertEqual((result['reason'],source.decoded,closed),('time_limit',0,[]))
        self.assertTrue(self.capture.released and self.detector.closed)

    def test_partial_publication_failure_retains_closed_evidence_for_identical_retry(self):
        class Client:
            def __init__(self):self.fail=True;self.calls=[]
            def eval(self,*args):
                self.calls.append(args)
                if self.fail and len(self.calls)==2:raise ConnectionError('injected batch reply loss')
                return b'1-0'
        client=Client()
        journal=Journal(None,namespace='test',trusted_org=ORG,role='colour',consumer='test')
        publisher=Publisher(client,journal,self.codec)
        source=self.source(3)
        with self.assertRaises(ConnectionError):
            run_capture(source,self.engine,publisher.publish,max_frames=3,max_seconds=20)
        self.assertEqual(len(self.engine.pending),1)
        self.assertEqual(len(self.engine.pipeline.pending_finalized_observations(owner=self.engine)),1)
        self.assertTrue(self.capture.released and self.detector.closed)
        evidence=next(iter(self.engine.pending.values()))
        count=self.detector.calls
        client.fail=False;publisher.publish(evidence)
        self.assertEqual(client.calls[:2],client.calls[2:])
        self.assertEqual(self.detector.calls,count)
        self.engine.acknowledge(evidence.owner.service_track_id)
        self.assertEqual(self.engine.pending,{})

    def test_provider_defaults_cannot_enable_a_route(self):
        with patch.dict(os.environ,{'COLOR_AGENT_ENABLED':'true'}):
            source=self.source(3);closed=[]
            run_capture(source,self.engine,closed.append,max_frames=3,max_seconds=20)
        self.assertIs(self.engine.agent_enabled,False)
        self.assertEqual(reference_bundle(self.codec,closed[0]).payload.value()['facts'][1]['delivery']['colour'],'red')

    def test_engine_does_not_compute_class_consensus(self):
        target='vehicle.vehicle_classification_manufacturer_service.vehicle_classification_manufacturer_service.pipeline.aggregate_vehicle_type'
        with patch(target,side_effect=AssertionError('Engine chose class consensus')):
            closed=[]
            run_capture(self.source(3),self.engine,closed.append,max_frames=3,max_seconds=20)
        reference=reference_bundle(self.codec,closed[0])
        self.assertEqual(reference.payload.value()['facts'][0]['delivery']['class']['name'],'car')

    def test_bound_exhaustion_blocks_without_truncation(self):
        source=self.source(21)
        # Exercise Engine's own bound as a defensive check, independently of the
        # public host window's <=20 frame precondition.
        with source:
            for _ in range(20):self.engine.process(source.read())
            with self.assertRaisesRegex(ValueError,'no truncation'):self.engine.process(source.read())
        self.engine.finish();self.engine.close()
        e=next(iter(self.engine.pending.values()))
        self.assertEqual((len(e.classes),len(e.measurements)),(20,20))
        self.assertEqual(e.owner.lifetime.frames_observed,21)
        with self.assertRaisesRegex(ValueError,'blocked Engine evidence'):reference_bundle(self.codec,e)


if __name__=='__main__':unittest.main()
