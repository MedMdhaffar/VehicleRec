"""Bounded combined Engine ledger. Domain selection is never performed here."""
from dataclasses import dataclass,field,replace,asdict
from hashlib import sha256
from threading import Lock
from collections import deque
import time

from contracts.db1.reference import canonical_json_bytes
from ..db1_rows.core import require
from ..engine_plate import native_access,PlateExecutionAdapter,CropBinding,CropPlan,OperationPlan
from ..plate_crop_smoke import EngineCrop
from ..observation_evidence import VehicleIdentity
from ..plate_metadata import PlateCandidate
from ..plate_association import associate_plate
from ..redis_scalar.plate_codec import pack_transcript
from .engine import Engine
from .plate_local import crop_in_frame,quality


@dataclass(frozen=True)
class VideoPlateLimits:
    streaming: bool=False
    max_frames: int=4
    max_seconds: float=180
    max_lifetimes: int=16
    max_raw_per_frame: int=16
    max_raw_total: int=128
    max_candidates_per_life: int=16
    max_segments_per_life: int=2
    max_attempts_total: int=16
    max_handles: int=16
    max_crop_bytes: int=8*1024*1024
    max_crop_total: int=32*1024*1024
    max_api_calls: int=80
    max_scalar_bytes: int=8*1024*1024

    def __post_init__(self):
        for name,maximum in [('max_frames',8),('max_lifetimes',16),('max_raw_per_frame',16),('max_raw_total',128),
            ('max_candidates_per_life',16),('max_segments_per_life',2),('max_attempts_total',16),('max_handles',16),
            ('max_crop_bytes',8*1024*1024),('max_crop_total',32*1024*1024),('max_api_calls',80),('max_scalar_bytes',8*1024*1024)]:
            value=getattr(self,name)
            if self.streaming and name in {'max_frames','max_raw_total','max_api_calls'} and value is None:continue
            require(type(value) is int and 1<=value<=maximum,'INVALID_CAPACITY','bounded combined profile required')
        require((self.streaming and self.max_seconds is None) or 0<self.max_seconds<=300,'INVALID_WINDOW','bounded admission seconds required')


class NativeAudit:
    def __init__(self,limit=80):
        self.limit=limit;self.events=deque(maxlen=128) if limit is None else [];self.calls={};self.active=None;self.max_active=0;self.lock=Lock()

    def start(self,kind):
        with self.lock:
            require(self.active is None,'NATIVE_OVERLAP','native work overlapped')
            require(self.limit is None or sum(self.calls.values())<self.limit,'CAPACITY_EXCEEDED','native API budget exhausted before invocation')
            self.active=kind;self.max_active=1;self.calls[kind]=self.calls.get(kind,0)+1
            self.events.append(dict(kind=kind,phase='start',monotonic=time.monotonic()))

    def retire(self,kind):
        with self.lock:
            require(self.active==kind,'NATIVE_RETIREMENT_MISMATCH','wrong native retirement')
            self.events.append(dict(kind=kind,phase='retired',monotonic=time.monotonic()));self.active=None

    def run(self,kind,call):
        with native_access():
            self.start(kind)
            try:return call()
            finally:self.retire(kind)

    def executor(self,executor):
        original=executor.invoke;audit=self
        def invoke(request,context):
            class Context:
                entered=False
                def started(self,model):
                    audit.start(request.plan.kind);self.entered=True;context.started(model)
                def retired(self):
                    if self.entered:audit.retire(request.plan.kind);self.entered=False
                    context.retired()
            return original(request,Context())
        return replace(executor,invoke=invoke)


class GuardedVehicle:
    def __init__(self,target,audit):self.target=target;self.audit=audit
    def process(self,pixels):return self.audit.run('vehicle',lambda:self.target.process(pixels))
    def close(self):
        with native_access():self.target.close()


@dataclass
class PlateLife:
    life: object
    candidates: list=field(default_factory=list)
    best: dict=field(default_factory=dict) # segment -> (candidate index, private crop)
    adapter: object=None
    blocker: str|None=None


