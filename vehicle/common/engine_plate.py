"""Engine-private, injected Plate execution. No pixels/handles enter transcripts.

Plans are explicitly supplied stand-ins for future Plate requests. This adapter
never normalizes/parses text, decides followups, ranks crops or selects a read.
"""
from concurrent.futures import ThreadPoolExecutor, Future
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from threading import Event, Lock
import time
from hashlib import sha256
from typing import Callable, Protocol

from contracts.db1.reference import canonical_json_bytes, strict_loads
from .db1_rows.core import (require, frozen_tree, score, receipt_in_lifetime,
    verify_shared_receipts, integer, validate_lifetime)
from .observation_evidence import VehicleIdentity
from .plate_metadata import (PlateCandidate, PlateCropAttempt, PlateOperation,
    PlateTranscript, PlateMetadata, PlateModel, MarkerScalar, _box, _ids, _name)
from .redis_scalar.codec import scalar_float, pack_life
from .redis_scalar.plate_codec import pack_transcript
from .redis_scalar.journal import PRODUCE
from .engine_plate_artifacts import LocalExecutor

MiB=1024*1024
BASE_RESERVE=131072
CROP_RESERVE=16384
ATTEMPT_RESERVE=32768
OPERATION_RESERVE=65536  # worst-case escaped text/IDs/dependencies + scalar/model fields

# One process-wide adapter lane; an unretired invocation keeps it charged.
_NATIVE_GATE=Lock()
_NATIVE_STATE=Lock()
_NATIVE_READY=Event()
_NATIVE_READY.set()


@contextmanager
def native_access():
    """The same lane for opt-in synchronous CPU detector/init/close calls.

    Refuse busy/unretired work; no timeout is evidence of cancellation. The
    bounded host uses only synchronous native APIs under this entry point.
    """
    with _NATIVE_STATE:
        require(_NATIVE_GATE.acquire(blocking=False),'NATIVE_LANE_BUSY','native execution has not retired')
        _NATIVE_READY.clear()
    try:yield
    finally:
        with _NATIVE_STATE:
            _NATIVE_GATE.release();_NATIVE_READY.set()


@dataclass(frozen=True, slots=True)
class CropBinding:
    candidate: PlateCandidate
    handle_id: str  # private Engine identity, not a registration or wire field
    size_bytes: int


class PrivateCrop(Protocol):
    @property
    def binding(self) -> CropBinding: ...
    @property
    def released(self) -> bool: ...
    def read(self) -> object: ...
    def release(self) -> None: ...  # idempotent; released acknowledges actual release


@dataclass(frozen=True, slots=True)
class OperationPlan:
    operation_id: str
    sequence: int
    dependencies: tuple[str, ...]
    kind: str
    executor_id: str


@dataclass(frozen=True, slots=True)
class CropPlan:
    attempt_id: str
    submission_ordinal: int
    candidate_id: str
    operations: tuple[OperationPlan, ...]
    source: str = 'injected-work-plan-v1'


@dataclass(frozen=True, slots=True)
class OCRScalar:
    raw_text: str
    confidence: float


@dataclass(frozen=True, slots=True)
class DependencyResult:
    operation_id: str
    kind: str
    model: PlateModel
    scalar: OCRScalar | MarkerScalar


@dataclass(frozen=True, slots=True)
class NativeRequest:
    """Private execution view; never serialized or passed to metadata workers."""
    pixels: object
    candidate: PlateCandidate
    plan: OperationPlan
    dependencies: tuple[DependencyResult, ...]


class KnownNotStarted(Exception):
    """Executor/scheduler attests no execution and no retained pixel use."""


class OutcomeUnknown(Exception):
    """Start/result cannot be established; retirement needs its own acknowledgment."""


def decode_ocr(raw):
    v=strict_loads(raw)
    require(type(v) is dict and set(v)=={'raw_text','confidence'},'MALFORMED_OCR','raw OCR fields required')
    return OCRScalar(v['raw_text'],scalar_float(v['confidence']))


def decode_marker(raw):
    v=strict_loads(raw)
    require(type(v) is dict and set(v)=={'detected','confidence','bbox','center_x'},
            'MALFORMED_MARKER','native marker scalar fields required')
    return MarkerScalar(v['detected'],scalar_float(v['confidence']),
        tuple(v['bbox']) if v['bbox'] is not None else None,
        scalar_float(v['center_x']) if v['center_x'] is not None else None)


