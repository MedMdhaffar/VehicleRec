"""Pure Plate decisions over bounded original Engine operation transcripts.

References identify supplied evidence, never database/media registration. No
recognizer is invoked here. Whole/region operations share one logical crop attempt.
"""
from dataclasses import dataclass, asdict
from types import SimpleNamespace
import re

from .observation_evidence import VehicleIdentity
from .frame_receipt import FrameReceipt
from .db1_rows.core import (FrozenJSON, require, score, frozen_tree,
    receipt_in_lifetime, verify_shared_receipts, validate_lifetime, integer)
from contracts.db1.reference import select_unreadable_attempt, classify_plate_omission, plate_attempt_rank
from contracts.db1.row_policies import admit_native_plate_text
from vehicle.license_plate_service.run_video_pipeline.text_policy import OCRResult, normalize_plate_text
from vehicle.license_plate_service.run_video_pipeline.scalar_policy import (
    LocalPlatePolicy, DEFAULT_POLICY, MarkerScalar, assemble_plate_fields,
    local_route, replace_cached_read)


@dataclass(frozen=True, slots=True)
class PlateModel:
    model_name: str
    version: int
    task: str  # ocr | detect

    def policy(self):
        return dict(model_name=self.model_name, version=self.version, task=self.task)


@dataclass(frozen=True, slots=True)
class PlateCandidate:
    identity: VehicleIdentity
    candidate_id: str
    segment_id: int
    raw_plate_id: int
    receipt: FrameReceipt
    association: str  # associated | ambiguous | unassociated
    crop_accepted: bool
    crop_ref: str | None
    crop_sha256: str | None
    bbox: tuple[int, int, int, int]  # original frame coordinates, Engine supplied
    crop_size: tuple[int, int]  # Engine supplied width/height, no image transforms
    quality: float | None
    detector_confidence: float | None
    quality_algorithm: str


@dataclass(frozen=True, slots=True)
class PlateOperation:
    operation_id: str
    sequence: int
    dependencies: tuple[str, ...]
    kind: str  # whole_ocr | marker | left_ocr | right_ocr
    outcome: str  # pending | not_started | succeeded | failed | unknown
    model: PlateModel | None = None  # actual execution, never configured default
    raw_text: str | None = None
    confidence: float | None = None
    marker: MarkerScalar | None = None
    route: str = 'private_local'


@dataclass(frozen=True, slots=True)
class PlateCropAttempt:
    attempt_id: str
    submission_ordinal: int
    candidate_id: str
    expected_operation_ids: tuple[str, ...]
    operations: tuple[PlateOperation, ...]


@dataclass(frozen=True, slots=True)
class PlateTranscript:
    identity: VehicleIdentity
    capability: str
    candidates: tuple[PlateCandidate, ...]
    expected_attempt_ids: tuple[str, ...]
    attempts: tuple[PlateCropAttempt, ...]
    detection_complete: bool
    admission_complete: bool
    settlement: str  # open | settled | uncertain (explicit host settlement)
    policy: LocalPlatePolicy = DEFAULT_POLICY


@dataclass(frozen=True, slots=True)
class PlateSelection:
    attempt_id: str
    segment_id: int
    receipt: FrameReceipt
    crop_score: float | None
    operation_id: str
    model: PlateModel
    text: str | None
    confidence: float | None
    category: str | None