class PlateLedger:
    def __init__(self,detector,bindings,audit,*,limits=VideoPlateLimits(),quality_algorithm,quality_fn=quality):
        self.detector,self.bindings,self.audit=detector,bindings,audit
        self.limits,self.quality_algorithm,self.quality_fn=limits,quality_algorithm,quality_fn
        self.lives={};self.current={};self.next_segment=1;self.plate_epoch=0;self.last_frame=-1
        self.associations=deque(maxlen=128) if limits.streaming else [];self.raw_count=0;self.detections=0;self.suppressed=0;self.quality_calls=0;self.attempts=0
        self.evidence_not_admitted=0
        self.crop_replacements=0;self.failure=None

    @property
    def buffers(self):return [crop for v in self.lives.values() for _,crop in v.best.values() if not crop.released]

    def reset_plate_tracker(self):
        # Caller must actually reset/replace tracker; no guessed native reset API.
        self.current={};self.plate_epoch+=1

    def observe(self,packet,observation):
        require(packet.receipt is observation.receipt and packet.receipt.frame_index>self.last_frame,
                'FRAME_ORDER','same ordered decoded frame required')
        self.last_frame=packet.receipt.frame_index
        for owner in observation.vehicles:
            sid=owner.service_track_id;identity=VehicleIdentity.from_lifetime(owner.lifetime)
            require(sid not in self.lives or VehicleIdentity.from_lifetime(self.lives[sid].life)==identity,
                    'PARENT_MISMATCH','service lifetime changed')
            self.lives.setdefault(sid,PlateLife(owner.lifetime)).life=owner.lifetime
        result=self.audit.run('plate',lambda:self.detector.process(packet.pixels))
        self.raw_count+=len(result.raw);self.suppressed+=result.suppressed
        require(len(result.raw)<=self.limits.max_raw_per_frame and (self.limits.max_raw_total is None or self.raw_count<=self.limits.max_raw_total),
                'CAPACITY_EXCEEDED','raw Plate detection bound; no candidate truncated')
        boxes=[dict(track_id=v.service_track_id,bbox=list(v.bbox)) for v in observation.vehicles]
        active={v.service_track_id for v in observation.vehicles}
        require(len(self.lives)<=self.limits.max_lifetimes,'CAPACITY_EXCEEDED','Plate lifetime bound')
        # A trackerless detection has no honest raw tracker ID in this contract.
        # Keep the diagnostic; block affected closure rather than inventing ID0.
        for detection in result.untracked:
            association=associate_plate(list(detection[:4]),boxes)
            targets={s['track_id'] for s in association['scores']} or active
            for sid in targets:self.lives[sid].blocker='UNCONFIRMED_PLATE_TRACK'
            self.associations.append(dict(frame=packet.receipt.frame_index,raw_plate_id=None,raw_bbox=detection[:4],
                association=association,status='tracker_unconfirmed',projections=sorted(targets)))
        frame_crop_bytes=0
        proposals=[];staged=dict(self.current);next_segment=self.next_segment
        seen=set()
        for raw,detection in result.tracked:
            require(raw not in seen and raw>=0,'INVALID_TRACK','unique original Plate tracker row required');seen.add(raw)
            association=associate_plate(list(detection[:4]),boxes);sid=association['selected_track_id']
            # Same conservative diagnostic projection as legacy SharedPlatePipeline.
            targets={sid} if sid is not None else {s['track_id'] for s in association['scores']} if association['status']=='ambiguous' else active
            crop=crop_in_frame(packet.pixels,detection)
            entry=dict(frame=packet.receipt.frame_index,receipt=packet.receipt,raw_plate_id=raw,raw_bbox=tuple(detection[:4]),
                confidence=float(detection[4]),association=association,projections=sorted(targets),status='observed')
            if sid is None:staged.pop((self.plate_epoch,raw),None)
            if crop is None:
                for target in targets:self.lives[target].blocker='UNREPRESENTABLE_PLATE_GEOMETRY'
                entry['status']='rejected_no_geometry';self.associations.append(entry);continue
            if sid is not None:
                frame_crop_bytes+=crop.crop.nbytes
                require(frame_crop_bytes<=self.limits.max_crop_total,'CAPACITY_EXCEEDED','transient frame crop bytes exceeded before admission')
            pixels=crop.crop.tobytes();h,w=crop.crop.shape[:2]
            valid=w>=4 and h>=4
            q=self.quality_fn(crop.crop,float(detection[4]),packet.pixels.shape,crop.bbox) if valid else None
            if valid:self.quality_calls+=1
            for target in sorted(targets):
                v=self.lives[target];identity=VehicleIdentity.from_lifetime(v.life)
                if self.limits.streaming and (len(v.candidates)+sum(1 for s,*_ in proposals if s==target)>=self.limits.max_candidates_per_life
                        or len(v.best)>=self.limits.max_segments_per_life and (self.plate_epoch,raw) not in self.current):
                    self.evidence_not_admitted+=1
                    continue
                key=(self.plate_epoch,raw)
                previous=staged.get(key) if sid is not None else None
                if previous is None or previous[0]!=identity:
                    segment=next_segment;next_segment+=1
                    if sid is not None:staged[key]=(identity,segment,packet.receipt.frame_index)
                else:
                    segment=previous[1];staged[key]=(identity,segment,packet.receipt.frame_index)
                candidate=PlateCandidate(identity,f'frame-{packet.receipt.frame_index}-plate-{raw}-parent-{target}',segment,raw,
                    packet.receipt,association['status'],False,'crop-sha256:'+sha256(pixels).hexdigest(),sha256(pixels).hexdigest(),
                    tuple(crop.bbox),(w,h),q,float(detection[4]),self.quality_algorithm)
                eligible=valid and sid==target
                proposals.append((target,candidate,pixels if eligible else None,eligible))
            entry.update(clipped_bbox=crop.bbox,crop_size=(w,h),crop_sha256=sha256(pixels).hexdigest(),quality=q,
                status='eligible' if valid and sid is not None else 'rejected_size' if not valid else 'diagnostic_only')
            self.associations.append(entry)
        # Preflight the entire frame's candidate/crop update. No partial admission.
        proposed={sid:list(v.candidates) for sid,v in self.lives.items()}
        best={sid:dict(v.best) for sid,v in self.lives.items()}
        for sid,candidate,pixels,eligible in proposals:
            proposed[sid].append(candidate)
            require(len(proposed[sid])<=self.limits.max_candidates_per_life,'CAPACITY_EXCEEDED','Plate candidates full; no truncation')
            if eligible:
                require(len(pixels)<=self.limits.max_crop_bytes,'CAPACITY_EXCEEDED','private crop exceeds bound')
                old=best[sid].get(candidate.segment_id)
                if old is None or candidate.quality>proposed[sid][old[0]].quality:
                    best[sid][candidate.segment_id]=(len(proposed[sid])-1,(candidate,pixels))
        for sid,values in best.items():
            require(len(values)<=self.limits.max_segments_per_life,'CAPACITY_EXCEEDED','Plate segments/attempts full')
        selected=[crop for values in best.values() for _,crop in values.values()]
        size=lambda crop:len(crop[1]) if isinstance(crop,tuple) else crop.binding.size_bytes if not crop.released else 0
        require(sum(size(c)>0 for c in selected)<=self.limits.max_handles and sum(size(c) for c in selected)<=self.limits.max_crop_total,
                'CAPACITY_EXCEEDED','private handle/byte budget full')
        # No private handle is admitted until every frame-level capacity check passes.
        for sid,values in best.items():
            for segment,(index,crop) in tuple(values.items()):
                if isinstance(crop,tuple):
                    candidate,pixels=crop
                    values[segment]=(index,EngineCrop(CropBinding(candidate,'private-'+candidate.candidate_id,len(pixels)),pixels))
        retained={id(crop) for values in best.values() for _,crop in values.values()}
        for crop in self.buffers:
            if id(crop) not in retained:
                crop.release();self.crop_replacements+=1
        for sid in self.lives:self.lives[sid].candidates=proposed[sid];self.lives[sid].best=best[sid]
        self.current=staged;self.next_segment=next_segment;self.detections+=len(result.tracked)
        # Missing/reassigned raw tracks start a new segment on later sightings.
        for key in tuple(self.current):
            if key[0]==self.plate_epoch and key[1] not in seen and self.last_frame-self.current[key][2]>=5:del self.current[key]

    def prepare(self,closed,*,progressive=False):
        sid=closed.owner.service_track_id;v=self.lives.setdefault(sid,PlateLife(closed.owner.lifetime))
        v.life=closed.owner.lifetime
        require(v.blocker is None and self.failure is None and closed.blocker is None,'BLOCKED_ENGINE_EVIDENCE',
                'incomplete Plate/parent evidence cannot finalize')
        if v.adapter is not None:
            require(v.adapter.progressive==progressive,'WRONG_VERSION','lifetime cannot change execution protocol')
            return v.adapter
        selected=sorted(v.best.values(),key=lambda value:(v.candidates[value[0]].receipt.frame_index,v.candidates[value[0]].segment_id))
        count=len(selected)
        require(self.attempts+count<=self.limits.max_attempts_total,'CAPACITY_EXCEEDED','global crop attempt budget')
        # Reserve all fixed operations, including geometry-ineligible future regions.
        planned=sum(len(x.adapter._ops) for x in self.lives.values() if x.adapter is not None)
        require(self.limits.streaming or 2*self.limits.max_frames+planned+count*4<=self.limits.max_api_calls,'CAPACITY_EXCEEDED','fixed plan API budget')
        plans=[];resources=[]
        for ordinal,(index,resource) in enumerate(selected,1):
            c=replace(v.candidates[index],crop_accepted=True);v.candidates[index]=c
            resource._binding=replace(resource.binding,candidate=c) # before immutable adapter admission only
            prefix=f'candidate-{index}-';first,last=('left','right') if ordinal%2 else ('right','left')
            operations=() if progressive else (OperationPlan(prefix+'whole',1,(),'whole_ocr','whole'),OperationPlan(prefix+'marker',2,(),'marker','marker'),
                OperationPlan(prefix+first,3,(prefix+'whole',prefix+'marker'),first+'_ocr',first),
                OperationPlan(prefix+last,4,(prefix+'whole',prefix+'marker'),last+'_ocr',last))
            plans.append(CropPlan(f'parent-{sid}-crop-{ordinal}',ordinal,c.candidate_id,operations,'fixed-local-smoke-plan-v1'))
            resources.append(resource)
        executors=tuple(self.audit.executor(self.bindings['detect' if kind=='marker' else 'ocr'].executor(name,kind))
            for name,kind in [('whole','whole_ocr'),('marker','marker'),('left','left_ocr'),('right','right_ocr')])
        adapter=PlateExecutionAdapter(v.life,executors,expected_attempt_ids=tuple(p.attempt_id for p in plans),progressive=progressive)
        # Save ownership before admission so any failure retains cleanup handles.
        v.adapter=adapter
        for c in v.candidates:adapter.observe_candidate(c)
        if not progressive:
            for plan,resource in zip(plans,resources):adapter.admit(resource.binding,resource,plan)
        adapter.mark_detection_complete()
        if not progressive:adapter.seal_admission()
        self.attempts+=count
        return adapter

    def complete(self,closed):
        adapter=self.prepare(closed)
        if not adapter.drain(5):
            if adapter._active is not None and adapter._active.returned.is_set():
                require(adapter._active.context.retirement.is_set(),'NATIVE_RETIREMENT_UNKNOWN','returned operation has no retirement acknowledgment')
            return None # waiting, never fabricated unreadable
        require(not adapter.blockers,'BLOCKED_ENGINE_EVIDENCE','conflicting original execution evidence')
        return adapter.snapshot()


