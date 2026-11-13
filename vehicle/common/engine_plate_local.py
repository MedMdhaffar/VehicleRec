"""Engine-private CPU Paddle/Ultralytics bindings; heavy imports are explicit/lazy.

Fixed plans are smoke orchestration, never Plate follow-up or read policy.
"""
from dataclasses import dataclass
import importlib.metadata
import json
import math
from numbers import Real
from pathlib import Path
from .engine_plate import KnownNotStarted,OutcomeUnknown,decode_ocr,decode_marker
from .engine_plate_artifacts import ArtifactFile,LocalArtifact,LocalExecutor
from .db1_rows.core import require
from .plate_metadata import MarkerScalar


class LocalNotStarted(KnownNotStarted):
    def __init__(self,code):self.code=code;super().__init__(code)


def artifact(task,path):
    """Inspect explicitly supplied files/installed versions, without initializing."""
    root=Path(__file__).resolve().parents[2]
    implementation=Path(__file__).resolve()
    if task=='ocr':
        import yaml
        path=Path(path).resolve(strict=True)
        name=yaml.safe_load((path/'inference.yml').read_text())['Global']['model_name']
        files=tuple(ArtifactFile.capture(n,path/n) for n in ('inference.json','inference.pdiparams','inference.yml'))
        versions=tuple((n,importlib.metadata.version(n)) for n in ('paddleocr','paddlepaddle','paddlex','numpy'))
        params=(('preprocess','local-config-BGR-RecResizeImg'),('cpu_threads','1'),('enable_mkldnn','false'),('enable_hpi','false'))
    else:
        require(task=='detect','INVALID_ARTIFACT','explicit OCR/marker task required')
        path=Path(path).resolve(strict=True)
        name=path.name # opaque local checkpoint filename; no invented training version
        files=(ArtifactFile.capture('weights',path),
               ArtifactFile.capture('marker-decoder',root/'vehicle/license_plate_service/run_video_pipeline/marker_localizer.py'),
               ArtifactFile.capture('marker-config',root/'vehicle/license_plate_service/run_video_pipeline/config.py'))
        versions=tuple((n,importlib.metadata.version(n)) for n in ('torch','ultralytics','opencv-python','numpy'))
        params=(('preprocess','BGR-direct-stretch-512-square'),('conf','0.05'),('imgsz','512'),('regions','original-full-height-outside-marker-int-box-v1'))
    return LocalArtifact(name,task,files+(ArtifactFile.capture('engine-binding',implementation),),versions,params)


class NativeCPU:
    """Synchronous installed APIs only: no timeout, threads, providers or defaults."""
    def __init__(self,identity):self.identity=identity;self.model=None;self.closed=False;self.placement={}

    def initialize(self):
        require(not self.closed,'RUNTIME_CLOSED','native runtime closed')
        self.identity.verify()
        for name,version in self.identity.versions:
            require(importlib.metadata.version(name)==version,'ARTIFACT_BINDING_CONFLICT','installed runtime version changed')
        if self.identity.task=='ocr':
            import os
            os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK']='True'
            from paddleocr import TextRecognition
            import paddle
            directory=Path(next(f.path for f in self.identity.files if f.label=='inference.yml')).parent
            self.model=TextRecognition(model_name=self.identity.upstream_name,model_dir=str(directory),device='cpu',
                enable_mkldnn=False,enable_hpi=False,cpu_threads=1)
            require(self.model._model_name==self.identity.upstream_name and Path(self.model._model_dir).resolve()==directory,
                    'ARTIFACT_BINDING_CONFLICT','initialized OCR artifact identity differs')
            self.placement=dict(device='cpu',paddle_device=paddle.get_device(),cuda_compiled=paddle.is_compiled_with_cuda())
        else:
            import torch
            from ultralytics import YOLO
            torch.set_num_threads(1)
            weights=next(f.path for f in self.identity.files if f.label=='weights')
            self.model=YOLO(weights,task='detect');self.model.to('cpu')
            require(Path(self.model.ckpt_path).resolve()==Path(weights),'ARTIFACT_BINDING_CONFLICT','loaded marker checkpoint differs')
            self.placement=dict(checkpoint_metadata={k:str(self.model.ckpt[k]) for k in ('version','date') if k in self.model.ckpt},device='cpu',parameter_devices=sorted({str(p.device) for p in self.model.model.parameters()}),
                                torch_cuda_available=torch.cuda.is_available(),torch_threads=torch.get_num_threads())

    def prepare(self,image):
        if self.identity.task=='ocr':return image
        return marker_tools().TounesMarkerLocalizer._prepare(image)

    def predict(self,image):
        if self.identity.task=='ocr':return self.model.predict(image)
        return self.model.predict([image],conf=.05,imgsz=512,device='cpu',verbose=False,stream=False)

    def close(self):
        if self.closed:return
        if self.model is not None and self.identity.task=='ocr':self.model.close()
        self.model=None;self.closed=True