@dataclass(frozen=True, slots=True)
class PlateResult:
    transcript: PlateTranscript
    outcome: str  # read | unreadable | omitted
    omission_reason: str | None
    selected: PlateSelection | None
    provenance: FrozenJSON

    def original_receipts(self):
        return tuple(c.receipt for c in self.transcript.candidates)

    def validate(self, lifetime):
        expected = PlateMetadata().evaluate(lifetime, self.transcript)
        require(expected.state == 'terminal' and expected.result == self,
                'PLATE_DECISION_MISMATCH', 'Plate terminal differs from original canonical transcript')

    def natural_fields(self, lifetime, identity_policy):
        """Domain-authenticated P1 fields; Aggregator never selects a reading."""
        self.validate(lifetime)
        require(self.selected is not None, 'INCOMPLETE_PLATE', 'omission has no row')
        s = self.selected
        ordered = sorted(self.transcript.attempts, key=lambda a: a.submission_ordinal)
        rank = next(i+1 for i,a in enumerate(ordered) if a.attempt_id == s.attempt_id)
        from uuid import UUID
        attempt_uid = str(UUID(str(identity_policy.attempt_uid(self.transcript.identity,
                                                               s.segment_id, s.attempt_id))))
        from .db1_rows.natural_policy import NaturalPolicy
        return s.receipt, NaturalPolicy.plate(outcome=self.outcome, attempts=len(ordered),
            attempt_uid=attempt_uid, ordinal=rank, receipt=s.receipt, route='local_private', model=s.model,
            complete=self.outcome == 'read', structural=True if self.outcome == 'read' else None,
            text=s.text, category=s.category, confidence=s.confidence, crop_score=s.crop_score)


@dataclass(frozen=True, slots=True)
class PlateEvaluation:
    state: str
    waiting: tuple[str, ...]
    result: PlateResult | None


def _json_scalars(value):
    """Convert immutable record tuples to JSON arrays before canonical encoding."""
    if isinstance(value, tuple):
        return [_json_scalars(v) for v in value]
    if isinstance(value, list):
        return [_json_scalars(v) for v in value]
    if isinstance(value, dict):
        return {k: _json_scalars(v) for k,v in value.items()}
    return value


def _name(value):
    require(type(value) is str and bool(value.strip()) and len(value.encode()) <= 256,
            'INVALID_METADATA', 'bounded explicit scalar identity required')


def _ids(values):
    require(type(values) is tuple and len(values) <= 128, 'CAPACITY_EXCEEDED', 'bounded immutable ID set required')
    for value in values:
        _name(value)
    require(len(set(values)) == len(values), 'DUPLICATE_ID', 'duplicate expected identity')


def _box(box, width=None, height=None):
    require(type(box) is tuple and len(box) == 4 and all(type(n) is int and 0 <= n <= 2147483647 for n in box)
            and box[2] > box[0] and box[3] > box[1], 'INVALID_GEOMETRY', 'positive integer rectangle required')
    if width is not None:
        require(box[2] <= width and box[3] <= height, 'INVALID_GEOMETRY', 'marker exceeds crop-local coordinates')


def _regional_inputs(operation, operations):
    """Resolve the exact whole/marker executions declared by one region."""
    whole = [operations[d] for d in operation.dependencies if operations[d].kind == 'whole_ocr']
    marker = [operations[d] for d in operation.dependencies if operations[d].kind == 'marker']
    require(len(whole) == len(marker) == 1, 'INVALID_DEPENDENCY',
            'region must identify exactly one whole OCR and one marker execution')
    return whole[0], marker[0]


