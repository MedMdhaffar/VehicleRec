"""Natural DB1 projection shared by the legacy facade and metadata boundary."""
import math
from decimal import Decimal
from .core import Blocked, require, score, integer, receipt_in_lifetime
from ..observation_evidence import RecognitionIdentity

class NaturalPolicy:
    @staticmethod
    def vehicle(owner):
        life = owner.lifetime
        require(owner.vehicle_type != 'person', 'UNSUPPORTED_CLASS', 'person is excluded')
        return dict(first_ts=life.first_receipt.utc.isoformat(), last_ts=life.last_receipt.utc.isoformat(),
                    **{'class': dict(name=owner.vehicle_type, class_group='vehicle')},
                    class_conf=score(owner.type_confidence), frames_observed=life.frames_observed, best_media=None)

    @staticmethod
    def delivery(kind, identity, receipt, fields, uid, validation):
        context = identity.context
        payload = dict(org_id=str(context.org_id), run_id=context.run_id,
                       camera_id=context.camera.camera_id, camera_timezone=context.camera.timezone,
                       ts=receipt.utc.isoformat(), uid=uid, track_id=identity.service_track_id, **fields)
        validation.delivery(kind, payload)
        return payload

    @staticmethod
    def plate(*, outcome, attempts, attempt_uid, ordinal, receipt, route, model,
              complete, structural, text, category, confidence, crop_score):
        """Shared P1 selected-attempt projection after domain admission.

        Legacy SQL resolution and pure metadata construction supply the same
        already-validated natural values; no model/key/crop fallback is chosen.
        """
        return dict(outcome=outcome, attempts=attempts, selected_attempt=dict(
            attempt_uid=attempt_uid, submission_ordinal=ordinal, ts=receipt.utc.isoformat(),
            route=route, model=model.policy(), text_complete=bool(complete),
            structural_valid=structural, plate_text=text, plate_category=category,
            conf=confidence, crop_score=crop_score, crop_media=None))

    @staticmethod
    def colour(e, life, crop_validator=None):
        if e.state == 'omitted':
            require(e.attempts == 0 and not e.samples and e.support_receipt is None,
                    'INVALID_COLOUR_OMISSION', 'colour omission contains attempted evidence')
            raise Omitted(e.omission_reason, 'no admitted colour attempt/capability')
        require(e.state in {'decided', 'undecided', 'error'} and e.attempts > 0,
                'INCOMPLETE_COLOUR', 'emitted colour requires attempts and a supported state')
        integer(e.attempts, 2147483647, 'colour attempts')
        integer(e.local_errors, 2147483647, 'local colour errors', 0)
        integer(e.agent_attempts, 2147483647, 'agent colour attempts', 0)
        require(len(e.samples) <= e.attempts
                and e.local_errors <= e.attempts, 'COLOUR_COUNT_MISMATCH', 'invalid colour attempt/sample counts')
        require(isinstance(e.executed_model, RecognitionIdentity), 'MISSING_MODEL_EVIDENCE',
                'completed colour must identify the executed algorithm/model; manifest names are not execution proof')
        receipt_in_lifetime(e.support_receipt, life)
        receipt_in_lifetime(e.last_attempt_receipt, life)
        require(all(s.receipt.frame_index <= e.last_attempt_receipt.frame_index for s in e.samples),
                'COLOUR_SUPPORT_MISMATCH', 'valid sample occurs after the last colour attempt')
        for sample in e.samples:
            receipt_in_lifetime(sample.receipt, life)
            score(sample.confidence, nullable=False)
            score(sample.quality, nullable=False)
            require(not isinstance(sample.weight, bool) and isinstance(sample.weight, (int, float, Decimal))
                    and math.isfinite(sample.weight) and sample.weight > 0, 'INVALID_COLOUR_SAMPLE', 'sample weight must be finite positive')
        require(len({s.receipt.frame_index for s in e.samples}) == len(e.samples),
                'COLOUR_COUNT_MISMATCH', 'duplicate same-frame valid samples')
        if e.source == 'agent_override':
            require(e.state == 'decided' and e.executed_model.task == 'vlm' and e.agent_attempts > 0
                    and e.agent_error is None and e.agent_crop is not None,
                    'COLOUR_MODEL_ROUTE_MISMATCH', 'agent override requires successful submitted crop and VLM attribution')
            crop_validator(e.agent_crop, life)
            require(e.support_receipt == e.agent_crop.receipt, 'COLOUR_SUPPORT_MISMATCH', 'agent support differs from submitted crop')
        else:
            require(e.source == 'local_hsv' and e.executed_model.task == 'classify',
                    'COLOUR_MODEL_ROUTE_MISMATCH', 'local colour requires executed classify algorithm')
            expected = max(e.samples, key=lambda s: s.receipt.frame_index).receipt if e.state == 'decided' and e.samples else e.last_attempt_receipt
            require(e.support_receipt == expected and (e.state != 'decided' or bool(e.samples)),
                    'COLOUR_SUPPORT_MISMATCH', 'local support is not last valid sample/defined last attempt')
        if e.best_crop is not None:
            crop_validator(e.best_crop, life)
        require(e.label is not None and e.confidence is not None if e.state == 'decided'
                else e.label is None and e.confidence is None,
                'COLOUR_DECISION_MISMATCH', 'colour fields differ from final state')
        conf = score(e.confidence)
        fields = dict(model=e.executed_model.policy(), colour=e.label, conf=conf, state=e.state,
                      samples=len(e.samples), support=dict(ts=e.support_receipt.utc.isoformat(),
                      source='agent_override' if e.source == 'agent_override' else 'local_temporal'))
        selected = dict(attempt_uid=None, route=fields['support']['source'], model_name=e.executed_model.model_name,
                        model_version=e.executed_model.version, attempts=e.attempts, samples=len(e.samples))
        return e.support_receipt, fields, selected



class Omitted(Blocked):
    def __init__(self, reason, message, metadata=None):
        require(reason in {'no_detection', 'rejected_crop', 'ambiguous_association', 'unassociated_crop',
                           'disabled_capability', 'unsupported_capability', 'failure_before_model', 'unsettled_operation'},
                'INVALID_OMISSION', 'omission lacks a supported explicit policy reason')
        self.reason = reason
        self.metadata = metadata
        super().__init__('EVIDENCE_OMITTED', message)