@dataclass(frozen=True, slots=True)
class InjectedExecutor:
    executor_id: str
    model: PlateModel
    invoke: Callable  # (NativeRequest, ExecutionContext) -> bounded raw bytes
    decode: Callable  # native response decoding only, no Plate semantic policy

    def __post_init__(self):
        _name(self.executor_id)
        require(isinstance(self.model,PlateModel) and self.model.task in {'ocr','detect'}
                and self.model.model_name.startswith(('synthetic-','injected-')),
                'INVALID_EXECUTOR','this slice requires explicitly synthetic artifact identities')
        _name(self.model.model_name);integer(self.model.version,2147483647,'model version')
        require(callable(self.invoke) and callable(self.decode),'INVALID_EXECUTOR','injected call/decoder required')


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    operation_id: str
    outcome: str
    model: PlateModel | None = None
    scalar: OCRScalar | MarkerScalar | None = None
    error: str | None = None


class ExecutionContext:
    """Actual-start and no-further-pixel-use acknowledgments are independent."""
    def __init__(self,executor):
        self._executor=executor
        self._lock=Lock()
        self.model=None
        self.starts=0
        self.conflict=None
        self.retirement=Event()
        self._retirement_hook=lambda:None

    def started(self,actual_model):
        with self._lock:
            self.starts+=1
            if isinstance(actual_model,PlateModel):self.model=actual_model
            if self.starts!=1 or actual_model!=self._executor.model:
                self.conflict='EXECUTOR_MODEL_CONFLICT'
                require(False,self.conflict,'actual execution differs from admitted executor identity')

    def retired(self):
        """Call only when no native/async executor will access the crop again."""
        self.retirement.set()
        self._retirement_hook()

    def observed(self):
        with self._lock:return self.model,self.conflict


@dataclass
class _Run:
    plan: OperationPlan
    crop_id: str
    context: ExecutionContext
    returned: Event = field(default_factory=Event)
    result: ExecutionRecord | None = None
    future: object = None
    cancel_before_run: bool = False
    entered: bool = False
    gate: Lock = field(default_factory=Lock)
    lane_guard: Lock = field(default_factory=Lock)
    lane_owned: bool = True


@dataclass(frozen=True, slots=True)
class Publication:
    batch_id: str
    event_id: str
    stream_id: str
    payload: bytes