class CombinedEngine:
    agent_enabled=False
    def __init__(self,context,clock,vehicle,plate,bindings,*,algorithm,quality_algorithm,plate_quality_algorithm,
                 estimate,quality_fn,limits=VideoPlateLimits(),plate_quality_fn=quality,manufacturer=None):
        self.audit=NativeAudit(limits.max_api_calls);self.limits=limits
        if manufacturer is not None:
            manufacturer.execute=lambda call:self.audit.run('manufacturer',call)
        self.colour=Engine(context,clock,GuardedVehicle(vehicle,self.audit),algorithm=algorithm,
            quality_algorithm=quality_algorithm,estimate=estimate,quality=quality_fn,max_vehicles=limits.max_lifetimes,
            manufacturer=manufacturer,streaming=limits.streaming)
        self.plate=PlateLedger(plate,bindings,self.audit,limits=limits,quality_algorithm=plate_quality_algorithm,quality_fn=plate_quality_fn)
        self.pending=self.colour.pending;self.failure=None;self.closed=False
    def process(self,packet):
        try:
            require(packet.pixels.nbytes<=32*1024*1024,'CAPACITY_EXCEEDED','decoded frame allocation exceeds bounded profile')
            self.colour.process(packet);self.plate.observe(packet,self.colour.pipeline.observation_frame)
        except BaseException as error:
            self.failure=getattr(error,'code','ENGINE_PROCESSING_FAILURE');self.plate.failure=self.failure;raise
    def finish(self,cause,*,trigger=None):self.colour.finish(cause,trigger=trigger)
    def acknowledge(self,sid):self.colour.acknowledge(sid)
    def close(self):
        # No automatic crop discard: failed publication stays explicitly retained.
        errors=[]
        for v in self.plate.lives.values():
            if v.adapter is not None:
                v.adapter.stop()
                if v.adapter.confirmed:
                    try:v.adapter.cleanup()
                    except Exception as e:errors.append(type(e).__name__)
        for binding in self.plate.bindings.values():
            try:
                with native_access():binding.close()
            except Exception as e:errors.append(getattr(e,'code',type(e).__name__))
        try:
            with native_access():self.plate.detector.close()
        except Exception as e:errors.append(getattr(e,'code',type(e).__name__))
        try:self.colour.close()
        except Exception as e:errors.append(getattr(e,'code',type(e).__name__))
        self.cleanup_errors=errors;self.closed=not errors
