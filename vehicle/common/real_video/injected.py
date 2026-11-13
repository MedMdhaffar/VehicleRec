"""Explicit synthetic capture/native bindings for the bounded coordination runner.

This is not an inference accuracy demonstration or registered production input.
All detector/OCR responses are labelled injected fixtures; canonical policy is
unchanged. Input still owns one capture and paired successful-decode receipts.
"""
from dataclasses import replace
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile,time
import numpy as np
from ..db1_context import CameraRegistration,ModelRegistration,ResolutionInfo,RunContext
from ..frame_receipt import FrameReceiptClock,ClockPolicy
from ..observation_evidence import RecognitionIdentity
from ..engine_plate_artifacts import LocalArtifact,ArtifactFile
from ..engine_plate_local import LocalPlateBinding
from .input import MP4Input
from .plate_engine import CombinedEngine,VideoPlateLimits
from .plate_local import PlateDetections


class Capture:
    def __init__(self,count):self.count=count;self.decoded=0;self.released=False
    def isOpened(self):return not self.released
    def read(self):
        if self.decoded>=self.count:return False,None
        self.decoded+=1
        return True,np.full((100,160,3),50,dtype=np.uint8)
    def get(self,key):
        import cv2
        return {cv2.CAP_PROP_FPS:25.,cv2.CAP_PROP_FRAME_COUNT:self.count,cv2.CAP_PROP_FRAME_WIDTH:160,
                cv2.CAP_PROP_FRAME_HEIGHT:100,cv2.CAP_PROP_POS_MSEC:self.decoded*40.}.get(key,0.)
    def release(self):self.released=True


class Vehicles:
    def process(self,image):return np.array([[0,0,100,90,7,.9,2,0],[110,0,155,90,8,.8,2,0]],dtype=float)
    def close(self):pass


class Plates:
    def process(self,image):return PlateDetections(((40,60,80,72,.9),),((1,(40,60,80,72,.9)),))
    def close(self):pass


class Runtime:
    def __init__(self,identity):self.identity=identity;self.placement=dict(device='cpu',runtime='injected')
    def initialize(self):pass
    def prepare(self,image):return image
    def predict(self,image):
        if self.identity.task=='ocr':return [dict(rec_text='123 تونس 4567',rec_score=.96)]
        boxes=SimpleNamespace(xyxy=np.array([[204.8,0,307.2,512]]),conf=np.array([.95]))
        class Boxes:
            xyxy=boxes.xyxy;conf=boxes.conf
            def __len__(self):return len(self.conf)
        return [SimpleNamespace(boxes=Boxes())]
    def close(self):pass


class InjectedVideo:
    def __init__(self,*,org,run,camera,timezone_name,frames=4,seconds=60):
        self.directory=tempfile.TemporaryDirectory(prefix='waycon-injected-')
        root=Path(self.directory.name);artifact=root/'injected.fixture';artifact.write_bytes(b'explicit injected native fixture v1')
        source=root/'injected.mp4';source.write_bytes(b'injected capture factory; not media')
        now=datetime.now(timezone.utc)
        self.context=RunContext(org,CameraRegistration(org,camera,None,timezone_name,now,None),run,'file',now,'bounded-injected-runner-v1',
            ResolutionInfo(None,None,'vehicle',ModelRegistration(None,'injected-detector-v1',1,'detect'),None,'explicit synthetic capture',False),'development')
        self.clock=FrameReceiptClock(self.context,utc_clock=lambda:datetime.now(timezone.utc),monotonic_clock=time.monotonic,policy=ClockPolicy())
        self.capture=Capture(frames)
        self.source=MP4Input(source,self.clock,capture_factory=lambda _:self.capture)
        self.bindings={}
        for task in ('ocr','detect'):
            identity=LocalArtifact('injected-runner-native',task,(ArtifactFile.capture('fixture',artifact),),(('injected-runtime','1'),),())
            self.bindings[task]=LocalPlateBinding(identity,runtime_factory=Runtime)
        self.engine=CombinedEngine(self.context,self.clock,Vehicles(),Plates(),self.bindings,
            algorithm=RecognitionIdentity('injected-colour-v1',1,'classify'),quality_algorithm='injected-quality-v1',
            plate_quality_algorithm='injected-plate-quality-v1',estimate=lambda c:SimpleNamespace(label='red',confidence=.8),
            quality_fn=lambda c,s,b:SimpleNamespace(combined=.9),plate_quality_fn=lambda c,conf,s,b:float(c.size),
            limits=VideoPlateLimits(max_frames=frames,max_seconds=seconds))

    def close(self):
        self.source.close();self.engine.close()
        self.directory.cleanup() # synthetic source/artifact only; no crops or broker evidence on disk