def marker_tools():
    # Legacy module uses an absolute package name. Install only this existing
    # package alias; do not import legacy pipelines/providers or change defaults.
    import sys
    from vehicle.license_plate_service import run_video_pipeline
    sys.modules.setdefault('run_video_pipeline',run_video_pipeline)
    from vehicle.license_plate_service.run_video_pipeline import marker_localizer
    return marker_localizer


def raw_ocr(outputs):
    # Installed PaddleOCR 3.7 TextRecognition returns a one-element result list
    # with rec_text/rec_score (possibly under res/json), not a semantic plate read.
    require(isinstance(outputs,(list,tuple)) and len(outputs)==1,'MALFORMED_OCR','one native crop result required')
    value=outputs[0]
    if not isinstance(value,dict):value=getattr(value,'json',None)
    if isinstance(value,dict) and 'res' in value:value=value['res']
    require(isinstance(value,dict) and 'rec_text' in value and 'rec_score' in value,
            'MALFORMED_OCR','installed TextRecognition scalar response required')
    text=value['rec_text'];native_score=value['rec_score']
    require(isinstance(native_score,Real) and not isinstance(native_score,bool),'MALFORMED_OCR','native numeric score required')
    confidence=float(native_score)
    require(type(text) is str and len(text.encode())<=4096 and math.isfinite(confidence) and 0<=confidence<=1,
            'MALFORMED_OCR','bounded native text/confidence required')
    return dict(raw_text=text,confidence=confidence)


