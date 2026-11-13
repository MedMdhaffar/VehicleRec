"""Single-owner plate consumer for file/external-frame ObservationCoordinator.

No decoder or vehicle inference. Workers return frozen outcomes and never hold
consumer/segment references. Deadline-expired running tasks cannot be killed by
Python; they remain charged to the bounded in-flight budget until completion.
Their late outcomes are discarded only after immutable unresolved evidence has
been transferred. No DB1-ready outcome is claimed for an unsettled lifetime.
"""
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
import math
import time
import threading

import numpy as np

from contracts.db1.reference import select_unreadable_attempt, classify_plate_omission, plate_attempt_rank
from contracts.db1.row_policies import admit_native_plate_text
try:
    from common.plate_association import associate_plate
except ModuleNotFoundError as exc:
    if exc.name != 'common':
        raise
    from vehicle.common.plate_association import associate_plate
from run_video_pipeline import config
from run_video_pipeline.plate_detector import PlateCrop, crop_detections, deduplicate_detections
from run_video_pipeline.run_video_prototype import (
    TrackState, RecognitionAttempt, TrackerDetections, _detection_for_track,
    DEFAULT_ASSOCIATION_PADDING,
)
from run_video_pipeline.schemas import UnifiedPlateResult
from run_video_pipeline.shared_evidence import (
    RunContext, VehicleFrame, VehicleIdentity, OwnedCrop, Execution, WorkerOutcome,
    PlateAttempt, SelectedPlate, FinalPlateEvidence, PlateAssociationEvidence,
)


class PlateCapacityError(RuntimeError):
    """Explicit backpressure before accepting additional work; nothing evicted."""


class _OwnedFuture(Future):
    def __init__(self):
        super().__init__()
        self.retired = threading.Event()

    @property
    def resources_released(self):
        return self.retired.is_set()


class _RecognitionExecutor:
    """A cancelled Future does not imply its queued crop was freed by Python.

    Keep its slot charged until the worker actually removes the work item. Only
    future/queue metadata is shared with workers; analytical state is never shared.
    """
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='shared-plate')

    def submit(self, function, *args):
        future = _OwnedFuture()

        def execute():
            try:
                if not future.set_running_or_notify_cancel():
                    return
                try:
                    future.set_result(function(*args))
                except BaseException as exc:
                    future.set_exception(exc)
            finally:
                future.retired.set()
        self.pool.submit(execute)
        return future

    def shutdown(self, *, wait=False, cancel_futures=False):
        # Published futures have already been settled/cancelled by owner closure.
        # Let queued wrappers retire so cancelled work releases its charged slot.
        self.pool.shutdown(wait=wait, cancel_futures=False)


def _resources_released(future):
    released = getattr(future, 'resources_released', None)
    if released is not None:
        return released is True
    # Opaque injected executors must explicitly acknowledge cancelled queue removal
    # to reclaim that slot. Completed, non-cancelled work has returned its outcome.
    return not future.cancelled()


class PrototypeRecognizer:
    """Reuse local OCR/routing/provider logic without standalone review writes.

    The supplied registrations describe the actual configured recognizers. A
    provider invocation is evidence of a request, not proof of remote accuracy.
    """
    def __init__(self, pipeline, *, local_model, agent_model=None):
        self.pipeline = pipeline
        self.models = {'private_local': local_model, 'agent': agent_model}

    def __call__(self, plate, record, *, receipt):
        def execution(route):
            model = self.models[route]
            if model is None:
                raise ValueError('executing unregistered recognizer')
            record(Execution(route, model))
        return self.pipeline.process_crop(plate, receipt=receipt,
                    execution_recorder=execution, persist_review=False)


