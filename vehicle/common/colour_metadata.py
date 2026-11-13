"""Metadata-only Colour boundary. Engine supplies scalar measurements and receipts."""
from dataclasses import dataclass

from .observation_evidence import VehicleIdentity, RecognitionIdentity
from .frame_receipt import FrameReceipt
from .db1_rows.core import (Blocked, FrozenJSON, require, score, frozen_tree,
                            verify_shared_receipts, receipt_in_lifetime)
from vehicle.vehicle_color_service.vehicle_color_service.scalar_policy import (
    make_colour_observation, decide_colour)
from vehicle.vehicle_color_service.vehicle_color_service.evidence import (
    FinalColourEvidence, ColourSampleEvidence)


@dataclass(frozen=True, slots=True)
class ColourMeasurement:
    identity: VehicleIdentity
    attempt_id: str
    receipt: FrameReceipt
    label: str
    confidence: float
    quality: float
    algorithm: RecognitionIdentity
    quality_algorithm: str


@dataclass(frozen=True, slots=True)
class ColourResult:
    evidence: FinalColourEvidence
    provenance: FrozenJSON

    def original_measurements(self):
        """Recover every original scalar attempt, including rejected samples."""
        try:
            p = self.provenance.value()
            require(isinstance(p, dict) and isinstance(p.get('attempts'), list)
                    and len(p['attempts']) == self.evidence.attempts,
                    'MODEL_PROVENANCE_MISMATCH', 'original attempt provenance count differs')
            from contracts.db1.reference import parse_timestamp
            return tuple(ColourMeasurement(
                self.evidence.identity, a['attempt_id'],
                FrameReceipt(a['frame_index'], parse_timestamp(a['ts']),
                             float(a['monotonic']), float(a['media_pts']) if a['media_pts'] is not None else None),
                a['label'], float(a['confidence']), float(a['quality']),
                RecognitionIdentity(**a['algorithm']), a['quality_algorithm']) for a in p['attempts'])
        except Blocked:
            raise
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise Blocked('MODEL_PROVENANCE_MISMATCH', 'malformed original attempt provenance') from error

    def original_receipts(self):
        return tuple(m.receipt for m in self.original_measurements())

    def validate(self, lifetime):
        """Colour authenticates terminal claims by replaying its canonical policy.

        Aggregator delegates to this domain boundary; it has no Colour selection
        policy of its own. Replay includes invalid original attempts and checks
        both final claims and all supporting evidence/provenance.
        """
        measurements = self.original_measurements()
        require(all(m.algorithm == self.evidence.executed_model for m in measurements),
                'MODEL_PROVENANCE_MISMATCH', 'executed algorithm differs from original attempts')
        expected = ColourMetadata().finalize(lifetime, measurements)
        actual_decision = (self.evidence.state, self.evidence.label, self.evidence.confidence)
        expected_decision = (expected.evidence.state, expected.evidence.label, expected.evidence.confidence)
        require(actual_decision == expected_decision,
                'COLOUR_DECISION_MISMATCH', 'terminal Colour claims differ from original canonical decision')
        require(self.evidence == expected.evidence and self.provenance == expected.provenance,
                'MODEL_PROVENANCE_MISMATCH', 'Colour evidence/provenance differs from original canonical result')


class ColourMetadata:
    """Finalizes injected scalar attempts; never requests pixels or providers."""

    def finalize(self, lifetime, measurements: tuple[ColourMeasurement, ...]):
        require(type(measurements) is tuple and bool(measurements),
                'MISSING_ATTEMPTS', 'enabled colour requires explicit measured attempts')
        require(len(measurements) <= 128, 'CAPACITY_EXCEEDED', 'at most 128 colour attempts per lifetime')
        require(lifetime.closure is not None and not lifetime.quarantined,
                'INCOMPLETE_OR_QUARANTINED', 'closed non-quarantined lifetime required')
        require(all(isinstance(m, ColourMeasurement) for m in measurements),
                'INVALID_MEASUREMENT', 'typed scalar measurements required')
        frozen_tree(measurements)
        verify_shared_receipts(measurements, {})
        identity = VehicleIdentity.from_lifetime(lifetime)
        ordered = sorted(measurements, key=lambda m: (m.receipt.frame_index, m.attempt_id))
        require(len({m.attempt_id for m in ordered}) == len(ordered)
                and len({m.receipt.frame_index for m in ordered}) == len(ordered),
                'DUPLICATE_ATTEMPT', 'one original colour attempt per frame')
        algorithm = ordered[0].algorithm
        require(isinstance(algorithm, RecognitionIdentity) and algorithm.task == 'classify',
                'MODEL_PROVENANCE_MISMATCH', 'local classify algorithm required')
        samples, provenance = [], []
        for m in ordered:
            require(isinstance(m, ColourMeasurement) and m.identity == identity,
                    'PARENT_MISMATCH', 'measurement differs from original vehicle identity')
            require(m.algorithm == algorithm and isinstance(m.quality_algorithm, str)
                    and bool(m.quality_algorithm.strip()) and isinstance(m.attempt_id, str)
                    and bool(m.attempt_id.strip()), 'MODEL_PROVENANCE_MISMATCH',
                    'explicit original attempt and consistent algorithm provenance required')
            receipt_in_lifetime(m.receipt, lifetime)
            score(m.confidence, nullable=False)
            score(m.quality, nullable=False)
            observation = make_colour_observation(m.label, m.confidence, m.quality)
            if observation.valid:
                samples.append((m, observation))
            provenance.append(dict(attempt_id=m.attempt_id, frame_index=m.receipt.frame_index,
                ts=m.receipt.utc.isoformat(), monotonic=m.receipt.monotonic, media_pts=m.receipt.media_pts,
                algorithm=m.algorithm.policy(), quality_algorithm=m.quality_algorithm,
                label=m.label, confidence=m.confidence, quality=m.quality))
        require(len(samples) <= 20, 'CAPACITY_EXCEEDED', 'at most 20 valid colour samples per lifetime')
        decision = decide_colour([o for _, o in samples], finalizing=True)
        decided = decision.label != 'unknown'
        support = samples[-1][0].receipt if decided else ordered[-1].receipt
        evidence = FinalColourEvidence(identity, lifetime.first_receipt, lifetime.last_receipt,
            lifetime.closure, lifetime.quarantined, 'decided' if decided else 'undecided',
            decision.label if decided else None, decision.confidence if decided else None,
            'local_hsv', support, len(ordered),
            tuple(ColourSampleEvidence(m.receipt, (), o.label, o.confidence, o.quality, o.weight)
                  for m, o in samples), 0, 0, None, None, None, None,
            decision.evidence.winner, decision.evidence.winner_share, algorithm, ordered[-1].receipt)
        return ColourResult(evidence, FrozenJSON.of(dict(attempts=provenance, reason=decision.reason)))