class PlateExecutionAdapter:
    """One bounded lifetime, one native slot, immutable completed revision-0 output.

    Ownership transfers only after admit succeeds. All public methods except
    executor callbacks are called by one Engine owner thread.
    """
    def __init__(self,lifetime,executors,*,expected_attempt_ids,scheduler=None,max_handles=8,max_crop_bytes=8*MiB,
                 max_total_crop_bytes=16*MiB,max_attempts=16,max_operations=32,
                 max_evidence_bytes=4*MiB,max_publication_bytes=6*MiB,max_candidates=16,progressive=False):
        from .vehicle_observation import VehicleLifetime
        require(isinstance(lifetime,VehicleLifetime),'INVALID_PARENT','typed original lifetime required')
        frozen_tree(lifetime)
        if lifetime.closure is not None:
            validate_lifetime(lifetime)
        require(not lifetime.quarantined,'CLOCK_QUARANTINE','quarantined execution is not admitted')
        require(not lifetime.context.database_keys_resolved and lifetime.context.environment=='development',
                'RESOLVED_CONTEXT','explicit unresolved Engine run context required')
        for value,maximum in ((max_handles,8),(max_crop_bytes,8*MiB),(max_total_crop_bytes,16*MiB),
                              (max_attempts,16),(max_operations,32),(max_publication_bytes,6*MiB),(max_candidates,16)):
            require(type(value) is int and 1<=value<=maximum,'INVALID_CAPACITY','bounded Engine profile required')
        require(type(max_evidence_bytes) is int and BASE_RESERVE<=max_evidence_bytes<=4*MiB,
                'INVALID_CAPACITY','bounded retained scalar reservation required')
        def text_size(v):
            if isinstance(v,str):return len(v.encode())
            if hasattr(v,'__dataclass_fields__'):return sum(text_size(getattr(v,k)) for k in v.__dataclass_fields__)
            return 0
        require(text_size(lifetime.context)<=8192,'CAPACITY_EXCEEDED','context exceeds Engine reservation')
        require(type(executors) is tuple and len(executors)<=16 and all(isinstance(e,(InjectedExecutor,LocalExecutor)) for e in executors)
                and len({e.executor_id for e in executors})==len(executors),
                'INVALID_EXECUTOR','bounded immutable unique executor bindings required')
        _ids(expected_attempt_ids)
        require(len(expected_attempt_ids)<=max_attempts,'CAPACITY_EXCEEDED','explicit crop plan exceeds Engine attempt profile')
        require(type(progressive) is bool,'INVALID_PLAN','explicit protocol mode required')
        self.progressive=progressive
        self._expected=expected_attempt_ids
        self._life=lifetime
        self._identity=VehicleIdentity.from_lifetime(lifetime)
        integer(self._identity.service_track_id,2**63-1,'service track ID')
        integer(self._identity.raw_track_id,2**63-1,'raw track ID',0)
        integer(self._identity.tracker_epoch,2**63-2,'tracker epoch',0)
        integer(self._identity.lifetime_sequence,2**63-1,'lifetime sequence')
        self._executors={e.executor_id:e for e in executors}
        self._pool=scheduler if scheduler is not None else ThreadPoolExecutor(max_workers=1,thread_name_prefix='engine-plate')
        self._owns_pool=scheduler is None
        self._pool_closed=False
        self._candidates={};self._max_candidates=max_candidates
        self._bindings={};self._resources={};self._plans={};self._ops={};self._records={};self._late_records={}
        self._released=set();self._resource_tokens={}
        self._active=None
        self._lane_busy=False
        self._stopped=False;self._draining=False
        self._detection_complete=False;self._admission_complete=False
        self._uncertain=False;self._forced_unknown=set();self._blockers=set()
        self._sealed=None;self._publication=None;self._confirmed=False
        self._limits=(max_handles,max_crop_bytes,max_total_crop_bytes,max_attempts,max_operations,max_evidence_bytes)
        self._max_publication_bytes=max_publication_bytes

    @property
    def lifetime(self):return self._life
    @property
    def confirmed(self):return self._confirmed
    @property
    def blockers(self):return tuple(sorted(self._blockers))
    @property
    def reserved_crop_bytes(self):return sum(b.size_bytes for k,b in self._bindings.items() if k not in self._released)
    @property
    def released_handles(self):return tuple(sorted(self._bindings[k].handle_id for k in self._released))

    def _mutable(self):require(self._sealed is None,'PAYLOAD_CONFLICT','prepared transcript is immutable')

    def close_lifetime(self,lifetime):
        self._mutable();validate_lifetime(lifetime)
        require(VehicleIdentity.from_lifetime(lifetime)==self._identity
                and lifetime.first_receipt==self._life.first_receipt
                and lifetime.last_receipt.frame_index>=self._life.last_receipt.frame_index
                and lifetime.last_receipt.monotonic>=self._life.last_receipt.monotonic
                and lifetime.frames_observed>=self._life.frames_observed,
                'PARENT_MISMATCH','original lifetime closure changed')
        require(self._life.closure is None or self._life==lifetime,'PAYLOAD_CONFLICT','accepted closure changed')
        verify_shared_receipts((self._life,lifetime,tuple(self._candidates.values())),{})
        for c in self._candidates.values():receipt_in_lifetime(c.receipt,lifetime)
        self._life=lifetime

    def mark_detection_complete(self):self._mutable();self._detection_complete=True
    def seal_admission(self):
        self._mutable()
        require(set(self._plans)==set(self._expected),'ADMISSION_INCOMPLETE','declared crop work remains unadmitted')
        self._admission_complete=True

    def _validate_candidate(self,c):
        require(isinstance(c,PlateCandidate) and c.identity==self._identity,
                'PARENT_MISMATCH','original candidate belongs to another lifetime')
        frozen_tree(c);_name(c.candidate_id);_name(c.quality_algorithm)
        require(c.association in {'associated','ambiguous','unassociated'} and type(c.crop_accepted) is bool,
                'INVALID_ASSOCIATION','explicit Engine association/admission required')
        require((c.crop_ref is None)==(c.crop_sha256 is None),'INVALID_CROP_REFERENCE','paired opaque reference/fingerprint required')
        if c.crop_ref is not None:
            _name(c.crop_ref)
            require(type(c.crop_sha256) is str and len(c.crop_sha256)==64
                    and all(x in '0123456789abcdef' for x in c.crop_sha256),'INVALID_CROP_REFERENCE','original fingerprint required')
        require(not c.crop_accepted or (c.association=='associated' and c.crop_ref is not None),
                'INVALID_ASSOCIATION','accepted crop requires original associated evidence')
        _box(c.bbox)
        require(type(c.crop_size) is tuple and len(c.crop_size)==2
                and all(type(n) is int and 0<n<=65536 for n in c.crop_size),'INVALID_GEOMETRY','bounded crop dimensions required')
        integer(c.segment_id,2**63-1,'segment');integer(c.raw_plate_id,2**63-1,'raw plate ID',0)
        require(all(old.segment_id!=c.segment_id or old.raw_plate_id==c.raw_plate_id for old in self._candidates.values()),
                'CROP_BINDING_CONFLICT','segment changes original plate identity')
        receipt_in_lifetime(c.receipt,self._life);score(c.quality,quality=True);score(c.detector_confidence)
        verify_shared_receipts((self._life,tuple(self._candidates.values()),c),{})
        require(c.candidate_id not in self._candidates or self._candidates[c.candidate_id]==c,
                'PAYLOAD_CONFLICT','accepted original candidate changed')

    def observe_candidate(self,candidate):
        """Record original scalar observations even when no crop work is eligible."""
        self._validate_candidate(candidate)
        if candidate.candidate_id in self._candidates:return
        self._mutable()
        require(not self._stopped and not self._detection_complete,'DETECTION_CLOSED','no new observations after stop/completeness')
        reserve=BASE_RESERVE+(len(self._candidates)+1)*CROP_RESERVE+len(self._plans)*ATTEMPT_RESERVE+len(self._ops)*OPERATION_RESERVE
        require(len(self._candidates)<self._max_candidates and reserve<=self._limits[-1],
                'CAPACITY_EXCEEDED','candidate/evidence capacity; nothing observed')
        self._candidates[candidate.candidate_id]=candidate

    def admit(self,binding,resource,plan):
        require(isinstance(binding,CropBinding) and isinstance(plan,CropPlan),
                'INVALID_PLAN','typed binding/plan required')
        frozen_tree((binding,plan))
        require(plan.attempt_id in self._expected,'UNEXPECTED_WORK','crop work is not in the explicit expected plan')
        old=self._plans.get(plan.attempt_id)
        if old is not None:
            require(self._bindings.get(binding.candidate.candidate_id)==binding
                    and self._resource_tokens.get(binding.candidate.candidate_id) is resource,
                    'PAYLOAD_CONFLICT','accepted crop binding changed')
            if old==plan:return self.records()
            require(self.progressive and plan.source==old.source=='plate-request-v2'
                    and (plan.attempt_id,plan.submission_ordinal,plan.candidate_id)==
                        (old.attempt_id,old.submission_ordinal,old.candidate_id)
                    and plan.operations[:len(old.operations)]==old.operations
                    and len(plan.operations)==len(old.operations)+1,
                    'PAYLOAD_CONFLICT','only an explicit v2 single-operation extension is allowed')
        self._mutable()
        require(not self._stopped and not self._admission_complete,'ADMISSION_CLOSED','no new admission after stop/seal')
        c=binding.candidate;self._validate_candidate(c)
        require(c.candidate_id==plan.candidate_id and c.association=='associated' and c.crop_accepted,
                'PARENT_MISMATCH','original associated candidate/crop required for execution')
        _name(binding.handle_id)
        new_candidate=c.candidate_id not in self._candidates
        require(not new_candidate or not self._detection_complete,'DETECTION_CLOSED','new candidate after completeness')
        require(resource.binding==binding and resource.released is False,'CROP_BINDING_CONFLICT','private handle binding/release differs')
        existing=self._bindings.get(c.candidate_id)
        require(existing is None or (existing==binding and self._resource_tokens[c.candidate_id] is resource),
                'PAYLOAD_CONFLICT','candidate/private crop binding changed')
        require(existing is not None or binding.handle_id not in {b.handle_id for b in self._bindings.values()},
                'DUPLICATE_ID','private handle already belongs to another candidate')
        _name(plan.attempt_id);integer(plan.submission_ordinal,2**63-1,'crop ordinal')
        require(plan.source in ({'plate-request-v2'} if self.progressive else {'injected-work-plan-v1','fixed-local-smoke-plan-v1'}),'UNSUPPORTED_PLAN','explicit injected stand-in plan required')
        require(type(plan.operations) is tuple and 1<=len(plan.operations)<=16
                and all(isinstance(o,OperationPlan) for o in plan.operations),'INVALID_PLAN','bounded typed operations required')
        require(all(p.attempt_id==plan.attempt_id or p.submission_ordinal!=plan.submission_ordinal for p in self._plans.values()),'DUPLICATE_ID','duplicate crop ordinal')
        sequences=set();available={}
        for o in plan.operations:integer(o.sequence,2147483647,'operation sequence')
        for o in sorted(plan.operations,key=lambda o:o.sequence):
            _name(o.operation_id);_ids(o.dependencies)
            require((o.operation_id not in self._ops or (old is not None and o in old.operations)) and o.operation_id not in available and o.sequence not in sequences,
                    'DUPLICATE_ID','unique operation identity/sequence required')
            require(o.kind in {'whole_ocr','marker','left_ocr','right_ocr'} and o.executor_id in self._executors,
                    'UNSUPPORTED_PLAN','explicit local executor/stage required')
            executor=self._executors[o.executor_id]
            require(not isinstance(executor,LocalExecutor) or executor.kind==o.kind,
                    'EXECUTOR_STAGE_CONFLICT','local executor kind differs from operation')
            require(executor.model.task==('detect' if o.kind=='marker' else 'ocr'),
                    'EXECUTOR_MODEL_CONFLICT','executor task differs from operation')
            require(all(d in available for d in o.dependencies),'INVALID_DEPENDENCY','earlier same-crop dependency required')
            if o.kind in {'left_ocr','right_ocr'}:
                require(sum(available[d].kind=='whole_ocr' for d in o.dependencies)==1
                        and sum(available[d].kind=='marker' for d in o.dependencies)==1,
                        'INVALID_DEPENDENCY','region requires exact whole/marker generations')
            sequences.add(o.sequence);available[o.operation_id]=o
        added=tuple(o for o in plan.operations if old is None or o not in old.operations)
        handles,per_crop,total,attempts,operations,evidence=self._limits
        new_crop=existing is None
        require(type(binding.size_bytes) is int and binding.size_bytes==c.crop_size[0]*c.crop_size[1]*3 and 1<=binding.size_bytes<=per_crop
                and len(self._bindings)+new_crop<=handles and self.reserved_crop_bytes+(binding.size_bytes if new_crop else 0)<=total
                and len(self._candidates)+new_candidate<=self._max_candidates and len(self._plans)+(old is None)<=attempts and len(self._ops)+len(added)<=operations,
                'CAPACITY_EXCEEDED','Engine crop/operation capacity reached; nothing admitted')
        reserved=BASE_RESERVE+(len(self._candidates)+new_candidate)*CROP_RESERVE+(len(self._plans)+(old is None))*ATTEMPT_RESERVE+(len(self._ops)+len(added))*OPERATION_RESERVE
        require(reserved<=evidence,'CAPACITY_EXCEEDED','retained evidence reservation exhausted; nothing admitted')
        # Ownership/ledger admission is all-or-nothing and precedes invocation.
        self._candidates[c.candidate_id]=c
        self._bindings[c.candidate_id]=binding;self._resources[c.candidate_id]=resource
        self._resource_tokens[c.candidate_id]=resource;self._plans[plan.attempt_id]=plan
        for o in added:
            self._ops[o.operation_id]=(plan.attempt_id,c.candidate_id,o)
            self._records[o.operation_id]=ExecutionRecord(o.operation_id,'pending')
        return self.records()

    def late_records(self):
        return tuple(self._late_records[k] for k in sorted(self._late_records))

    def records(self):
        self.poll()
        values=dict(self._records)
        if self._active and not self._active.returned.is_set():
            o=self._active.plan
            values[o.operation_id]=ExecutionRecord(o.operation_id,'unknown',self._active.context.observed()[0],error='EXECUTION_IN_FLIGHT')
        return tuple(values[k] for k in sorted(values))

    @staticmethod
    def _retire_lane(run):
        with run.lane_guard:
            if run.lane_owned and run.returned.is_set() and run.context.retirement.is_set():
                run.lane_owned=False
                with _NATIVE_STATE:
                    _NATIVE_GATE.release();_NATIVE_READY.set()

    def _perform(self,run):
        o=run.plan;context=run.context;executor=self._executors[o.executor_id]
        result=None
        try:
            with run.gate:
                cancelled=run.cancel_before_run
                if not cancelled:run.entered=True
            if cancelled:
                context.retired();result=ExecutionRecord(o.operation_id,'not_started',error='SUBMISSION_NOT_ACCEPTED');return
            resource=self._resources[run.crop_id]
            binding=self._bindings[run.crop_id]
            try:
                require(resource.binding==binding,'CROP_BINDING_CONFLICT','private handle binding changed after admission')
                pixels=resource.read()
                require(resource.binding==binding,'CROP_BINDING_CONFLICT','private handle binding changed during read')
                size=getattr(pixels,'nbytes',len(pixels) if isinstance(pixels,(bytes,bytearray,memoryview)) else None)
                require(type(size) is int and size==binding.size_bytes,'CROP_BINDING_CONFLICT','private allocation size changed')
                view=memoryview(pixels)
                require(view.contiguous and view.itemsize==1 and view.format in {'B','b','c'} and sha256(view).hexdigest()==binding.candidate.crop_sha256,
                        'CROP_BINDING_CONFLICT','private allocation fingerprint changed')
                pixels=view.toreadonly()
            except Exception as error:
                context.retired();result=ExecutionRecord(o.operation_id,'not_started',error='CROP_BINDING_CONFLICT' if getattr(error,'code',None)=='CROP_BINDING_CONFLICT' else 'CROP_ACCESS_FAILED');return
            dependencies=tuple(DependencyResult(d,self._ops[d][2].kind,self._records[d].model,self._records[d].scalar) for d in o.dependencies)
            request=NativeRequest(pixels,binding.candidate,o,dependencies)
            try:raw=executor.invoke(request,context)
            except KnownNotStarted as error:
                model,conflict=context.observed()
                if model is None:context.retired()
                result=ExecutionRecord(o.operation_id,'unknown' if model else 'not_started',model,error='INCONSISTENT_START' if model else getattr(error,'code','EXECUTOR_NOT_STARTED'));return
            except OutcomeUnknown:
                model,_=context.observed();result=ExecutionRecord(o.operation_id,'unknown',model,error='EXECUTION_UNKNOWN');return
            except Exception:
                model,conflict=context.observed()
                result=ExecutionRecord(o.operation_id,'unknown' if conflict or model is None else 'failed',model,error=conflict or 'INVOCATION_FAILED');return
            model,conflict=context.observed()
            if model is None or conflict:
                result=ExecutionRecord(o.operation_id,'unknown',model,error=conflict or 'START_UNCONFIRMED');return
            # Invocation and decoding are distinct: malformed output cannot erase
            # an observed model invocation or become an invented scalar result.
            try:
                require(type(raw) is bytes and len(raw)<=65536,'RAW_OUTPUT_LIMIT','bounded native response required')
                scalar=executor.decode(raw)
                if o.kind=='marker':
                    require(isinstance(scalar,MarkerScalar) and type(scalar.detected) is bool,'MALFORMED_MARKER','marker scalar required')
                    score(scalar.confidence,nullable=False)
                    require(scalar.detected==(scalar.bbox is not None),'MALFORMED_MARKER','marker detection/geometry differ')
                    if scalar.bbox is not None:
                        _box(scalar.bbox,*request.candidate.crop_size);score(scalar.center_x,quality=True,nullable=False)
                        require(scalar.bbox[0]<=scalar.center_x<=scalar.bbox[2],'MALFORMED_MARKER','marker center outside original crop')
                    else:require(scalar.center_x is None,'MALFORMED_MARKER','absent marker has center')
                else:
                    require(isinstance(scalar,OCRScalar) and type(scalar.raw_text) is str
                            and len(scalar.raw_text.encode())<=4096,'MALFORMED_OCR','bounded raw scalar text required')
                    score(scalar.confidence,nullable=False)
                frozen_tree(scalar)
                result=ExecutionRecord(o.operation_id,'succeeded',model,scalar)
            except Exception:result=ExecutionRecord(o.operation_id,'failed',model,error='OUTPUT_DECODE_FAILED')
        finally:
            if result is None:
                model,_=context.observed();result=ExecutionRecord(o.operation_id,'unknown',model,error='WORKER_OUTCOME_UNKNOWN')
            run.result=result;run.returned.set()  # retirement acknowledgment is NOT inferred here
            self._retire_lane(run)

    def poll(self):
        run=self._active
        if run is None:return False
        if run.future is not None and run.future.cancelled() and not run.returned.is_set():
            with run.gate:
                if run.entered:
                    self._blockers.add('SUBMISSION_CLAIM_CONFLICT')
                else:
                    run.cancel_before_run=True;run.context.retired()
                    run.result=ExecutionRecord(run.plan.operation_id,'not_started',error='SCHEDULER_CANCELLED')
                    run.returned.set()
        self._retire_lane(run)
        _,conflict=run.context.observed()
        if conflict:self._blockers.add(conflict)
        if run.returned.is_set():
            if run.result.error in {'CROP_BINDING_CONFLICT','ARTIFACT_BINDING_CONFLICT','EXECUTOR_STAGE_CONFLICT'}:self._blockers.add(run.result.error)
            if self._sealed is None:self._records[run.plan.operation_id]=run.result
            else:self._late_records[run.plan.operation_id]=run.result
            if run.context.retirement.is_set():self._active=None
            return True
        return False

    def step(self):
        self._lane_busy=False
        self.poll()
        if self._sealed is not None or self._active is not None or (self._stopped and not self._draining):return False
        pending=sorted((v for k,v in self._ops.items() if self._records[k].outcome=='pending'),
            key=lambda v:(self._plans[v[0]].submission_ordinal,v[2].sequence))
        if not pending:return False
        _,crop_id,o=pending[0]
        dependencies=[self._records[d] for d in o.dependencies]
        if any(r.outcome in {'pending','unknown'} for r in dependencies):return False
        if any(r.outcome!='succeeded' for r in dependencies):
            self._records[o.operation_id]=ExecutionRecord(o.operation_id,'not_started',error='DEPENDENCY_FAILED');return True
        with _NATIVE_STATE:
            if not _NATIVE_GATE.acquire(blocking=False):
                self._lane_busy=True;return False
            _NATIVE_READY.clear()
        run=_Run(o,crop_id,ExecutionContext(self._executors[o.executor_id]))
        run.context._retirement_hook=lambda:self._retire_lane(run)
        self._active=run  # charged/pinned before submit, including ambiguous submission errors
        try:
            future=self._pool.submit(self._perform,run)
            if isinstance(future,Future):run.future=future
            else:self._blockers.add('INVALID_SCHEDULER_RECEIPT')
        except KnownNotStarted:
            with run.gate:
                if run.entered:
                    self._blockers.add('SUBMISSION_CLAIM_CONFLICT')
                else:
                    run.cancel_before_run=True;run.context.retired()
                    run.result=ExecutionRecord(o.operation_id,'not_started',error='SUBMISSION_NOT_ACCEPTED');run.returned.set()
        except Exception:pass  # submitted/start unknown; retain token/resource until actual retirement
        self._retire_lane(run)
        return True

    def drain(self,timeout=1.0):
        require(isinstance(timeout,(int,float)) and not isinstance(timeout,bool) and 0<=timeout<=5,'INVALID_TIMEOUT','bounded drain deadline required')
        end=time.monotonic()+timeout
        while True:
            self.poll()
            pending=any(r.outcome=='pending' for r in self._records.values())
            if self._active is None and not pending:return True
            remaining=end-time.monotonic()
            if remaining<=0:return False
            if self._active is None:
                if not self.step():
                    if self._lane_busy:_NATIVE_READY.wait(min(remaining,.01))
                    else:return False
            else:
                event=self._active.returned if not self._active.returned.is_set() else self._active.context.retirement
                event.wait(min(remaining,.01))

    def stop(self,*,drain_timeout=0,finish_admitted=False):
        require(isinstance(drain_timeout,(int,float)) and not isinstance(drain_timeout,bool) and 0<=drain_timeout<=5,
                'INVALID_TIMEOUT','bounded stop drain deadline required')
        self._stopped=True
        if finish_admitted:
            self._draining=True
            try:self.drain(drain_timeout)
            finally:self._draining=False
        self.poll()
        active=self._active.plan.operation_id if self._active else None
        if self._sealed is None:
            for k,r in tuple(self._records.items()):
                if r.outcome=='pending' and k!=active:self._records[k]=ExecutionRecord(k,'not_started',error='ENGINE_STOP')
        if self._owns_pool and not self._pool_closed:
            self._pool.shutdown(wait=False,cancel_futures=False);self._pool_closed=True

    def settle_uncertain(self):
        self._mutable();self.poll()
        require(self._stopped,'SETTLEMENT_MISMATCH','explicit stop required before uncertainty settlement')
        require(self._active is not None or any(r.outcome=='unknown' for r in self._records.values()),
                'SETTLEMENT_MISMATCH','known settled execution cannot be relabelled uncertain')
        if self._active is not None:self._forced_unknown.add(self._active.plan.operation_id)
        self._uncertain=True

    def snapshot(self):
        self.poll()
        if self._sealed is not None:return self._sealed
        attempts=[]
        for a in sorted(self._plans.values(),key=lambda a:a.submission_ordinal):
            operations=[]
            for o in sorted(a.operations,key=lambda o:o.sequence):
                r=self._records[o.operation_id]
                if o.operation_id in self._forced_unknown:
                    model=self._active.context.observed()[0] if self._active and self._active.plan.operation_id==o.operation_id else r.model
                    r=ExecutionRecord(o.operation_id,'unknown',model,error='HOST_UNCERTAINTY_SETTLEMENT')
                elif self._active and self._active.plan.operation_id==o.operation_id and not self._active.returned.is_set():
                    r=ExecutionRecord(o.operation_id,'unknown',self._active.context.observed()[0])
                scalar=r.scalar
                operations.append(PlateOperation(o.operation_id,o.sequence,o.dependencies,o.kind,r.outcome,r.model,
                    scalar.raw_text if isinstance(scalar,OCRScalar) else None,
                    scalar.confidence if isinstance(scalar,OCRScalar) else None,
                    scalar if isinstance(scalar,MarkerScalar) else None))
            attempts.append(PlateCropAttempt(a.attempt_id,a.submission_ordinal,a.candidate_id,
                tuple(o.operation_id for o in a.operations),tuple(operations)))
        settled=self._active is None and all(r.outcome not in {'pending','unknown'} for r in self._records.values())
        unknown=any(r.outcome=='unknown' for r in self._records.values()) or bool(self._forced_unknown)
        settlement='uncertain' if self._uncertain else 'settled' if settled else 'open'
        if unknown and not self._uncertain:settlement='open'
        transcript=PlateTranscript(self._identity,'enabled',tuple(self._candidates.values()),
            self._expected,tuple(attempts),
            self._detection_complete,self._admission_complete,settlement)
        require(len(canonical_json_bytes(pack_transcript(transcript)))<=self._limits[-1],
                'CAPACITY_EXCEEDED','retained evidence exceeds reserved profile; never truncate')
        if self._life.closure is not None:PlateMetadata().evaluate(self._life,transcript)
        return transcript

    def prepare_publication(self,codec,*,batch_id,stream_id):
        self.poll()
        require(not self._blockers,'ENGINE_EVIDENCE_CONFLICT','conflicting original crop/execution evidence prevents publication')
        require(codec.org==self._identity.context.org_id,'PARENT_MISMATCH','publisher tenant differs')
        import re
        require(type(stream_id) is str and re.fullmatch(r'[1-9][0-9]{0,18}-0',stream_id),
                'INVALID_STREAM_ID','bounded stable explicit positive stream ID required')
        if self._publication is not None:
            # Sealing forbids candidate/plan/closure mutation. Retry transport
            # from the retained packet without repeating Plate interpretation.
            require((batch_id,stream_id)==(self._publication.batch_id,self._publication.stream_id),
                    'PAYLOAD_CONFLICT','prepared publication identity/bytes changed')
            return self._publication
        transcript=self.snapshot()
        evaluation=PlateMetadata().evaluate(self._life,transcript)
        require(evaluation.state=='terminal','EXECUTION_WAITING','no publication for unfinished work')
        raw=codec.make('plate.batch',dict(life=pack_life(self._life),batch_id=batch_id,revision=0,
                                        transcript=pack_transcript(transcript)))
        require(len(raw)<=self._max_publication_bytes,'CAPACITY_EXCEEDED','publication capacity; nothing discarded')
        publication=Publication(batch_id,codec.decode(raw).event_id,stream_id,raw)
        self._sealed=transcript;self._publication=publication
        return publication

    def publish(self,client,journal,codec,*,batch_id,stream_id):
        key=journal.key('engine.evidence.plate')
        require(key.endswith(f':{{vehicle-v1.{self._identity.context.org_id}}}:engine.evidence.plate'),
                'PARENT_MISMATCH','publisher journal tenant differs')
        publication=self.prepare_publication(codec,batch_id=batch_id,stream_id=stream_id)
        if not self._confirmed:
            reply=client.eval(PRODUCE,1,key,publication.stream_id,
                        publication.payload,journal.max_stream)
            require(reply in (publication.stream_id,publication.stream_id.encode()),
                    'PUBLICATION_UNCONFIRMED','retain exact payload until explicit-ID acknowledgment')
            self._confirmed=True
        return publication

    def confirm_progressive_terminal(self, transcript):
        """Host attests the matching Plate terminal handoff has been confirmed.

        This is separate from request admission and scalar execution publication.
        The live v2 host verifies the fenced Plate journal ACK and original bytes
        before making this explicit crop-release handshake.
        """
        self.poll()
        require(self.progressive and self._active is None and not self._blockers
                and self._admission_complete and transcript == self.snapshot()
                and transcript.settlement == 'settled',
                'COMPLETION_MISMATCH', 'exact retired v2 transcript required')
        require(PlateMetadata().evaluate(self._life,transcript).state == 'terminal',
                'EXECUTION_WAITING', 'unfinished enabled work cannot release crops')
        self._sealed=transcript
        self._confirmed=True

    def cleanup(self,*,discard=False):
        self.poll()
        require(self._confirmed or (discard and self._stopped),'UNCONFIRMED_PUBLICATION','retain crop until confirmed handoff or explicit stopped-host discard')
        for key,resource in tuple(self._resources.items()):
            if key in self._released:continue
            if self._active and self._active.crop_id==key:continue
            try:
                if not resource.released:resource.release()
                require(resource.released is True,'RELEASE_UNCONFIRMED','private handle did not acknowledge release')
            finally:
                if resource.released is True:
                    self._released.add(key);self._resources[key]=None
        return tuple(sorted(k for k in self._resources if k not in self._released))