def _work(recognizer, crop, confidence, completion_clock, max_result_bytes):
    executions = []

    def record(execution):
        if not isinstance(execution, Execution):
            raise ValueError('recognizer must record registered execution evidence')
        # Split OCR calls/provider retries belong to this single operation.
        if execution not in executions:
            executions.append(execution)
    result, error = None, None
    try:
        response = recognizer(PlateCrop(crop.array(), crop.bbox, confidence), record,
                              receipt=crop.receipt)
        if not isinstance(response, UnifiedPlateResult):
            raise ValueError('recognizer must return UnifiedPlateResult')
        result = response.model_dump_json().encode()
        if len(result) > max_result_bytes:
            result = None
            raise ValueError('recognition result exceeds configured byte budget')
    except Exception:
        error = 'recognizer_error'
    return WorkerOutcome(result, tuple(executions), error, completion_clock())


@dataclass
class _Operation:
    evidence: PlateAttempt
    future: object = None
    policy_score: float = 0
    area: int = 0
    sharpness: float = 0


@dataclass
class _Segment:
    identity: VehicleIdentity
    segment_id: int
    raw_id: int
    state: TrackState
    best: OwnedCrop | None = None
    quality: float | None = None
    operations: list[_Operation] = field(default_factory=list)
    sealed: bool = False


@dataclass
class _Vehicle:
    identity: VehicleIdentity
    segments: list[_Segment] = field(default_factory=list)
    detections: int = 0
    admitted: int = 0
    ambiguous: int = 0
    unassociated: int = 0
    rejected: int = 0