class LocalPlateBinding:
    """A shared lazy model, invoked only inside the adapter's serialized lane.

    Native response scalarization never invokes Plate policy. Invalid native
    structures produce a bounded invalid envelope for adapter decode failure.
    CPU APIs must return quiescent; OutcomeUnknown explicitly retains retirement.
    """
    def __init__(self,identity,*,runtime_factory=NativeCPU):
        self.identity=identity;self.factory=runtime_factory;self.runtime=None
        self.cleanup_pending=False
        self.active=False;self.closed=False;self.calls={};self.lineage=[];self.failures=[]

    def executor(self,name,kind):
        require((self.identity.task=='detect')==(kind=='marker'),'INVALID_EXECUTOR','native task/operation differs')
        require(kind in {'whole_ocr','marker','left_ocr','right_ocr'},'INVALID_EXECUTOR','explicit fixed operation required')
        return LocalExecutor(name,self.identity,kind,lambda req,ctx:self.invoke(kind,req,ctx),decode_marker if kind=='marker' else decode_ocr)

    def invoke(self,kind,request,context):
        import numpy as np
        require(not self.closed,'RUNTIME_CLOSED','binding closed')
        started=False;uncertain=False;self.active=True
        try:
            if request.plan.kind!=kind:raise LocalNotStarted('EXECUTOR_STAGE_CONFLICT')
            if self.cleanup_pending:raise LocalNotStarted('RUNTIME_CLEANUP_PENDING')
            try:self.identity.verify()
            except Exception as error:
                self.failures.append(dict(phase='artifact',code='ARTIFACT_BINDING_CONFLICT'))
                raise LocalNotStarted('ARTIFACT_BINDING_CONFLICT') from error
            w,h=request.candidate.crop_size
            # Copy preserves the immutable admitted allocation, including when
            # a library mutates/preprocesses its input internally.
            image=np.frombuffer(request.pixels,dtype=np.uint8).reshape(h,w,3).copy()
            box=(0,0,w,h)
            if kind in {'left_ocr','right_ocr'}:
                markers=[d for d in request.dependencies if d.kind=='marker']
                if len(markers)!=1 or not isinstance(markers[0].scalar,MarkerScalar):raise LocalNotStarted('INELIGIBLE_GEOMETRY')
                marker=markers[0].scalar
                if not marker.detected or marker.bbox is None:raise LocalNotStarted('INELIGIBLE_GEOMETRY')
                x1,_,x2,_=marker.bbox
                if not (0<x1<x2<w):raise LocalNotStarted('INELIGIBLE_GEOMETRY')
                box=(0,0,x1,h) if kind=='left_ocr' else (x2,0,w,h)
                image=image[:,box[0]:box[2]].copy()
            if self.runtime is None:
                try:
                    # Own the object before initialization: a failed close must
                    # not lose the only handle or permit another allocation.
                    runtime=self.factory(self.identity);self.runtime=runtime
                    self.cleanup_pending=True
                    try:runtime.initialize()
                    except Exception:
                        try:runtime.close()
                        except Exception as cleanup_error:
                            self.failures.append(dict(phase='cleanup',code=type(cleanup_error).__name__))
                        else:
                            self.runtime=None;self.cleanup_pending=False
                        raise
                    self.cleanup_pending=False
                except Exception as error:
                    self.failures.append(dict(phase='initialization',code=type(error).__name__))
                    raise LocalNotStarted('ARTIFACT_BINDING_CONFLICT' if getattr(error,'code',None)=='ARTIFACT_BINDING_CONFLICT' else 'INITIALIZATION_FAILED') from error
            if self.runtime.identity!=self.identity:
                raise LocalNotStarted('ARTIFACT_BINDING_CONFLICT')
            try:self.identity.verify()
            except Exception as error:raise LocalNotStarted('ARTIFACT_BINDING_CONFLICT') from error
            self.lineage.append(dict(operation_id=request.plan.operation_id,kind=kind,original_sha256=request.candidate.crop_sha256,
                original_size=(w,h),input_box=box,dependency_ids=request.plan.dependencies,model=self.identity.model.policy()))
            try:native_input=self.runtime.prepare(image)
            except Exception as error:raise LocalNotStarted('PREPARATION_FAILED') from error
            context.started(self.runtime.identity.model);started=True
            self.calls[kind]=self.calls.get(kind,0)+1
            outputs=self.runtime.predict(native_input) # actual synchronous invocation boundary
            try:
                if kind=='marker':
                    require(isinstance(outputs,(list,tuple)) and len(outputs)==1,'MALFORMED_MARKER','one native marker result required')
                    # Missing/nonfinite native fields are execution/decode failure,
                    # not invented absence. Empty actual Boxes is valid absence.
                    boxes=getattr(outputs[0],'boxes',None)
                    require(boxes is not None,'MALFORMED_MARKER','native Boxes required')
                    require(len(boxes)<=256 and len(boxes.conf.tolist())==len(boxes.xyxy.tolist())==len(boxes),
                            'MALFORMED_MARKER','bounded native box/score alignment required')
                    for coords in boxes.xyxy.tolist():
                        require(len(coords)==4 and all(math.isfinite(float(v)) for v in coords)
                            and 0<=coords[0]<coords[2]<=512 and 0<=coords[1]<coords[3]<=512,
                            'MALFORMED_MARKER','native 512-space box geometry required')
                    for conf in boxes.conf.tolist():require(math.isfinite(float(conf)) and 0<=float(conf)<=1,'MALFORMED_MARKER','native confidence')
                    decoded=marker_tools().TounesMarkerLocalizer._decode(outputs[0],image)
                    value=dict(detected=decoded.detected,confidence=decoded.confidence,bbox=decoded.bbox,center_x=decoded.center_x)
                else:value=raw_ocr(outputs)
                raw=json.dumps(value,ensure_ascii=False,allow_nan=False).encode()
                return raw if len(raw)<=65536 else b'null'
            except Exception:
                self.failures.append(dict(phase='decode',code='MALFORMED_NATIVE_OUTPUT'))
                return b'null' # decoder rejects; actual-start/model attribution survives
        except OutcomeUnknown:
            uncertain=True;self.failures.append(dict(phase='invocation',code='OUTCOME_UNKNOWN'));raise
        except LocalNotStarted:raise
        except Exception:
            self.failures.append(dict(phase='invocation' if started else 'preparation',code='NATIVE_FAILURE'));raise
        finally:
            if not uncertain:self.active=False;context.retired()

    def close(self):
        require(not self.active,'EXECUTION_UNRETIRED','native work may still use private input')
        if self.closed:return
        if self.runtime is not None:
            self.cleanup_pending=True
            self.runtime.close()
        self.cleanup_pending=False
        self.closed=True
