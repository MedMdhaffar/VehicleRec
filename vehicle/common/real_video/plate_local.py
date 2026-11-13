"""Explicit local Plate detector/tracker, using existing native implementations."""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import importlib.metadata
from ..db1_rows.core import require
from ..engine_plate_artifacts import ArtifactFile,LocalArtifact
from ..engine_plate_local import marker_tools


def geometry():
    marker_tools() # existing package alias only; no provider/pipeline import
    from vehicle.license_plate_service.run_video_pipeline import crop_geometry,plate_detector
    return crop_geometry,plate_detector


def detector_artifact(weights,tracker,*,frame_rate):
    root=Path(__file__).resolve().parents[3]
    _,tools=geometry();config=tools.config
    return LocalArtifact(Path(weights).name,'detect',tuple(ArtifactFile.capture(name,path) for name,path in (
        ('weights',weights),('tracker',tracker),('native-plate-detector',root/'vehicle/license_plate_service/run_video_pipeline/plate_detector.py'),
        ('geometry',root/'vehicle/license_plate_service/run_video_pipeline/crop_geometry.py'),
        ('plate-config',root/'vehicle/license_plate_service/run_video_pipeline/config.py'),('binding',__file__))),
        tuple((n,importlib.metadata.version(n)) for n in ('torch','ultralytics','numpy','opencv-python','lap')),
        (('conf',str(config.PLATE_CONF)),('imgsz',str(config.DETECT_IMGSZ)),('dedup_iou',str(config.PLATE_DEDUP_IOU)),('association_padding','0.18'),('frame_rate',str(frame_rate)),('segment_disappearance_frames','5'),('coordinates','full-frame-int-truncate-clamp-v1')))


class TrackerIDs:
    """Isolate installed ByteTrack's documented process-global BaseTrack counter.

    All uses are under the host native lane. Plate construction/update must not
    reset or consume vehicle IDs; each tracker's own returned IDs remain intact.
    """
    def __init__(self,counter):self.counter=counter;self.value=0
    @contextmanager
    def use(self):
        previous=self.counter._count;self.counter._count=self.value
        try:yield
        finally:self.value=self.counter._count;self.counter._count=previous


class NativePlateDetector:
    """No capture, vehicle inference, text policy or implicit model retrieval.

    Caller holds native_access for initialization/process/close. CPU APIs are
    synchronous; completed return is not inferred from a deadline/Future state.
    """
    def __init__(self,identity,*,frame_rate):
        self.identity=identity;self.frame_rate=frame_rate
        self.detector=None;self.tracker=None;self.calls=0;self.forwards=0;self.hook=None
        self.closed=False;self.placement={};self.ids=None

    def initialize(self):
        require(not self.closed,'RUNTIME_CLOSED','Plate detector closed');self.identity.verify()
        if self.tracker is not None:return
        import torch,yaml
        from ultralytics.trackers.byte_tracker import BYTETracker
        from ultralytics.trackers.basetrack import BaseTrack
        _,tools=geometry()
        paths={f.label:f.path for f in self.identity.files}
        self.detector=tools.PlateDetector(paths['weights'],device='cpu')
        self.detector.load() # missing artifact must fail, never become []
        self.detector._model.to('cpu')
        self.hook=self.detector._model.model.register_forward_pre_hook(self._forward)
        args=SimpleNamespace(**yaml.safe_load(Path(paths['tracker']).read_text()))
        self.ids=TrackerIDs(BaseTrack)
        with self.ids.use():self.tracker=BYTETracker(args,frame_rate=self.frame_rate)
        self.placement=dict(device='cpu',parameter_devices=sorted({str(p.device) for p in self.detector._model.model.parameters()}),
            model_names={str(k):str(v) for k,v in self.detector._model.names.items()},
            checkpoint_version=str(self.detector._model.ckpt.get('version','unrecorded')),torch_threads=torch.get_num_threads(),
            tracker_frame_rate=self.frame_rate)

    def _forward(self,*_):self.forwards+=1

    def process(self,image):
        self.initialize();self.identity.verify()
        g,tools=geometry();self.calls+=1
        params=dict(self.identity.parameters)
        raw=tuple(self.detector.predict(image,conf=float(params['conf']),imgsz=int(params['imgsz'])))
        # Preserve raw API rows as well as canonical duplicate suppression.
        detections=tools.deduplicate_detections(raw,iou_threshold=float(params['dedup_iou']))
        with self.ids.use():
            index = getattr(self, 'source_index', None)
            if index is not None:
                from ..console_sampling import advance_tracker
                advance_tracker(self.tracker,index-getattr(self,'previous_source_index',index-1),g.TrackerDetections.from_tuples(()),image)
                self.previous_source_index=index
            tracked=self.tracker.update(g.TrackerDetections.from_tuples(detections,association_padding=.18),image)
        used=set();matched=[]
        for row in tracked:
            detection=g._detection_for_track(row,detections,.18,used)
            if detection is not None:matched.append((int(row[4]),tuple(detection)))
        unmatched=tuple(tuple(d) for i,d in enumerate(detections) if i not in used)
        return PlateDetections(raw,tuple(matched),unmatched,len(raw)-len(detections))

    def close(self):
        if self.closed:return
        if self.hook is not None:self.hook.remove();self.hook=None
        self.detector=None;self.tracker=None;self.closed=True


class PlateDetections:
    def __init__(self,raw,tracked,untracked=(),suppressed=0):
        self.raw=tuple(raw);self.tracked=tuple(tracked);self.untracked=tuple(untracked);self.suppressed=suppressed


def crop_in_frame(pixels,detection,*,origin=(0,0)):
    """Detector-local int rectangle → frame-space clipped original-resolution ROI.

    The host uses full-frame Plate inference (origin0); explicit ROI origins are
    supported for mapping tests, never an extra vehicle/Plate inference pass.
    """
    _,tools=geometry()
    x1,y1,x2,y2,confidence=detection
    frame=(x1+origin[0],y1+origin[1],x2+origin[0],y2+origin[1],confidence)
    all_crops=tools.crop_detections(pixels,[frame],min_width=1,min_height=1)
    return all_crops[0] if all_crops else None


def quality(crop,confidence,shape,bbox):
    g,_=geometry();h,w=shape[:2];x1,y1,x2,y2=bbox
    return g.crop_quality_evidence(crop,confidence,border_contact=x1<=0 or y1<=0 or x2>=w or y2>=h)[0]