class SharedPlatePipeline:
    def __init__(self, context, *, detector, tracker, recognizer, executor=None,
                 capability='enabled', drain_timeout=1.0, max_pending=16,
                 max_segments=128, max_segments_per_vehicle=16, max_finalized=128,
                 max_crop_bytes=8*1024*1024, max_result_bytes=65536,
                 quality=None, completion_clock=time.monotonic, on_association=None,
                 association_padding=DEFAULT_ASSOCIATION_PADDING,
                 strong_quality=12000.0, strong_min_candidates=3,
                 min_candidates=12, stable_observations=5,
                 disappearance_frames=5, minimum_exit_candidates=8,
                 retry_improvement=1.50, recheck_detail_improvement=1.25,
                 replacement_confidence_gain=.02):
        if not isinstance(context, RunContext):
            raise ValueError('shared plate requires explicit RunContext')
        if context.source_kind != 'file':
            raise ValueError('shared plate currently supports file/external file frames only')
        if capability not in {'enabled', 'disabled', 'unsupported'}:
            raise ValueError('invalid plate capability')
        if not math.isfinite(drain_timeout) or drain_timeout < 0:
            raise ValueError('finite nonnegative closure deadline required')
        for value in (max_pending, max_segments, max_segments_per_vehicle, max_finalized, max_crop_bytes,
                      max_result_bytes, strong_min_candidates, min_candidates,
                      stable_observations, disappearance_frames, minimum_exit_candidates):
            if type(value) is not int or value < 1:
                raise ValueError('resource and candidate limits must be positive integers')
        if (association_padding < 0 or not math.isfinite(association_padding)
                or retry_improvement <= 1 or recheck_detail_improvement <= 1):
            raise ValueError('invalid tracking/recheck policy')
        self.context, self.capability = context, capability
        self.detector, self.tracker, self.recognizer = detector, tracker, recognizer
        self.executor = executor if executor is not None else _RecognitionExecutor()
        self._owns_executor = executor is None
        self.drain_timeout, self.max_pending = drain_timeout, max_pending
        self.max_segments, self.max_segments_per_vehicle = max_segments, max_segments_per_vehicle
        self.max_finalized = max_finalized
        self.max_crop_bytes, self.max_result_bytes = max_crop_bytes, max_result_bytes
        self.quality, self.completion_clock = quality, completion_clock
        self.on_association = on_association
        self.association_padding = association_padding
        self.strong_quality, self.strong_min_candidates = strong_quality, strong_min_candidates
        self.min_candidates, self.stable_observations = min_candidates, stable_observations
        self.disappearance_frames, self.minimum_exit_candidates = disappearance_frames, minimum_exit_candidates
        self.retry_improvement, self.recheck_detail_improvement = retry_improvement, recheck_detail_improvement
        self.replacement_confidence_gain = replacement_confidence_gain
        self._vehicles, self._plate_current, self._inflight, self._pending = {}, {}, {}, {}
        self._next_segment, self._next_attempt, self._last_frame = 1, 1, -1
        self._closed_through, self._closed_ranges = 0, []
        self._result_owner = None
        self._shutdown = False

    def claim_result_delivery(self, owner):
        if owner is None or (self._result_owner is not None and self._result_owner is not owner):
            raise RuntimeError('plate results already belong to another owner')
        self._result_owner = owner

    def _check_owner(self, owner):
        if self._result_owner is not None and owner is not self._result_owner:
            raise RuntimeError('only the coordinator may transfer plate results')

    def _closed(self, sid):
        return sid <= self._closed_through or any(a <= sid <= b for a, b in self._closed_ranges)

    def _remember_closed(self, sid):
        merged = []
        for a, b in sorted(self._closed_ranges + [(sid, sid)]):
            if merged and a <= merged[-1][1]+1:
                merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
            else:
                merged.append((a,b))
        if merged and merged[0][0] <= self._closed_through+1:
            self._closed_through = merged.pop(0)[1]
        self._closed_ranges = merged

    def poll(self):
        """Owner-only completion application; also reap detached late futures."""
        for ordinal, (future, operation) in tuple(self._inflight.items()):
            if not future.done():
                continue
            if operation is not None:
                self._complete(operation)
            if _resources_released(future):
                del self._inflight[ordinal]
            else:
                self._inflight[ordinal] = (future, None)

    def _complete(self, operation):
        attempt = operation.evidence
        try:
            outcome = operation.future.result()
            if not isinstance(outcome, WorkerOutcome):
                raise TypeError('invalid worker outcome')
            operation.evidence = replace(attempt, status='failed' if outcome.error else 'completed',
                executions=outcome.executions, result_json=outcome.result_json,
                error=outcome.error, completion_monotonic=outcome.completion_monotonic)
        except CancelledError:
            operation.evidence = replace(attempt, status='cancelled', error='cancelled_before_execution')
        except Exception:
            operation.evidence = replace(attempt, status='failed', error='worker_exception', execution_unknown=True)
        operation.future = None

    def _refresh(self, segment):
        state = segment.state
        state.attempts, state.cached_attempt_index = [], None
        state.pending = next((op.future for op in segment.operations if op.future is not None), None)
        for operation in segment.operations:  # Submission order, never completion order.
            a = operation.evidence
            if a.status == 'submitted':
                continue
            result = (UnifiedPlateResult.model_validate_json(a.result_json) if a.result_json else
                      UnifiedPlateResult(sample_id=a.attempt_id, timestamp=a.crop.receipt.utc.isoformat(),
                        route='unknown', route_confidence=0, route_reason=a.error or a.status,
                        source='none', model_version='none', bbox=list(a.crop.bbox), status='error'))
            admitted = self._read(a, result)
            # Analytical completeness admission is separate from standalone review status.
            if not admitted:
                result = result.model_copy(update={'status': 'rejected'})
            state.record_attempt(RecognitionAttempt(result, 0, 0,
                crop_score=operation.policy_score, crop_area=operation.area,
                crop_sharpness=operation.sharpness,
                crop_frame=a.crop.receipt.frame_index), self.replacement_confidence_gain)

    @staticmethod
    def _model(attempt, result=None):
        route = result.route if result is not None else None
        return next((e for e in reversed(attempt.executions) if route is None or e.route == route), None)

    @classmethod
    def _read(cls, attempt, result):
        if (attempt.crop_score is not None and (not math.isfinite(attempt.crop_score) or attempt.crop_score < 0)):
            return None
        if result.status != 'accepted' or cls._model(attempt, result) is None:
            return None
        return admit_native_plate_text(result.model_dump(mode='json'))

    def _submit(self, segment, trigger, *, closure=False):
        self.poll()
        if len(segment.operations) >= 2 or any(o.future is not None for o in segment.operations) or segment.best is None:
            return
        if not closure and len(self._inflight) >= self.max_pending:
            raise PlateCapacityError('pending plate work full; poll/drain before retry; no operation admitted')
        ordinal = self._next_attempt
        self._next_attempt += 1
        attempt = PlateAttempt(f'{self.context.run_id}:plate:{ordinal}', ordinal,
                segment.identity, segment.segment_id, segment.raw_id, segment.best,
                segment.quality, trigger, 'submitted')
        operation = _Operation(attempt, policy_score=segment.state.best_score,
                               area=segment.state.best_area, sharpness=segment.state.best_sharpness)
        segment.operations.append(operation)  # Ledger exists BEFORE dispatch, even if submit raises.
        if len(self._inflight) >= self.max_pending:
            operation.evidence = replace(attempt, status='submission_failed', error='closure_capacity_exhausted')
            self._refresh(segment)
            return
        try:
            operation.future = self.executor.submit(_work, self.recognizer, attempt.crop,
                segment.state.best_confidence, self.completion_clock, self.max_result_bytes)
        except Exception:
            operation.evidence = replace(attempt, status='submission_failed', error='executor_submission_failed')
        else:
            self._inflight[ordinal] = (operation.future, operation)
        self._refresh(segment)

    def _schedule(self, segment, *, exiting=False):
        self._refresh(segment)
        state = segment.state
        if exiting and not segment.operations and state.candidate_count >= self.minimum_exit_candidates:
            self._submit(segment, 'parent_or_plate_segment_end', closure=True)
        elif not segment.sealed:
            trigger = state.initial_trigger_reason(strong_quality=self.strong_quality,
                strong_min_candidates=self.strong_min_candidates, stable_min_candidates=self.min_candidates,
                stable_observations=self.stable_observations)
            if trigger:
                self._submit(segment, trigger)
            elif state.recheck_is_warranted(self.retry_improvement, self.recheck_detail_improvement):
                self._submit(segment, 'substantially_better_crop_recheck')

    def _seal(self, segment):
        self._schedule(segment, exiting=True)
        segment.sealed = True
        # Best candidate buffers are unnecessary once no new operation can be admitted.
        segment.best = None
        segment.state.best_crop = segment.state.first_crop = None

    def process_vehicle_frame(self, image, observation):
        if self._shutdown or not isinstance(observation, VehicleFrame) or observation.receipt.frame_index <= self._last_frame:
            raise ValueError('shared plate requires an increasing active VehicleFrame')
        if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
            raise ValueError('shared plate requires BGR uint8 image')
        ids = set()
        for vehicle in observation.vehicles:
            life = vehicle.lifetime
            if (life.context != self.context or life.last_receipt is not observation.receipt
                    or life.closure is not None or life.quarantined or vehicle.service_track_id in ids
                    or self._closed(vehicle.service_track_id)):
                raise ValueError('vehicle does not belong to this open run/frame')
            ids.add(vehicle.service_track_id)
            identity = VehicleIdentity.from_lifetime(life)
            existing = self._vehicles.setdefault(identity.service_track_id, _Vehicle(identity))
            if existing.identity != identity:
                raise ValueError('parent identity changed within service lifetime')
        self._last_frame = observation.receipt.frame_index
        self.poll()
        if self.capability != 'enabled':
            return ()
        # Existing plate detector and BYTETracker adapters only; no vehicle model.
        detections = deduplicate_detections(self.detector.predict(image, conf=None, imgsz=None),
                                            iou_threshold=config.PLATE_DEDUP_IOU)
        tracked = self.tracker.update(TrackerDetections.from_tuples(detections,
                                    association_padding=self.association_padding), image)
        boxes = [dict(track_id=v.service_track_id, bbox=list(v.bbox)) for v in observation.vehicles]
        used, seen, admitted = set(), set(), []
        for row in tracked:
            raw = int(row[4])
            detection = _detection_for_track(row, detections, self.association_padding, used)
            if detection is None:
                continue
            seen.add(raw)
            association = associate_plate(list(detection[:4]), boxes)
            sid = association['selected_track_id']
            if self.on_association is not None:
                self.on_association(PlateAssociationEvidence(observation.receipt, raw,
                    association['status'], sid, tuple((s['track_id'], s['score']) for s in association['scores'])))
            if sid is None:
                # Diagnostic candidates only, never recognition/analytical admission.
                candidates = {s['track_id'] for s in association['scores']} if association['status'] == 'ambiguous' else ids
                for candidate in candidates:
                    v = self._vehicles[candidate]
                    v.detections += 1
                    if association['status'] == 'ambiguous':
                        v.ambiguous += 1
                    else:
                        v.unassociated += 1
                old = self._plate_current.pop(raw, None)
                if old is not None:
                    self._seal(old)
                continue
            v = self._vehicles[sid]
            v.detections += 1
            safe = crop_detections(image, [detection], min_width=4, min_height=4)
            if not safe:
                v.rejected += 1
                continue
            plate = safe[0]
            if plate.crop.nbytes > self.max_crop_bytes:
                raise PlateCapacityError('plate crop exceeds configured byte bound; crop not admitted')
            segment = self._plate_current.get(raw)
            if segment is None or segment.identity != v.identity:
                if (sum(len(p.segments) for p in self._vehicles.values()) >= self.max_segments
                        or len(v.segments) >= self.max_segments_per_vehicle):
                    raise PlateCapacityError('segment bound reached; release closures before new admission')
                if segment is not None:
                    self._seal(segment)
                segment = _Segment(v.identity, self._next_segment, raw,
                                   TrackState(raw, self._last_frame, self._last_frame))
                self._next_segment += 1
                v.segments.append(segment)
                self._plate_current[raw] = segment
            v.admitted += 1
            if segment.state.consider(plate, self._last_frame, image.shape):
                segment.best = OwnedCrop.capture(plate.crop, plate.bbox, observation.receipt)
                segment.quality = (self.quality(plate.crop.copy(), plate.confidence) if self.quality is not None
                                   else segment.state.best_score)
                if segment.quality is not None:
                    segment.quality = float(segment.quality)
            segment.state.observed_frames.clear()  # Diagnostics must not grow with frame count.
            segment.state.first_crop = None
            self._schedule(segment)
            admitted.append((raw, sid, segment.segment_id))
        for raw, segment in tuple(self._plate_current.items()):
            if raw not in seen and self._last_frame-segment.state.last_frame >= self.disappearance_frames:
                self._seal(segment)
                del self._plate_current[raw]
        return tuple(admitted)

    def _settle(self, segments):
        self.poll()
        futures = [o.future for s in segments for o in s.operations if o.future is not None]
        if futures:
            wait(futures, timeout=self.drain_timeout)
        self.poll()
        for segment in segments:
            for operation in segment.operations:
                future = operation.future
                if future is None:
                    continue
                cancelled = future.cancel()
                if cancelled or future.done():
                    self._complete(operation)
                    ordinal = operation.evidence.submission_ordinal
                    if _resources_released(future):
                        self._inflight.pop(ordinal, None)
                    else:
                        self._inflight[ordinal] = (future, None)
                else:
                    # Detach state; future owns bytes and returns an outcome, never mutates evidence.
                    operation.evidence = replace(operation.evidence, status='settlement_timeout',
                        error='closure_deadline_exceeded', execution_unknown=True)
                    self._inflight[operation.evidence.submission_ordinal] = (future, None)
                    operation.future = None

    def _select(self, segments, ledger):
        reads = []
        for segment in segments:
            self._refresh(segment)
            index = segment.state.cached_attempt_index
            if index is None:
                continue
            settled = [o.evidence for o in segment.operations if o.evidence.status != 'submitted']
            a = settled[index]
            if a.result_json:
                result = UnifiedPlateResult.model_validate_json(a.result_json)
                text = self._read(a, result)
                if text:
                    reads.append((a, text, self._model(a, result)))
        ranking = lambda a: plate_attempt_rank(dict(crop_score=a.crop_score,
            submission_ordinal=a.submission_ordinal, attempt_id=a.attempt_id), coerce_quality=False)
        if reads:
            a, text, execution = min(reads, key=lambda x: ranking(x[0]))
            return 'read', SelectedPlate(a.attempt_id, a.segment_id, a.crop, a.crop_score,
                                         text, execution.route, execution.model), None
        policies = [dict(attempt_id=a.attempt_id, submission_ordinal=a.submission_ordinal,
            submitted=True, associated=True, crop_accepted=True, crop_score=a.crop_score,
            ts=a.crop.receipt.utc.isoformat(), executed_model=self._model(a).model.policy() if a.executions else None)
            for a in ledger]
        selection = select_unreadable_attempt(policies)
        selected = selection['selected_attempt_id']
        if selected is not None:
            a = next(a for a in ledger if a.attempt_id == selected)
            execution = self._model(a)
            return 'unreadable', SelectedPlate(a.attempt_id, a.segment_id, a.crop, a.crop_score,
                                               None, execution.route, execution.model), None
        return 'omitted', None, selection['omission_reason']

    def finalize_vehicle(self, vehicle, *, owner=None):
        self._check_owner(owner)
        life = vehicle.lifetime
        identity = VehicleIdentity.from_lifetime(life)
        sid = identity.service_track_id
        if identity.context != self.context or life.closure is None:
            raise ValueError('plate finalization needs closed lifetime in this run')
        if sid in self._pending:
            return self._pending[sid]
        if self._closed(sid):
            return None
        if len(self._pending) >= self.max_finalized:
            raise PlateCapacityError('finalized plate queue full; transfer evidence before retry; nothing discarded')
        v = self._vehicles.get(sid) or _Vehicle(identity)
        if v.identity != identity:
            raise ValueError('closure identity differs from admitted parent')
        for segment in v.segments:
            if not segment.sealed:
                self._seal(segment)
        self._settle(v.segments)
        ledger = tuple(o.evidence for s in v.segments for o in s.operations)
        incomplete = any(a.execution_unknown for a in ledger)
        if incomplete:
            outcome, selected, omission = 'omitted', None, 'unsettled_operation'
        elif ledger:
            outcome, selected, omission = self._select(v.segments, ledger)
        else:
            association = (None if v.admitted else
                           'ambiguous' if v.ambiguous else 'unassociated' if v.unassociated else None)
            omission = classify_plate_omission(dict(capability=self.capability, association=association,
                        detections=v.detections, admitted_crops=v.admitted, models_executed=0))
            outcome, selected = 'omitted', None
        evidence = FinalPlateEvidence(identity, life.first_receipt, life.last_receipt,
            life.closure, life.quarantined, outcome, omission, ledger, selected,
            v.detections, v.admitted, v.ambiguous, v.unassociated, incomplete)
        self._pending[sid] = evidence
        self._vehicles.pop(sid, None)
        self._plate_current = {raw:s for raw,s in self._plate_current.items() if s.identity.service_track_id != sid}
        self._remember_closed(sid)
        return evidence

    def release_finalized(self, service_id, *, owner=None):
        """Idempotent after finalization, including retries after a side effect."""
        self._check_owner(owner)
        if type(service_id) is not int or service_id < 1 or not self._closed(service_id):
            raise ValueError('release requires a known finalized service identity')
        self._pending.pop(service_id, None)

    def drain_finalized(self, *, owner=None):
        self._check_owner(owner)
        result = tuple(self._pending.values())
        self._pending.clear()
        return result

    def shutdown(self):
        """Call AFTER owner stop/finish and closure transfer. Never waits on running OCR."""
        if self._vehicles:
            raise RuntimeError('deliver all owner closures before plate shutdown')
        if not self._shutdown and self._owns_executor:
            self.executor.shutdown(wait=False, cancel_futures=True)
        self._shutdown = True
        self.poll()
