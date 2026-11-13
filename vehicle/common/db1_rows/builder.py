"""Pure completed-evidence construction. No coordinator mutation or I/O.

Identity policy names are logical fact/attempt identities, not content hashes.
Callers must supply prior accepted results to detect changed-payload retries.
"""
from dataclasses import dataclass, replace
from hashlib import sha256
import math
from jsonschema.exceptions import ValidationError
from uuid import UUID

from contracts.db1.reference import (canonical_json_bytes, derive_date_key,
                                    select_unreadable_attempt, classify_plate_omission,
                                    retry_comparison, strict_loads)
from contracts.db1.row_policies import admit_native_plate_text
from ..db1_context import positive_bigint
from ..frame_receipt import FrameReceipt
from ..observation_evidence import VehicleIdentity, OwnedCrop
from ..observation_coordinator import CompletedVehicle
from ..vehicle_frame import VehicleBoxObservation
from .records import Resolutions
from .core import (FrozenJSON, UUID5Policy, Blocked, require, score, frozen_tree,
                   verify_shared_receipts, receipt_in_lifetime, integer, validate_lifetime, validate_parent)
from .validation import ContractValidation
from .natural_policy import NaturalPolicy, Omitted

KINDS = ('fact_vehicle', 'fact_vehicle_colour', 'fact_plate_read', 'fact_vehicle_make')


@dataclass(frozen=True, slots=True)
class FactResult:
    row_kind: str
    delivery: FrozenJSON | None
    sql: FrozenJSON | None
    diagnostic: FrozenJSON
    metadata: FrozenJSON
    issues: tuple[str, ...]
    retry: str  # new | identical | conflict | blocked | omitted

    @property
    def ready(self):
        return self.sql is not None and self.retry != 'conflict'

    def snapshot(self):
        if self.sql is None or self.delivery is None:
            raise ValueError('no resolved SQL projection')
        return dict(schema_version='db1-row-snapshot-v0', row_kind=self.row_kind,
                    delivery_payload=self.delivery.value(), resolved_sql_row=self.sql.value())


@dataclass(frozen=True, slots=True)
class Construction:
    facts: tuple[FactResult, ...]

    @property
    def ready_rows(self):
        return tuple(f for f in self.facts if f.ready)


def crop_valid(crop, life):
    require(isinstance(crop, OwnedCrop) and type(crop.pixels) is bytes and len(crop.pixels) > 0,
            'INVALID_CROP', 'owned crop bytes required')
    require(len(crop.shape) == 3 and crop.shape[2] == 3 and crop.dtype == '|u1'
            and all(type(i) is int and i > 0 for i in crop.shape)
            and len(crop.pixels) == math.prod(crop.shape)
            and len(crop.bbox) == 4 and all(type(i) is int for i in crop.bbox)
            and crop.bbox[2] - crop.bbox[0] == crop.shape[1]
            and crop.bbox[3] - crop.bbox[1] == crop.shape[0],
            'INVALID_CROP', 'retained BGR uint8 crop geometry/bytes are inconsistent')
    receipt_in_lifetime(crop.receipt, life)


