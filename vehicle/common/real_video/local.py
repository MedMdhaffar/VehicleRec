"""Explicit heavy Engine construction from local artifacts only."""
import hashlib
import importlib.metadata
import os
from pathlib import Path
from ..observation_evidence import RecognitionIdentity
from ..db1_rows.core import require
from contracts.db1.reference import canonical_json_bytes

ROOT=Path(__file__).resolve().parents[3]
VA=ROOT/'vehicle/vehicle_classification_manufacturer_service/vehicle_classification_manufacturer_service'
VC=ROOT/'vehicle/vehicle_color_service/vehicle_color_service'


def fingerprint(path):
    path=Path(path).resolve()
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    return dict(path=str(path),bytes=path.stat().st_size,sha256=digest.hexdigest())


def identities(model,tracker):
    from vehicle.vehicle_color_service.vehicle_color_service import config as cc
    from vehicle.vehicle_classification_manufacturer_service.vehicle_classification_manufacturer_service import config as dc
    from vehicle.vehicle_classification_manufacturer_service.vehicle_classification_manufacturer_service.classification import VEHICLE_TYPE_CONF_THRESHOLD
    inputs=dict(weights=fingerprint(model),tracker=fingerprint(tracker),
        code=[fingerprint(p) for p in [VC/'colour.py',VC/'temporal.py',VC/'scalar_policy.py',VC/'config.py',
             VA/'detector.py',VA/'classification.py',VA/'pipeline.py',VA/'config.py',
             ROOT/'vehicle/common/real_video/engine.py',ROOT/'vehicle/common/real_video/input.py',
             ROOT/'vehicle/common/real_video/host.py',ROOT/'vehicle/common/real_video/local.py',
             ROOT/'scripts/vehicle_real_video_smoke.py']],
        colour_config={k:getattr(cc,k) for k in dir(cc) if k.startswith('COLOUR_') or k in
           {'COLOR_QUALITY_SATURATION_FRAC','MIN_VALID_COLOR_SAMPLES','MAX_VALID_COLOR_SAMPLES',
            'SHORT_TRACK_WINNER_SHARE','SHORT_TRACK_WINNER_MARGIN','FINAL_WINNER_SHARE','FINAL_WINNER_MARGIN',
            'EARLY_LOCK_WINNER_SHARE','EARLY_LOCK_MARGIN'}},
        detector_config=dict(conf=dc.TRACK_CONF,imgsz=640,class_confidence_threshold=VEHICLE_TYPE_CONF_THRESHOLD,
            lost_frames=dc.LOST_FRAMES),
        modes=dict(roi='multi',normalization='none',neutral='adaptive',feature='hsv',quality='enhanced'),
        packages={n:importlib.metadata.version(n) for n in ['torch','ultralytics','opencv-python','numpy','redis','jsonschema','lap']})
    material=dict(weights_sha256=inputs['weights']['sha256'],tracker_sha256=inputs['tracker']['sha256'],
        code={Path(v['path']).relative_to(ROOT).as_posix():v['sha256'] for v in inputs['code']},
        colour_config=inputs['colour_config'],detector_config=inputs['detector_config'],
        modes=inputs['modes'],packages=inputs['packages'])
    digest=hashlib.sha256(canonical_json_bytes(material)).hexdigest()
    inputs['execution_fingerprint']=digest
    inputs['detector_name']='local-yolo26-sha256:'+inputs['weights']['sha256']
    inputs['colour_name']='local-hsv-sha256:'+digest
    inputs['quality_name']='crop-quality-enhanced-sha256:'+digest
    return inputs


class LocalDetector:
    def __init__(self,model,tracker,*,device,frame_rate,threads):
        # Apply before importing the inference library. No provider path is loaded.
        os.environ['YOLO_OFFLINE']='true'
        os.environ['YOLO_AUTOINSTALL']='false'
        os.environ['COLOR_AGENT_ENABLED']='false'
        require(Path(model).is_file() and Path(tracker).is_file(),'MISSING_LOCAL_ARTIFACT','local model/tracker required')
        import torch
        from vehicle.vehicle_classification_manufacturer_service.vehicle_classification_manufacturer_service.detector import VehicleDetectorTracker
        selected = torch.device(device)
        require(device=='cpu' or (selected.type=='cuda' and selected.index is not None and torch.cuda.is_available()
                and selected.index < torch.cuda.device_count()),'INVALID_DEVICE','explicit available device required')
        torch.set_num_threads(threads)
        self.target=VehicleDetectorTracker(device=device,model_path=model,tracker_path=tracker,frame_rate=frame_rate)
        self.device=device
        self.prediction_calls,self.forward_calls=0,0
        self.closed=False
        self.hook=self.target.model.model.register_forward_pre_hook(self._forward)
        self.placement=dict(requested=device,resolved=self.target.device,cuda_available=torch.cuda.is_available(),
            torch_threads=torch.get_num_threads(),gpu_name=torch.cuda.get_device_name(selected) if selected.type=='cuda' else None,
            model_names={str(k):str(v) for k,v in self.target.model.names.items()},
            checkpoint_ultralytics_version=str(self.target.model.ckpt.get('version','unrecorded')))

    def _forward(self,*_):self.forward_calls+=1

    def process(self,pixels):
        self.prediction_calls+=1
        index = getattr(self, 'source_index', None)
        update = self.target.tracker.update
        if index is not None:
            from ..console_sampling import advance_tracker
            gap = index-getattr(self, 'previous_source_index', index-1)
            def sampled(results, image, *args, **kwargs):
                advance_tracker(self.target.tracker, gap, results[:0], image, update=update)
                return update(results, image, *args, **kwargs)
            self.target.tracker.update = sampled
        try:result=self.target.process(pixels)
        finally:self.target.tracker.update=update
        if index is not None:self.previous_source_index=index
        self.placement['parameter_devices']=sorted({str(p.device) for p in self.target.model.model.parameters()})
        return result

    def close(self):
        if not self.closed:
            import gc
            import torch
            self.hook.remove()
            self.target=None
            gc.collect()
            if self.device!='cpu':torch.cuda.empty_cache()
            self.closed=True


def pixel_operations():
    from vehicle.vehicle_color_service.vehicle_color_service.colour import estimate_colour_detailed
    from vehicle.vehicle_color_service.vehicle_color_service.temporal import compute_colour_crop_quality_details
    def estimate(crop):
        return estimate_colour_detailed(crop,roi_mode='multi',normalization='none',neutral_mode='adaptive',feature_mode='hsv')
    def quality(crop,shape,bbox):
        return compute_colour_crop_quality_details(crop,shape,bbox,mode='enhanced')
    return estimate,quality