class PlateMetadata:
    """Semantic ownership only. Engine supplies admission/execution/settlement."""
    def evaluate(self, lifetime, transcript):
        t = transcript
        require(isinstance(t, PlateTranscript), 'INVALID_METADATA', 'typed Plate transcript required')
        frozen_tree(t)
        validate_lifetime(lifetime)
        require(not lifetime.quarantined, 'CLOCK_QUARANTINE', 'quarantined lifetime cannot emit facts')
        identity = VehicleIdentity.from_lifetime(lifetime)
        require(t.identity == identity, 'PARENT_MISMATCH', 'Plate transcript differs from owner')
        require(t.capability in {'enabled', 'disabled', 'unsupported'} and t.settlement in {'open','settled','uncertain'},
                'INVALID_METADATA', 'explicit capability and settlement required')
        require(type(t.detection_complete) is bool and type(t.admission_complete) is bool,
                'INVALID_METADATA', 'explicit complete-work flags required')
        require(type(t.candidates) is tuple and type(t.attempts) is tuple
                and len(t.candidates) <= 128 and len(t.attempts) <= 128,
                'CAPACITY_EXCEEDED', 'at most 128 candidates/crop attempts; never truncate')
        require(isinstance(t.policy, LocalPlatePolicy), 'INVALID_METADATA', 'explicit local policy required')
        for field in asdict(t.policy).values():
            score(field, nullable=False)
        _ids(t.expected_attempt_ids)
        verify_shared_receipts((lifetime, t), {})
        candidates = {}
        segments = {}
        for c in t.candidates:
            require(isinstance(c, PlateCandidate) and c.identity == identity, 'PARENT_MISMATCH', 'candidate has wrong parent')
            _name(c.candidate_id)
            require(c.candidate_id not in candidates, 'DUPLICATE_ID', 'duplicate candidate')
            candidates[c.candidate_id] = c
            integer(c.segment_id, 2**63-1, 'segment')
            integer(c.raw_plate_id, 2**63-1, 'raw plate ID', 0)
            require(segments.setdefault(c.segment_id, c.raw_plate_id) == c.raw_plate_id,
                    'INVALID_ASSOCIATION', 'segment changes original raw plate tracker identity')
            receipt_in_lifetime(c.receipt, lifetime)
            require(c.association in {'associated','ambiguous','unassociated'} and type(c.crop_accepted) is bool,
                    'INVALID_ASSOCIATION', 'explicit association/admission required')
            _box(c.bbox)
            require(type(c.crop_size) is tuple and len(c.crop_size) == 2
                    and all(type(n) is int and 0 < n <= 65536 for n in c.crop_size), 'INVALID_GEOMETRY', 'bounded crop dimensions required')
            score(c.quality, quality=True); score(c.detector_confidence)
            _name(c.quality_algorithm)
            require((c.crop_ref is None) == (c.crop_sha256 is None), 'INVALID_CROP_REFERENCE', 'paired reference/fingerprint required')
            if c.crop_ref is not None:
                _name(c.crop_ref)
                require(type(c.crop_sha256) is str and re.fullmatch('[0-9a-f]{64}', c.crop_sha256),
                        'INVALID_CROP_REFERENCE', 'SHA256 required, never registration proof')
            require(not c.crop_accepted or (c.association == 'associated' and c.crop_ref is not None),
                    'INVALID_ASSOCIATION', 'accepted crop requires associated supplied reference')
        require(all(isinstance(a, PlateCropAttempt) for a in t.attempts),
                'INVALID_METADATA', 'typed logical crop attempts required')
        attempts, all_ops, expected_ops, interpreted, provenance = {}, set(), set(), {}, []
        incomplete = not t.detection_complete or not t.admission_complete or t.settlement == 'open'
        uncertain = t.settlement == 'uncertain'
        for a in t.attempts:
            integer(a.submission_ordinal, 2**63-1, 'attempt ordinal')
        for a in sorted(t.attempts, key=lambda a: a.submission_ordinal):
            require(isinstance(a, PlateCropAttempt), 'INVALID_METADATA', 'typed logical crop attempt required')
            _name(a.attempt_id)
            require(a.attempt_id not in attempts and a.attempt_id in t.expected_attempt_ids,
                    'DUPLICATE_ID', 'duplicate or unexpected attempt identity')
            integer(a.submission_ordinal, 2**63-1, 'attempt ordinal')
            require(all(b.submission_ordinal != a.submission_ordinal for b in attempts.values()), 'DUPLICATE_ID', 'duplicate ordinal')
            attempts[a.attempt_id] = a
            c = candidates.get(a.candidate_id)
            require(c is not None and c.crop_accepted, 'INVALID_ASSOCIATION', 'attempt must reference original associated accepted crop')
            _ids(a.expected_operation_ids)
            require(not expected_ops.intersection(a.expected_operation_ids), 'DUPLICATE_ID',
                    'expected operation identity reused across logical attempts')
            expected_ops.update(a.expected_operation_ids)
            require(type(a.operations) is tuple and len(a.operations) <= 16 and bool(a.expected_operation_ids),
                    'CAPACITY_EXCEEDED', '1..16 expected operations per crop required')
            require(len(a.expected_operation_ids) <= 16, 'CAPACITY_EXCEEDED', 'at most 16 expected operations')
            require(all(isinstance(o, PlateOperation) for o in a.operations),
                    'INVALID_METADATA', 'typed operations required')
            for o in a.operations:
                integer(o.sequence, 2147483647, 'request sequence')
            ops, sequences = {}, set()
            for o in sorted(a.operations, key=lambda o: o.sequence):
                require(isinstance(o, PlateOperation), 'INVALID_METADATA', 'typed operation required')
                _name(o.operation_id); _ids(o.dependencies)
                require(o.operation_id not in all_ops and o.operation_id in a.expected_operation_ids,
                        'DUPLICATE_ID', 'duplicate or unexpected operation')
                all_ops.add(o.operation_id)
                integer(o.sequence, 2147483647, 'request sequence')
                require(o.sequence not in sequences, 'DUPLICATE_ID', 'duplicate operation sequence')
                sequences.add(o.sequence)
                require(o.kind in {'whole_ocr','marker','left_ocr','right_ocr'} and o.route == 'private_local',
                        'UNSUPPORTED_ROUTE', 'only local whole/marker/region operations supported')
                require(all(d in ops for d in o.dependencies), 'INVALID_DEPENDENCY', 'dependencies must precede operation in same crop')
                require(o.outcome in {'pending','not_started','succeeded','failed','unknown'}, 'INVALID_EXECUTION', 'explicit execution outcome required')
                if o.kind in {'left_ocr','right_ocr'}:
                    _regional_inputs(o, ops)
                if o.outcome in {'succeeded','failed'}:
                    require(isinstance(o.model, PlateModel) and o.model.task == ('detect' if o.kind == 'marker' else 'ocr'),
                            'MODEL_PROVENANCE_MISMATCH', 'actual model/task required for known execution')
                if o.model is not None:
                    require(isinstance(o.model, PlateModel), 'MODEL_PROVENANCE_MISMATCH', 'typed execution model required')
                    _name(o.model.model_name); integer(o.model.version, 2147483647, 'model version')
                    require(o.model.task == ('detect' if o.kind == 'marker' else 'ocr'), 'MODEL_PROVENANCE_MISMATCH', 'task differs from operation')
                require(o.outcome not in {'pending','not_started'} or o.model is None,
                        'FABRICATED_EXECUTION', 'unexecuted operation claims model')
                if o.outcome == 'succeeded':
                    require(all(ops[d].outcome == 'succeeded' for d in o.dependencies), 'INVALID_DEPENDENCY', 'successful followup requires successful dependencies')
                    if o.kind == 'marker':
                        require(isinstance(o.marker, MarkerScalar) and o.raw_text is None and o.confidence is None,
                                'INVALID_EXECUTION', 'marker scalar output required')
                        m = o.marker
                        require(type(m.detected) is bool, 'INVALID_EXECUTION', 'explicit marker detection flag required')
                        score(m.confidence, nullable=False)
                        require(m.detected == (m.bbox is not None), 'INVALID_GEOMETRY', 'marker detected/bbox disagree')
                        if m.bbox is not None:
                            _box(m.bbox, *c.crop_size)
                            score(m.center_x, quality=True, nullable=False)
                            require(m.bbox[0] <= m.center_x <= m.bbox[2], 'INVALID_GEOMETRY', 'marker center inconsistent')
                        else:
                            require(m.center_x is None, 'INVALID_GEOMETRY', 'absent marker has center')
                    else:
                        require(type(o.raw_text) is str and len(o.raw_text.encode()) <= 4096 and o.marker is None,
                                'INVALID_EXECUTION', 'bounded original scalar OCR text required')
                        score(o.confidence, nullable=False)
                else:
                    require(o.raw_text is None and o.confidence is None and o.marker is None,
                            'CONFLICTING_RESULT', 'non-successful operation cannot claim scalar result')
                incomplete |= o.outcome == 'pending'
                uncertain |= o.outcome == 'unknown'
                ops[o.operation_id] = o
            incomplete |= set(ops) != set(a.expected_operation_ids)
            interpreted[a.attempt_id] = self._interpret(c, ops, t.policy)
            provenance.append(dict(attempt_id=a.attempt_id, candidate_id=a.candidate_id,
                submission_ordinal=a.submission_ordinal, expected_operations=sorted(a.expected_operation_ids),
                operations=[dict(asdict(o), dependencies=sorted(o.dependencies)) for o in ops.values()]))
        incomplete |= set(attempts) != set(t.expected_attempt_ids)
        if t.capability != 'enabled':
            require(not t.candidates and not t.attempts and not t.expected_attempt_ids,
                    'CAPABILITY_RESULT_CONFLICT', 'disabled/unsupported capability claims work')
            reason = classify_plate_omission(dict(capability=t.capability))
            return self._terminal(t, (), (), 'omitted', reason, None)
        if incomplete:
            return PlateEvaluation('waiting', ('engine_work',), None)
        require(not uncertain or t.settlement == 'uncertain', 'SETTLEMENT_MISMATCH', 'unknown execution needs explicit host uncertainty settlement')
        if uncertain:
            return self._terminal(t, provenance, candidates.values(), 'omitted', 'unsettled_operation', None)
        reads, cached = [], {}
        for a in sorted(t.attempts, key=lambda a: a.submission_ordinal):
            c = candidates[a.candidate_id]
            native, operation = interpreted[a.attempt_id]
            text = admit_native_plate_text(native)
            previous = cached.get(c.segment_id)
            if previous is None:
                cached[c.segment_id] = (a, native, operation, text)
            else:
                _, old, _, oldtext = previous
                replace, _ = replace_cached_read(old['status'], oldtext, old['category_confidence'],
                    native['status'], text, native['category_confidence'], .02)
                if replace:
                    cached[c.segment_id] = (a, native, operation, text)
        for a,native,o,text in cached.values():
            if text is not None and o is not None:
                reads.append((a,native,o,text))
        def policy(a):
            c = candidates[a.candidate_id]
            executed = [o for o in sorted(a.operations, key=lambda o:o.sequence)
                        if o.model is not None and o.model.task == 'ocr' and o.outcome in {'succeeded','failed'}]
            return dict(attempt_id=a.attempt_id, submission_ordinal=a.submission_ordinal,
                submitted=True, associated=True, crop_accepted=True, crop_score=c.quality,
                ts=c.receipt.utc.isoformat(), executed_model=executed[-1].model.policy() if executed else None)
        if reads:
            a,native,o,text = min(reads, key=lambda x:plate_attempt_rank(policy(x[0]), coerce_quality=False))
            c = candidates[a.candidate_id]
            selected = PlateSelection(a.attempt_id, c.segment_id, c.receipt, c.quality, o.operation_id,
                o.model, text, native['text_confidence'], 'private')
            return self._terminal(t, provenance, candidates.values(), 'read', None, selected)
        choice = select_unreadable_attempt([policy(a) for a in t.attempts])
        if choice['selected_attempt_id'] is not None:
            a = attempts[choice['selected_attempt_id']]; c = candidates[a.candidate_id]
            o = max((o for o in a.operations if o.model is not None and o.model.task == 'ocr'
                     and o.outcome in {'succeeded','failed'}), key=lambda o:o.sequence)
            selected = PlateSelection(a.attempt_id, c.segment_id, c.receipt, c.quality, o.operation_id, o.model, None, None, None)
            return self._terminal(t, provenance, candidates.values(), 'unreadable', None, selected)
        association = ('ambiguous' if any(c.association == 'ambiguous' for c in t.candidates) else
                       'unassociated' if any(c.association == 'unassociated' for c in t.candidates) else None)
        reason = classify_plate_omission(dict(capability='enabled', association=association,
            detections=len(candidates), admitted_crops=sum(c.crop_accepted for c in t.candidates), models_executed=0))
        return self._terminal(t, provenance, candidates.values(), 'omitted', reason, None)

    @staticmethod
    def _interpret(candidate, operations, policy):
        # Keep the full graph: latest-per-kind lookup alone loses the executions
        # that regional outputs were actually requested against.
        ops = {o.operation_id: o for o in operations.values()}
        kinds = {o.kind: o for o in sorted(ops.values(), key=lambda o:o.sequence)}
        last = max((o for o in ops.values() if o.kind != 'marker'
                    and o.outcome in {'succeeded','failed'}),
                   key=lambda o:o.sequence, default=None)
        whole_op, marker_op = kinds.get('whole_ocr'), kinds.get('marker')
        left, right = kinds.get('left_ocr'), kinds.get('right_ocr')
        if (last is not None and last.kind in {'left_ocr','right_ocr'}
                and left and right and left.outcome == right.outcome == 'succeeded'):
            left_inputs = _regional_inputs(left, ops)
            right_inputs = _regional_inputs(right, ops)
            require(left_inputs == right_inputs, 'INVALID_DEPENDENCY',
                    'left/right fusion requires the same whole OCR and marker executions')
            whole_op, marker_op = left_inputs
        else:
            # Partial/failed sides are not a regional fusion; a later whole OCR
            # gets its own canonical admission without borrowing earlier sides.
            # The last-OCR guard prevents lending an execution to old text.
            left = right = None

        def ocr(o):
            return OCRResult(o.raw_text, normalize_plate_text(o.raw_text), o.confidence) if o and o.outcome == 'succeeded' else OCRResult('', '', 0)
        marker = marker_op.marker if marker_op and marker_op.outcome == 'succeeded' else MarkerScalar(False)
        fields = assemble_plate_fields(candidate.bbox, candidate.detector_confidence,
            ocr(whole_op), marker, ocr(left), ocr(right), policy=policy)
        eligible, confidence, _ = local_route(SimpleNamespace(**fields), policy=policy)
        supporting = ((whole_op, left, right)
                      if fields['resolution_source'] == 'marker_left_right_ocr' else (whole_op,))
        operation = max((o for o in supporting if o is not None),
                        key=lambda o:o.sequence, default=None)
        # Attribute either regional order to its last supporting OCR. A later
        # failed or unrelated OCR on this route cannot lend a model to old text.
        eligible &= operation is not None and operation.outcome == 'succeeded' and operation == last
        native = dict(status='accepted' if eligible else 'rejected', route='private_local', source='local_ocr',
            plate_text_normalized=fields['normalized_plate'], text_confidence=fields['ocr_confidence'],
            category_confidence=confidence, plate_category='private', evidence=dict(local=fields))
        return native, operation if eligible else None

    @staticmethod
    def _terminal(t, attempts, candidates, outcome, reason, selected):
        # Canonical ordering applies to *all* original evidence, even rejected crops.
        candidate_values = []
        for c in sorted(candidates, key=lambda c:c.candidate_id):
            v = asdict(c); v.pop('identity')
            v['receipt'] = dict(frame_index=c.receipt.frame_index, utc=c.receipt.utc.isoformat(),
                                monotonic=c.receipt.monotonic, media_pts=c.receipt.media_pts)
            candidate_values.append(v)
        provenance = FrozenJSON.of(_json_scalars(dict(policy=asdict(t.policy), capability=t.capability,
            detection_complete=t.detection_complete, admission_complete=t.admission_complete,
            settlement=t.settlement, expected_attempts=sorted(t.expected_attempt_ids),
            candidates=candidate_values, attempts=list(attempts))))
        require(len(provenance.canonical) <= 4*1024*1024, 'CAPACITY_EXCEEDED', 'Plate provenance exceeds 4MiB; never truncate')
        # Store canonical transcript order so equivalent input ordering also gives
        # equal typed terminal results, independent of list/set arrival order.
        from dataclasses import replace
        canonical = replace(t, candidates=tuple(sorted(t.candidates, key=lambda c:c.candidate_id)),
            expected_attempt_ids=tuple(sorted(t.expected_attempt_ids)),
            attempts=tuple(replace(a, expected_operation_ids=tuple(sorted(a.expected_operation_ids)),
                operations=tuple(replace(o, dependencies=tuple(sorted(o.dependencies)))
                    for o in sorted(a.operations, key=lambda o:o.sequence)))
                for a in sorted(t.attempts, key=lambda a:a.submission_ordinal)))
        return PlateEvaluation('terminal', (), PlateResult(canonical, outcome, reason, selected, provenance))