class RowBuilder:
    def __init__(self, *, identity_policy, validation: ContractValidation):
        if not isinstance(validation, ContractValidation):
            raise ValueError('caller-loaded P1 validation required')
        if not callable(getattr(identity_policy, 'row_uid', None)) or not callable(getattr(identity_policy, 'attempt_uid', None)):
            raise ValueError('explicit stable row and attempt identity policy required')
        self.identity_policy = identity_policy
        self.validation = validation

    def construct(self, completed: CompletedVehicle, resolutions: Resolutions | None, *, prior: Construction | None = None):
        """Return proposed results. Caller retains evidence until it accepts results.

        A prior Construction is caller-owned accepted in-memory history, not a
        durable deduplication store. A conflict projection is retained for review
        but excluded from ready_rows. No new identity is allocated on conflict.
        """
        if not isinstance(completed, CompletedVehicle) or not isinstance(completed.vehicle, VehicleBoxObservation):
            raise ValueError('CompletedVehicle with immutable owner snapshot required')
        owner, life = completed.vehicle, completed.vehicle.lifetime
        identity = VehicleIdentity.from_lifetime(life)
        # Malformed fundamental identity cannot be represented by P1 diagnostics.
        positive_bigint(identity.service_track_id, 'service_track_id')
        integer(identity.tracker_epoch, 2**63 - 2, 'native tracker epoch', 0)
        integer(identity.raw_track_id, 2**63 - 1, 'raw_track_id', 0)
        integer(identity.lifetime_sequence, 2**63 - 1, 'lifetime_sequence')
        names = [name for name, _ in completed.consumer_evidence]
        global_error = None
        try:
            frozen_tree(completed)
            verify_shared_receipts(completed, {})
            require(len(set(names)) == len(names), 'DUPLICATE_CONSUMER', 'duplicate completed consumer name')
            validate_lifetime(life)
        except (Blocked, ValueError, TypeError, AttributeError, KeyError) as error:
            global_error = error
        consumers = dict(completed.consumer_evidence)
        facts = []
        for kind, name in zip(KINDS, (None, 'colour', 'plate', 'make')):
            evidence = consumers.get(name) if name else owner
            fact = self._construct_fact(kind, evidence, owner, identity, resolutions, global_error)
            # Parent eligibility is part of the new proposal, before retry comparison.
            if kind != 'fact_vehicle' and fact.sql is not None and not facts[0].ready:
                diag = fact.diagnostic.value()
                diag.update(record_type='quarantine', reason='construction_blocked', error_code='PARENT_NOT_READY',
                            row_uid=None, payload_sha256=None)
                fact = replace(fact, sql=None, diagnostic=FrozenJSON.of(diag), retry='blocked',
                               issues=fact.issues + ('PARENT_NOT_READY: required vehicle projection is blocked',))
            if prior is not None:
                # Match using the immutable owner, including when the new payload
                # vanished during omission or evidence validation.
                natural_key = (str(identity.context.org_id), identity.context.run_id,
                               identity.context.camera.camera_id, identity.service_track_id)
                matches = [old for old in prior.facts if old.row_kind == kind and old.delivery is not None
                           and tuple(old.delivery.value()[k] for k in ('org_id', 'run_id', 'camera_id', 'track_id')) == natural_key]
                require(len(matches) <= 1, 'DUPLICATE_PRIOR', 'prior contains duplicate row identities')
                if matches:
                    old = matches[0]
                    identical = fact.delivery is not None and old.delivery.canonical == fact.delivery.canonical
                    if old.sql is not None and fact.sql is not None:
                        identical = retry_comparison(old.snapshot(), fact.snapshot())['classification'] == 'identical'
                    conflict = not identical or (old.sql is not None and fact.sql is None)
                    if conflict:
                        proposal_diag = fact.diagnostic.value()
                        metadata = fact.metadata.value()
                        metadata.update(proposal_diagnostic=proposal_diag,
                                        accepted_row_uid=old.delivery.value()['uid'],
                                        accepted_delivery_sha256=old.delivery.digest,
                                        accepted_sql_sha256=old.sql.digest if old.sql is not None else None)
                        diag = dict(proposal_diag)
                        # P1 incomplete settlement requires unsettled_operation.
                        # Keep the entire valid original diagnostic in metadata;
                        # the primary envelope describes the identity conflict.
                        diag.pop('settlement', None)
                        diag.update(record_type='quarantine', reason='construction_blocked',
                                    error_code='PAYLOAD_CONFLICT', row_uid=old.delivery.value()['uid'])
                        self.validation.diagnostic(diag)
                        fact = replace(fact, retry='conflict', diagnostic=FrozenJSON.of(diag),
                                       metadata=FrozenJSON.of(metadata),
                                       issues=fact.issues + ('PAYLOAD_CONFLICT: accepted payload/projection changed or disappeared',))
                    else:
                        fact = replace(fact, retry='identical')
            facts.append(fact)
        return Construction(tuple(facts))

    def _construct_fact(self, kind, evidence, owner, identity, resolutions, global_error):
        life, context = owner.lifetime, owner.lifetime.context
        receipt, selected, media = life.first_receipt if isinstance(life.first_receipt, FrameReceipt) else None, None, None
        metadata = dict(native_tracker_epoch=life.tracker_epoch, lifetime_sequence=life.lifetime_sequence,
                        closure_cause=getattr(life.closure, 'value', str(life.closure)) if life.closure else None,
                        quarantined=life.quarantined)
        delivery, sql, issues, lookup = None, None, [], None
        reason, code, record_type, retry = 'row_finalized', None, 'row', 'new'
        settlement = None
        try:
            if global_error:
                raise global_error
            require(not life.quarantined, 'CLOCK_QUARANTINE', 'quarantined lifetime cannot emit analytical facts')
            if kind == 'fact_vehicle_make':
                raise Blocked('UNSUPPORTED_MANUFACTURER', 'no current manufacturer producer; structural fixtures are not capability')
            require(evidence is not None, 'UNAVAILABLE_CONSUMER', 'completed consumer evidence was not supplied')
            if kind != 'fact_vehicle':
                validate_parent(evidence, life, identity)
            uid = str(UUID(str(self.identity_policy.row_uid(identity, kind))))
            if kind == 'fact_vehicle':
                require(owner.vehicle_type != 'person', 'UNSUPPORTED_CLASS', 'person is excluded from vehicle service admission')
                fields = NaturalPolicy.vehicle(owner)
            elif kind == 'fact_vehicle_colour':
                receipt, fields, selected = self._colour(evidence, life)
            else:
                receipt, fields, selected, plate_metadata, settlement = self._plate(evidence, life, identity)
                metadata.update(plate_metadata)
            delivery = NaturalPolicy.delivery(kind, identity, receipt, fields, uid, self.validation)
            if kind == 'fact_plate_read':
                # Preserve the facade's reviewed failure precedence: missing
                # run registration must be reported before media resolution.
                self._resolution(resolutions, context, life)
                media = self._media(resolutions, identity, evidence.selected.crop)
                fields['selected_attempt']['crop_media'] = media.natural() if media else None
            sql = self.project_natural(kind,delivery,resolutions,context,life,media=media)
        except (Blocked, ValueError, TypeError, AttributeError, KeyError, ValidationError) as error:
            code = getattr(error, 'code', 'INVALID_EVIDENCE_OR_SCHEMA')
            issues.append(f'{code}: {error}')
            if isinstance(error, Omitted) and error.metadata is not None:
                metadata.update(error.metadata)
            if code == 'CLOCK_QUARANTINE':
                reason, record_type, retry = 'clock_discontinuity', 'quarantine', 'blocked'
            elif code in {'UNSUPPORTED_MANUFACTURER', 'UNAVAILABLE_CONSUMER'}:
                reason, record_type, retry = 'unsupported_capability', 'omission', 'omitted'
            elif isinstance(error, Omitted):
                reason, record_type, retry = error.reason, 'omission', 'omitted'
                if reason == 'unsettled_operation':
                    unresolved = sum(a.execution_unknown or a.status in {'submitted', 'settlement_timeout'} for a in evidence.ledger)
                    settlement = dict(incomplete=True, attempts=len(evidence.ledger), unresolved_attempts=unresolved)
                    selected = None
            else:
                reason = 'catalogue_unresolved' if code in {'MISSING_RESOLUTION', 'MISSING_DATE', 'MISSING_DIMENSION', 'MODEL_RESOLUTION_MISMATCH', 'MISSING_SOURCE_MEDIA', 'UNRESOLVED_DEVELOPMENT'} else 'construction_blocked'
                record_type, retry = 'quarantine', 'blocked'
        # Schema-incompatible evidence must not leak an invalid natural payload.
        if delivery is not None:
            try:
                self.validation.delivery(kind, delivery)
            except Exception as error:
                delivery = None
                issues.append(f'INVALID_NATURAL_PAYLOAD: {error.message if hasattr(error, "message") else error}')
        natural = FrozenJSON.of(delivery) if delivery is not None else None
        resolved = FrozenJSON.of(sql) if sql is not None else None
        if resolved is not None and kind in {'fact_vehicle', 'fact_plate_read'} and media is None:
            reason = 'media_unavailable'
        diag = dict(schema_version='db1-diagnostics-v1', record_type=record_type, row_kind=kind,
                    row_uid=delivery['uid'] if sql is not None else None, run_id=context.run_id,
                    track_id=identity.service_track_id, reason=reason,
                    time_basis='host_replay_receipt' if context.source_kind == 'file' else 'host_frame_receipt',
                    frame_index=receipt.frame_index if receipt else None, media_pts=receipt.media_pts if receipt and receipt.media_pts is not None and receipt.media_pts >= 0 else None,
                    raw_tracker=dict(raw_id=identity.raw_track_id, epoch=identity.tracker_epoch + 1),
                    association=None, selected=selected, error_code=code,
                    media_status='stored' if media else 'unavailable' if sql is not None and kind in {'fact_vehicle', 'fact_plate_read'} else 'not_requested',
                    payload_sha256=natural.digest if sql is not None else None)
        if receipt and receipt.media_pts is not None and receipt.media_pts < 0:
            metadata['native_media_pts'] = receipt.media_pts  # P1 diagnostic PTS cannot represent negative preroll.
        if settlement is not None and reason != 'clock_discontinuity':
            diag['settlement'] = settlement
        self.validation.diagnostic(diag)
        return FactResult(kind, natural, resolved, FrozenJSON.of(diag), FrozenJSON.of(metadata), tuple(issues), retry)


    def project_natural(self, kind, base, resolutions, context, life, *, media=None):
        """Canonical supported SQL projection, shared by both delivery paths."""
        require(kind in KINDS[:3], 'UNSUPPORTED_MANUFACTURER', 'no manufacturer producer')
        self.validation.delivery(kind,base)
        lookup = self._resolution(resolutions, context, life)
        # Validate explicit registrations even when natural payload is eligible.
        run = resolutions.run
        row = dict(org_id=base['org_id'], run_key=run.run_key, camera_key=run.camera.camera_key,
                   date_key=derive_date_key(base['ts'], context.camera.timezone), ts=base['ts'], uid=base['uid'],
                   track_id=base['track_id'])
        require(row['date_key'] in run.date_keys, 'MISSING_DATE', 'supporting receipt date is not registered')
        lookup['parent'] = {k: row[k] for k in ('org_id', 'run_key', 'camera_key', 'track_id')}
        if kind == 'fact_vehicle':
            dim = self._dimension(resolutions.classes, base['class']['name'], 'class')
            require(dim.group == 'vehicle', 'CLASS_GROUP_MISMATCH', 'class must belong to vehicle group')
            lookup['class'] = dict(name=dim.name, class_group=dim.group, class_key=dim.key)
            row.update(first_ts=base['first_ts'], last_ts=base['last_ts'], class_key=dim.key,
                       class_conf=base['class_conf'], frames_observed=base['frames_observed'], best_media_key=None)
            supplied = [m for m in resolutions.media if m.kind == 'vehicle_crop' and m.service_track_id == base['track_id']]
            require(not supplied, 'UNSUPPORTED_VEHICLE_MEDIA', 'owner exposes no selected classification crop to validate vehicle media')
            lookup['media'] = None
        elif kind == 'fact_vehicle_colour':
            model = self._model(resolutions, base['model'])
            lookup['model'] = dict(**model.identity.policy(), model_key=model.key)
            dim = self._dimension(resolutions.colours, base['colour'], 'colour') if base['colour'] is not None else None
            lookup['catalogue'] = dict(name=dim.name, key=dim.key, approved=True) if dim else None
            row.update(model_key=model.key, colour_key=dim.key if dim else None, conf=base['conf'],
                       state=base['state'], samples=base['samples'])
        else:
            attempt = base['selected_attempt']
            model = self._model(resolutions, attempt['model'])
            lookup['model'] = dict(**model.identity.policy(), model_key=model.key)
            category = attempt['plate_category']
            dim = self._dimension(resolutions.plate_categories, category, 'plate_category') if category is not None else None
            lookup['catalogue'] = dict(name=dim.name, key=dim.key, approved=True) if dim else None
            require(attempt['crop_media']==(media.natural() if media else None), 'MEDIA_MISMATCH', 'natural selected media differs from resolution')
            lookup['media'] = dict(**media.natural(), media_key=media.media_key) if media else None
            row.update(model_key=model.key, outcome=base['outcome'], plate_text=attempt['plate_text'],
                       plate_category_key=dim.key if dim else None, conf=attempt['conf'], attempts=base['attempts'],
                       crop_media_key=media.media_key if media else None, crop_score=attempt['crop_score'])
        # Media is now part of natural payload; validate both channels together.
        snapshot = dict(schema_version='db1-row-snapshot-v0', row_kind=kind,
                        delivery_payload=base, resolved_sql_row=row)
        self.validation.snapshot(snapshot, lookup)
        return row

    def _resolution(self, resolutions, context, life):
        require(isinstance(resolutions, Resolutions), 'MISSING_RESOLUTION',
                'explicit organization, camera version, run, dimensions, models, dates and media resolution required')
        run = resolutions.run
        require(not run.synthetic or context.environment == 'test', 'SYNTHETIC_RESOLUTION', 'synthetic registration is test-only')
        require(context.environment != 'development' and context.database_keys_resolved,
                'UNRESOLVED_DEVELOPMENT', 'unprovisioned development context cannot produce a DB1-ready projection')
        require(run.org_id == context.org_id and run.run_id == context.run_id and run.run_key == context.resolution.run_key
                and run.camera == context.camera and run.lane_key == context.resolution.lane_key
                and run.lane_name == context.resolution.lane_name and run.detector == context.resolution.detector
                and run.source_kind == ('file' if context.source_kind == 'file' else 'stream')
                and run.source_media_key == context.resolution.source_media_key
                and run.started_at == context.started_at and run.service_version == context.service_version,
                'RUN_RESOLUTION_MISMATCH', 'resolution differs from original organization/camera SCD2/run/detector/source')
        require(run.camera.valid_from <= life.first_receipt.utc
                and (run.camera.valid_to is None or life.last_receipt.utc < run.camera.valid_to),
                'CAMERA_VERSION_MISMATCH', 'vehicle receipts cross registered camera version validity')
        if run.source_kind == 'file':
            inputs = [m for m in resolutions.media if m.media_key == run.source_media_key]
            require(len(inputs) == 1, 'MISSING_SOURCE_MEDIA', 'file run input media registration missing')
            source = inputs[0]
            require(source.kind == 'clip' and source.org_id == run.org_id and source.run_id == run.run_id
                    and source.camera_key == run.camera.camera_key and source.camera_id == run.camera.camera_id
                    and source.run_key in {None, run.run_key}, 'SOURCE_MEDIA_MISMATCH', 'input clip ownership differs from run')
        return dict(run=dict(org_id=str(run.org_id), run_id=run.run_id, run_key=run.run_key,
                             camera_id=run.camera.camera_id, camera_key=run.camera.camera_key,
                             timezone=run.camera.timezone, date_keys=run.date_keys))

    @staticmethod
    def _dimension(records, name, role):
        matches = [d for d in records if d.name == name and d.approved]
        require(len(matches) == 1, 'MISSING_DIMENSION', f'explicit approved {role} mapping missing for {name!r}')
        return matches[0]

    @staticmethod
    def _model(resolutions, natural):
        matches = [m for m in resolutions.models if m.identity.policy() == natural]
        require(len(matches) == 1, 'MODEL_RESOLUTION_MISMATCH', 'selected executed recognition model is not resolved')
        return matches[0]

    @staticmethod
    def _media(resolutions, identity, crop):
        matches = [m for m in resolutions.media if m.kind == 'plate_crop' and m.service_track_id == identity.service_track_id]
        if not matches:
            return None  # SQL nullable; crop retention is not media registration.
        require(len(matches) == 1, 'MEDIA_AMBIGUOUS', 'one selected supporting plate object required')
        m, c = matches[0], identity.context
        require(m.org_id == c.org_id and m.run_key == c.resolution.run_key and m.run_id == c.run_id
                and m.camera_key == c.camera.camera_key and m.camera_id == c.camera.camera_id
                and m.ts == crop.receipt.utc and m.frame_index == crop.receipt.frame_index
                and m.crop_sha256 == sha256(crop.pixels).hexdigest()
                and m.width == crop.shape[1] and m.height == crop.shape[0],
                'MEDIA_MISMATCH', 'registered crop ownership/receipt/bytes/geometry differs from selected evidence')
        return m

    @staticmethod
    def _colour(e, life):
        return NaturalPolicy.colour(e, life, crop_valid)

    def _plate(self, e, life, identity):
        require(type(e.ledger) is tuple, 'MUTABLE_EVIDENCE', 'immutable plate operation ledger required')
        ids, ordinals = [a.attempt_id for a in e.ledger], [a.submission_ordinal for a in e.ledger]
        require(len(set(ids)) == len(ids) and len(set(ordinals)) == len(ordinals),
                'PLATE_LEDGER_MISMATCH', 'duplicate native attempt identity/ordinal')
        integer(len(e.ledger), 2147483647, 'plate operation count', 0)
        for count in (e.detections, e.admitted_crops, e.ambiguous_crops, e.unassociated_crops):
            integer(count, 2147483647, 'plate diagnostic count', 0)
        for a in e.ledger:
            require(isinstance(a.attempt_id, str) and bool(a.attempt_id.strip()), 'PLATE_LEDGER_MISMATCH', 'native attempt identity required')
            integer(a.raw_plate_id, 2**63 - 1, 'raw plate tracker ID', 0)
            require(a.parent == identity, 'PARENT_MISMATCH', 'operation belongs to another original parent')
            positive_bigint(a.segment_id, 'native segment ID')
            positive_bigint(a.submission_ordinal, 'native submission ordinal')
            require(type(a.execution_unknown) is bool, 'PLATE_LEDGER_MISMATCH', 'execution uncertainty must be an explicit bool')
            crop_valid(a.crop, life)
            score(a.crop_score, quality=True)
            require(a.status in {'completed', 'failed', 'cancelled', 'submission_failed', 'submitted', 'settlement_timeout'},
                    'PLATE_LEDGER_MISMATCH', 'unknown operation status')
            require(a.status not in {'cancelled', 'submission_failed'} or not a.executions,
                    'FABRICATED_EXECUTION', 'cancelled/submission-failed operation claims execution')
        ranks = {ordinal: index + 1 for index, ordinal in enumerate(sorted(ordinals))}
        mappings = dict(attempt_mappings=[dict(native_attempt_id=a.attempt_id, native_segment_id=a.segment_id,
            native_submission_ordinal=a.submission_ordinal, mapped_submission_ordinal=ranks[a.submission_ordinal],
            frame_index=a.crop.receipt.frame_index, ts=a.crop.receipt.utc.isoformat(),
            attempt_uid=str(UUID(str(self.identity_policy.attempt_uid(identity, a.segment_id, a.attempt_id))))) for a in e.ledger])
        unresolved = sum(a.execution_unknown or a.status in {'submitted', 'settlement_timeout'} for a in e.ledger)
        require(type(e.settlement_incomplete) is bool and e.settlement_incomplete == bool(unresolved), 'SETTLEMENT_MISMATCH', 'settlement flag differs from operation ledger')
        if unresolved:
            raise Omitted('unsettled_operation', 'operations have unknown/unsettled execution; no unreadable or failure_before_model substitution', mappings)
        if e.outcome == 'omitted':
            require(e.selected is None, 'PLATE_OMISSION_MISMATCH', 'omission retains selected analytical evidence')
            if e.ledger:
                policies = self._unreadable_policies(e.ledger)
                require(select_unreadable_attempt(policies)['selected_attempt_id'] is None,
                        'PLATE_OMISSION_MISMATCH', 'eligible executed operation cannot be omitted as failure_before_model')
                expected = 'failure_before_model'
            else:
                # Global detection/association counters do not prove ownership.
                # Preserve producer omission claim only; never turn counters into rows.
                association = None if e.admitted_crops else 'ambiguous' if e.ambiguous_crops else 'unassociated' if e.unassociated_crops else None
                expected = classify_plate_omission(dict(capability='enabled', association=association,
                    detections=e.detections, admitted_crops=e.admitted_crops, models_executed=0))
            require(e.omission_reason in {expected, 'disabled_capability', 'unsupported_capability'},
                    'PLATE_OMISSION_MISMATCH', 'producer omission disagrees with P1 operation eligibility')
            raise Omitted(e.omission_reason, 'no selected admitted operation; detection counters do not establish parent ownership', mappings)
        require(e.outcome in {'read', 'unreadable'} and e.selected is not None and e.omission_reason is None,
                'INCOMPLETE_PLATE', 'plate analytical outcome needs one selected original attempt')
        s = e.selected
        matches = [a for a in e.ledger if a.attempt_id == s.attempt_id]
        require(len(matches) == 1, 'SELECTED_ATTEMPT_MISMATCH', 'selected attempt absent from original ledger')
        a = matches[0]
        require(a.segment_id == s.segment_id and a.crop == s.crop and a.crop_score == s.crop_score
                and any(x.route == s.route and x.model == s.model for x in a.executions)
                and a.status in {'completed', 'failed'},
                'SELECTED_ATTEMPT_MISMATCH', 'selected crop/score/route/model/segment differs from original operation')
        route = {'private_local': 'local_private', 'agent': 'agent'}.get(s.route)
        require(route is not None and s.model.task == ('vlm' if route == 'agent' else 'ocr'),
                'PLATE_MODEL_ROUTE_MISMATCH', 'selected execution task must match route; agent OCR attribution is blocked')
        result = {}
        if a.result_json is not None:
            result = strict_loads(a.result_json)
            canonical_json_bytes(result)  # reject nonfinite values
            require(isinstance(result, dict), 'INVALID_PLATE_RESULT', 'operation result must be a JSON object')
        text, conf, category, complete, structural = None, None, None, False, None
        if e.outcome == 'read':
            execution = next((x for x in reversed(a.executions) if x.route == result.get('route')), None)
            require(execution is not None and execution.route == s.route and execution.model == s.model,
                    'RESULT_EXECUTION_MISMATCH', 'read model differs from last execution matching the result route')
            require(a.status == 'completed' and a.error is None and result.get('status') == 'accepted'
                    and result.get('route') == s.route and result.get('source') == ('agent' if route == 'agent' else 'local_ocr'),
                    'PLATE_READ_ADMISSION', 'selected read is not an accepted result of its actual execution route')
            text = admit_native_plate_text(result)
            require(text is not None, 'PLATE_READ_ADMISSION', 'selected result lacks analytical completeness admission')
            require(text == s.text, 'SELECTED_TEXT_MISMATCH', 'selected text differs from selected attempt normalized result')
            evidence = result.get('evidence', {})
            structural = evidence.get('local', {}).get('parser_status') == 'valid' if route == 'local_private' else None
            complete = evidence.get('text_complete') is True if route == 'agent' else structural
            require(complete, 'PLATE_READ_ADMISSION', 'read lacks defined completeness admission; agent assertion is not accuracy proof')
            require('text_confidence' in result and 'plate_category' in result,
                    'MISSING_PLATE_RESULT_FIELDS', 'selected native read lacks explicit confidence/category fields')
            conf = score(result['text_confidence'], nullable=False)
            category = result.get('plate_category')
            category = None if category in {None, 'unknown'} else category
        else:
            require(s.text is None, 'SELECTED_TEXT_MISMATCH', 'unreadable selected attempt must not claim text')
            for operation in e.ledger:
                if operation.result_json is not None and operation.executions and operation.status == 'completed':
                    native = strict_loads(operation.result_json)
                    text_claim = admit_native_plate_text(native)
                    eligible_route = any(x.route == native.get('route') for x in operation.executions)
                    require(text_claim is None or not eligible_route,
                            'UNREADABLE_READ_CONFLICT', 'unreadable ledger contains an admitted executed read')
            choice = select_unreadable_attempt(self._unreadable_policies(e.ledger))
            require(choice['selected_attempt_id'] == a.attempt_id, 'UNREADABLE_SELECTION_MISMATCH', 'selected unreadable attempt differs from reusable P1 selection')
            require(a.executions[-1].route == s.route and a.executions[-1].model == s.model,
                    'SELECTED_ATTEMPT_MISMATCH', 'unreadable model is not last executed route of selected attempt')
        rank = ranks[a.submission_ordinal]
        attempt_uid = str(UUID(str(self.identity_policy.attempt_uid(identity, a.segment_id, a.attempt_id))))
        fields = NaturalPolicy.plate(outcome=e.outcome, attempts=len(e.ledger),
            attempt_uid=attempt_uid, ordinal=rank, receipt=a.crop.receipt, route=route, model=s.model,
            complete=complete, structural=structural, text=text, category=category,
            confidence=conf, crop_score=a.crop_score)
        selected = dict(attempt_uid=attempt_uid, route=route, model_name=s.model.model_name,
                        model_version=s.model.version, attempts=len(e.ledger), samples=None)
        metadata = dict(**mappings, native_attempt_id=a.attempt_id, native_segment_id=a.segment_id,
                        native_submission_ordinal=a.submission_ordinal, mapped_submission_ordinal=rank,
                        native_raw_plate_id=a.raw_plate_id)
        return a.crop.receipt, fields, selected, metadata, dict(incomplete=False, attempts=len(e.ledger), unresolved_attempts=0)

    @staticmethod
    def _unreadable_policies(ledger):
        return [dict(attempt_id=a.attempt_id, submission_ordinal=a.submission_ordinal,
                     submitted=True, associated=True, crop_accepted=True, crop_score=a.crop_score,
                     ts=a.crop.receipt.utc.isoformat(), executed_model=a.executions[-1].model.policy() if a.executions else None)
                for a in ledger]
