"""Synthetic scalar fixtures for the first bounded architecture slice."""
import json
import subprocess
import sys
import unittest
from dataclasses import replace, FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID
from unittest.mock import patch

from vehicle.common.aggregator import Aggregator, Branch, ClassSample
from vehicle.common.colour_metadata import ColourMetadata, ColourMeasurement
from vehicle.common.db1_context import CameraRegistration, ModelRegistration, ResolutionInfo, RunContext
from vehicle.common.db1_rows import ContractValidation, UUID5Policy, RowBuilder, NaturalBuilder
from vehicle.common.db1_rows.core import Blocked
from vehicle.common.frame_receipt import FrameReceipt
from vehicle.common.observation_evidence import VehicleIdentity, RecognitionIdentity
from vehicle.common.observation_coordinator import CompletedVehicle
from vehicle.common.vehicle_frame import VehicleBoxObservation
from vehicle.common.vehicle_observation import VehicleLifetime, VehicleClosure

ROOT = Path(__file__).resolve().parents[3]


def fixture(count=5):
    """No SQL keys, crops, model initialization or persistence."""
    start = datetime(2026, 10, 8, 22, 59, 59, tzinfo=timezone.utc)
    org = UUID('12345678-1234-4234-8234-123456789012')
    camera = CameraRegistration(org, 'scalar-camera', None, 'Africa/Tunis', start, None)
    context = RunContext(org, camera, 'scalar-run', 'file', start, 'test-slice',
        ResolutionInfo(None, None, 'vehicle', ModelRegistration(None, 'detector-v1', 1, 'detect'),
                       None, 'unresolved scalar fixture', False), 'development')
    receipts = tuple(FrameReceipt(i, start+timedelta(seconds=i), 100+i, i*.04) for i in range(5))
    life = VehicleLifetime(context, 1, 0, 19, 1, receipts[0], receipts[-1], 5, VehicleClosure.END_OF_INPUT)
    identity = VehicleIdentity.from_lifetime(life)
    owner = VehicleBoxObservation(life, (), 'unknown', 0.0)
    model = RecognitionIdentity('hsv-config-v1', 1, 'classify')
    measurements = tuple(ColourMeasurement(identity, f'original-{i}', r, 'red', .8, .9, model,
                                           'quality-enhanced-v1') for i,r in enumerate(receipts[:count]))
    classes = tuple(ClassSample(identity, r, 'car', .8) for r in receipts)
    return owner, measurements, classes


class SliceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validation = ContractValidation(json.loads((ROOT/'contracts/db1/rows.v0.schema.json').read_text()),
            json.loads((ROOT/'contracts/db1/diagnostics.v1.schema.json').read_text()))
        cls.policy = UUID5Policy(UUID('98c2adf9-264e-4ac2-b6a1-3239fa02ecbb'))

    def setUp(self):
        self.owner, self.measurements, self.classes = fixture()
        self.colour = ColourMetadata().finalize(self.owner.lifetime, self.measurements)
        self.aggregator = Aggregator(identity_policy=self.policy, validation=self.validation)

    def join(self, colour=None, plate=None, prior=None):
        return self.aggregator.join(self.owner, self.classes, (
            Branch('colour', 'enabled', colour or self.colour),
            plate or Branch('plate', 'disabled'), Branch('manufacturer', 'unsupported')), prior=prior)

    def test_ready_parent_colour_and_truthful_omissions(self):
        result = self.join()
        self.assertEqual(result.state, 'ready')
        value = result.delivery.payload.value()
        self.assertEqual(value['omissions'], [dict(domain='manufacturer', reason='unsupported_capability'),
                                            dict(domain='plate', reason='disabled_capability')])
        self.assertEqual([f['row_kind'] for f in value['facts']], ['fact_vehicle','fact_vehicle_colour'])
        for fact in value['facts']:
            self.validation.delivery(fact['row_kind'], fact['delivery'])
            self.assertEqual(fact['delivery']['org_id'], str(self.owner.lifetime.context.org_id))
        parent, colour = [f['delivery'] for f in value['facts']]
        self.assertNotEqual(parent['ts'], colour['ts'])
        self.assertEqual(colour['colour'], 'red')
        self.assertEqual(colour['support']['ts'], '2026-10-08T23:00:03.000000Z')
        self.assertEqual(value['colour_provenance']['attempts'][0]['attempt_id'], 'original-0')
        self.assertEqual(value['colour_provenance']['attempts'][0]['algorithm']['model_name'], 'hsv-config-v1')
        with self.assertRaises(FrozenInstanceError):
            result.delivery.payload = None
        value['facts'].clear()
        self.assertEqual(len(result.delivery.payload.value()['facts']), 2)

    def test_enabled_unfinished_waits_without_delivery(self):
        result = self.join(plate=Branch('plate','enabled'))
        self.assertEqual((result.state, result.waiting, result.delivery), ('waiting', ('plate',), None))

    def test_insufficient_evidence_is_undecided_not_provider(self):
        colour = ColourMetadata().finalize(self.owner.lifetime, self.measurements[:2])
        fact = self.join(colour).delivery.payload.value()['facts'][1]['delivery']
        self.assertEqual(fact['state'], 'undecided')
        self.assertIsNone(fact['colour'])
        self.assertIsNone(fact['conf'])
        self.assertEqual(colour.evidence.agent_attempts, 0)

    def test_identity_and_provenance_blockers(self):
        for change, code in ((dict(identity=replace(self.measurements[0].identity, service_track_id=2)), 'PARENT_MISMATCH'),
            (dict(algorithm=RecognitionIdentity('different',1,'classify')), 'MODEL_PROVENANCE_MISMATCH'),
            (dict(quality_algorithm=''), 'MODEL_PROVENANCE_MISMATCH')):
            bad = (replace(self.measurements[0], **change),)+self.measurements[1:]
            with self.assertRaises(Blocked) as raised:
                ColourMetadata().finalize(self.owner.lifetime, bad)
            self.assertEqual(raised.exception.code, code)
        bad = replace(self.colour, evidence=replace(self.colour.evidence,
            identity=replace(self.colour.evidence.identity, service_track_id=2)))
        with self.assertRaises(Blocked):
            self.join(bad)

    def test_receipt_conflict_and_changed_payload_retry(self):
        prior = self.join().delivery
        changed = (replace(self.measurements[0], quality=.7),)+self.measurements[1:]
        colour = ColourMetadata().finalize(self.owner.lifetime, changed)
        with self.assertRaises(Blocked) as raised:
            self.join(colour, prior=prior)
        self.assertEqual(raised.exception.code, 'PAYLOAD_CONFLICT')
        bad = replace(self.colour, evidence=replace(self.colour.evidence,
            last_vehicle_receipt=replace(self.owner.lifetime.last_receipt, monotonic=999)))
        with self.assertRaises(Blocked) as raised:
            self.join(bad)
        self.assertEqual(raised.exception.code, 'FRAME_RECEIPT_MISMATCH')

    def test_canonical_order_equivalence_and_original_ids(self):
        original = self.join().delivery
        colour = ColourMetadata().finalize(self.owner.lifetime, tuple(reversed(self.measurements)))
        retry = self.join(colour, prior=original).delivery
        self.assertEqual(original.digest, retry.digest)
        self.assertEqual(original.payload.canonical, retry.payload.canonical)
        self.assertEqual(original.payload.value()['facts'][1]['delivery']['uid'],
                         self.policy.row_uid(self.colour.evidence.identity, 'fact_vehicle_colour'))

    def test_legacy_facade_same_natural_payload(self):
        result = self.join().delivery.payload.value()
        parent = result['facts'][0]['delivery']
        owner = replace(self.owner, vehicle_type=parent['class']['name'], type_confidence=float(parent['class_conf']))
        legacy = RowBuilder(identity_policy=self.policy, validation=self.validation).construct(
            CompletedVehicle(owner, (('colour',self.colour.evidence),)), None)
        for old, new in zip(legacy.facts, result['facts']):
            self.assertEqual(old.delivery.value(), new['delivery'])
            self.assertIsNone(old.sql)
            self.assertEqual(old.retry, 'blocked')  # Legacy still requires resolutions.

    def test_aggregator_keeps_colour_selection_and_owns_class_consensus(self):
        # Validation replays Colour's own canonical policy, never another policy
        # implemented in Aggregator. Domain replay must retain the same result.
        from vehicle.common.colour_metadata import decide_colour
        with patch('vehicle.common.colour_metadata.decide_colour', wraps=decide_colour) as policy:
            result = self.join().delivery.payload.value()
        policy.assert_called_once()
        self.assertEqual(result['facts'][1]['delivery']['colour'], self.colour.evidence.label)
        self.assertEqual(result['facts'][0]['delivery']['class']['name'], 'car')
        self.assertEqual(self.owner.vehicle_type, 'unknown')

    def test_terminal_provenance_mismatch_is_blocked(self):
        bad = replace(self.colour, evidence=replace(self.colour.evidence,
            executed_model=RecognitionIdentity('not-original', 1, 'classify')))
        with self.assertRaises(Blocked) as raised:
            self.join(bad)
        self.assertEqual(raised.exception.code, 'MODEL_PROVENANCE_MISMATCH')

    def test_zero_valid_samples_and_duplicate_attempts(self):
        measurements = tuple(replace(m, label='unknown', confidence=0) for m in self.measurements)
        colour = ColourMetadata().finalize(self.owner.lifetime, measurements)
        fact = self.join(colour).delivery.payload.value()['facts'][1]['delivery']
        self.assertEqual((fact['state'], fact['samples']), ('undecided', 0))
        with self.assertRaises(Blocked) as raised:
            ColourMetadata().finalize(self.owner.lifetime, self.measurements+(self.measurements[0],))
        self.assertEqual(raised.exception.code, 'DUPLICATE_ATTEMPT')

    def test_disabled_output_and_capacity_are_explicit_blockers(self):
        with self.assertRaises(Blocked) as raised:
            self.join(plate=Branch('plate', 'disabled', self.colour))
        self.assertEqual(raised.exception.code, 'CAPABILITY_RESULT_CONFLICT')
        with self.assertRaises(Blocked) as raised:
            ColourMetadata().finalize(self.owner.lifetime, self.measurements*26)
        self.assertEqual(raised.exception.code, 'CAPACITY_EXCEEDED')

    def test_final_colour_claims_must_match_canonical_policy(self):
        for change in (dict(label='blue'), dict(confidence=.4),
                       dict(state='undecided', label=None, confidence=None)):
            with self.subTest(change=change):
                forged = replace(self.colour, evidence=replace(self.colour.evidence, **change))
                with self.assertRaises(Blocked) as raised:
                    self.join(forged)
                self.assertEqual(raised.exception.code, 'COLOUR_DECISION_MISMATCH')
        insufficient = ColourMetadata().finalize(self.owner.lifetime, self.measurements[:2])
        forged = replace(insufficient, evidence=replace(insufficient.evidence,
            state='decided', label='red', confidence=1.0))
        with self.assertRaises(Blocked) as raised:
            self.join(forged)
        self.assertEqual(raised.exception.code, 'COLOUR_DECISION_MISMATCH')

    def test_invalid_original_attempt_receipt_conflicts_with_class_sample(self):
        # Frame 1 is rejected by Colour but still has an authoritative receipt.
        invalid = replace(self.measurements[1], label='unknown', confidence=0,
                          receipt=replace(self.measurements[1].receipt, monotonic=101.25))
        measurements = (self.measurements[0], invalid)+self.measurements[2:]
        colour = ColourMetadata().finalize(self.owner.lifetime, measurements)
        self.assertNotIn(invalid.receipt, tuple(s.receipt for s in colour.evidence.samples))
        with self.assertRaises(Blocked) as raised:
            self.join(colour)
        self.assertEqual(raised.exception.code, 'FRAME_RECEIPT_MISMATCH')

    def test_missing_accepted_colour_conflicts_instead_of_waiting(self):
        prior = self.join().delivery
        branches = (Branch('colour', 'enabled'), Branch('plate', 'disabled'),
                    Branch('manufacturer', 'unsupported'))
        first = self.aggregator.join(self.owner, self.classes, branches)
        self.assertEqual((first.state, first.waiting, first.delivery), ('waiting', ('colour',), None))
        with self.assertRaises(Blocked) as raised:
            self.aggregator.join(self.owner, self.classes, branches, prior=prior)
        self.assertEqual(raised.exception.code, 'PAYLOAD_CONFLICT')

    def test_accepted_terminal_bundle_cannot_regress_to_plate_wait(self):
        prior = self.join().delivery
        with self.assertRaises(Blocked) as raised:
            self.join(plate=Branch('plate', 'enabled'), prior=prior)
        self.assertEqual(raised.exception.code, 'PAYLOAD_CONFLICT')

    def test_natural_builder_orders_omissions_without_aggregator(self):
        builder = NaturalBuilder(identity_policy=self.policy, validation=self.validation)
        omissions = (('plate', 'disabled_capability'), ('manufacturer', 'unsupported_capability'))
        first = builder.construct(self.owner, self.colour, omissions=omissions)
        equivalent = builder.construct(self.owner, self.colour, omissions=tuple(reversed(omissions)))
        self.assertEqual(first.payload.canonical, equivalent.payload.canonical)
        self.assertEqual(first.digest, equivalent.digest)
        retry = builder.construct(self.owner, self.colour, omissions=tuple(reversed(omissions)), prior=first)
        self.assertEqual(first.digest, retry.digest)

    def test_pure_import_in_fresh_interpreter(self):
        code = '''
import sys
import vehicle.common.colour_metadata
import vehicle.common.aggregator
from vehicle.common.db1_rows.natural import NaturalBuilder
for prefix in ('cv2','numpy','torch','ultralytics','paddle','openai','sqlite3',
               'vehicle.common.observation_coordinator', 'vehicle.common.db1_rows.builder',
               'vehicle.common.db1_rows.records'):
    assert not any(n == prefix or n.startswith(prefix+'.') for n in sys.modules), prefix
'''
        subprocess.run([sys.executable, '-c', code], check=True, cwd=ROOT)


if __name__ == '__main__':
    unittest.main()
