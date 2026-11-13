"""Synthetic construction tests only: no persistence, models or transport."""
import json
import unittest
from dataclasses import replace, FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from hashlib import sha256
from pathlib import Path
from uuid import UUID
from unittest.mock import patch, Mock

from vehicle.common.db1_context import CameraRegistration, ModelRegistration, ResolutionInfo, RunContext
from vehicle.common.db1_rows import (RowBuilder, ContractValidation, UUID5Policy, Dimension,
                                    ResolvedModel, RunResolution, MediaResolution, Resolutions)
from vehicle.common.frame_receipt import FrameReceipt
from vehicle.common.observation_evidence import VehicleIdentity, OwnedCrop, RecognitionIdentity
from vehicle.common.observation_coordinator import CompletedVehicle
from vehicle.common.vehicle_observation import VehicleLifetime, VehicleClosure
from vehicle.common.vehicle_frame import VehicleBoxObservation
from vehicle.vehicle_color_service.vehicle_color_service.evidence import FinalColourEvidence, ColourSampleEvidence
from vehicle.license_plate_service.run_video_pipeline.shared_evidence import (
    RecognizerModel, Execution, PlateAttempt, SelectedPlate, FinalPlateEvidence)

ROOT = Path(__file__).resolve().parents[3]
UTC = timezone.utc
START = datetime(2026, 10, 8, 22, 59, 59, tzinfo=UTC)
ORG = UUID('12345678-1234-4234-8234-123456789012')
NAMESPACE = UUID('98c2adf9-264e-4ac2-b6a1-3239fa02ecbb')
LOCAL = RecognitionIdentity('synthetic-colour-config-sha', 2, 'classify')
AGENT = RecognitionIdentity('synthetic-colour-agent-prompt-sha', 7, 'vlm')
OCR = RecognizerModel('synthetic-ocr-bundle-sha', 3)


def fixture():
    camera = CameraRegistration(ORG, 'synthetic-test-camera', 41, 'Africa/Tunis', START - timedelta(days=1), None)
    detector = ModelRegistration(91, 'synthetic-detector-artifact-sha', 3, 'detect')
    context = RunContext(ORG, camera, 'synthetic-test-run', 'file', START, 'unit-test',
                         ResolutionInfo(51, 7, 'vehicle', detector, 101, 'synthetic unit test only', True), 'test')
    first = FrameReceipt(0, START, 100, 0)
    last = FrameReceipt(2, START + timedelta(seconds=2), 102, 0.08)
    life = VehicleLifetime(context, 1, 0, 19, 1, first, last, 3, VehicleClosure.END_OF_INPUT)
    parent = VehicleIdentity.from_lifetime(life)
    crop = OwnedCrop(bytes(range(12)), (2, 2, 3), '|u1', (0, 0, 2, 2), first)
    samples = (ColourSampleEvidence(first, crop.bbox, 'silver/gray', .8, .9, 1),
               ColourSampleEvidence(last, crop.bbox, 'silver/gray', .9, .9, 1))
    colour = FinalColourEvidence(parent, first, last, life.closure, False, 'decided', 'silver/gray', .9,
              'local_hsv', last, 3, samples, 0, 0, None, crop, None, None, 'silver/gray', 1, LOCAL, last)
    result = dict(status='accepted', route='private_local', source='local_ocr', plate_text_normalized='123 TUN 4567',
                  plate_category='unknown', text_confidence=.9, evidence={'local': {'parser_status': 'valid'}})
    attempt = PlateAttempt('synthetic-test-run:plate:17', 17, parent, 8, 1, crop, None, 'exit', 'completed',
                           (Execution('private_local', OCR),), json.dumps(result).encode(), None, 111)
    selected = SelectedPlate(attempt.attempt_id, 8, crop, None, '123 TUN 4567', 'private_local', OCR)
    plate = FinalPlateEvidence(parent, first, last, life.closure, False, 'read', None, (attempt,), selected, 1, 1, 0, 0, False)
    completed = CompletedVehicle(VehicleBoxObservation(life, (0, 0, 2, 2), 'car', .8), (('colour', colour), ('plate', plate)))
    clip = MediaResolution(101, ORG, None, 41, context.run_id, camera.camera_id, START - timedelta(days=10),
                           'clip', 'synthetic-tests', 'clips/input.mp4', 'video/mp4', 500, 2, 2, 'a'*64, None, None, None)
    run = RunResolution(ORG, context.run_id, 51, camera, 7, 'vehicle', detector, 'file', 101, START,
                        'unit-test', (20261008, 20261009), 'synthetic unit test only', True)
    resolutions = Resolutions(run, (Dimension('car', 11, True, 'vehicle'),),
                              (Dimension('silver/gray', 21, True), Dimension('brown/beige', 22, True)), (),
                              (ResolvedModel(LOCAL, 81), ResolvedModel(AGENT, 82),
                               ResolvedModel(RecognitionIdentity(OCR.model_name, OCR.version, 'ocr'), 83)), (clip,))
    # Plate resolution permits OCR as well; immutable recognition identity uses
    # explicit task, never borrows detector/colour registrations.
    return completed, resolutions


class BuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validation = ContractValidation(json.loads((ROOT/'contracts/db1/rows.v0.schema.json').read_text()),
                                            json.loads((ROOT/'contracts/db1/diagnostics.v1.schema.json').read_text()))

    def setUp(self):
        self.builder = RowBuilder(identity_policy=UUID5Policy(NAMESPACE), validation=self.validation)
        self.completed, self.res = fixture()

    def result(self, completed=None, resolutions='default', prior=None):
        return self.builder.construct(completed or self.completed, self.res if resolutions == 'default' else resolutions, prior=prior)

    def fact(self, result, kind):
        return next(f for f in result.facts if f.row_kind == kind)

    def evidence(self, name):
        return dict(self.completed.consumer_evidence)[name]

    def with_evidence(self, name, evidence):
        return replace(self.completed, consumer_evidence=tuple((n, evidence if n == name else v) for n,v in self.completed.consumer_evidence))

    def assertBlocked(self, result, kind, code):
        fact = self.fact(result, kind)
        self.assertFalse(fact.ready, fact.issues)
        self.assertTrue(any(code in issue for issue in fact.issues), fact.issues)
        self.validation.diagnostic(fact.diagnostic.value())
        return fact

    def test_resolved_supported_facts_exact_columns_and_formats(self):
        result = self.result()
        self.assertEqual([f.row_kind for f in result.ready_rows], ['fact_vehicle','fact_vehicle_colour','fact_plate_read'])
        for f in result.ready_rows:
            self.validation._row.validate(f.snapshot())
            self.validation.diagnostic(f.diagnostic.value())
        vehicle = self.fact(result,'fact_vehicle').sql.value()
        self.assertEqual(set(vehicle), {'org_id','run_key','camera_key','date_key','ts','uid','track_id',
                                        'first_ts','last_ts','class_key','class_conf','frames_observed','best_media_key'})
        self.assertIsNone(vehicle['best_media_key'])

    def test_own_dates_across_local_midnight_and_pts_separate(self):
        result = self.result()
        self.assertEqual(self.fact(result,'fact_vehicle').sql.value()['date_key'],20261008)
        self.assertEqual(self.fact(result,'fact_vehicle_colour').sql.value()['date_key'],20261009)
        self.assertEqual(self.fact(result,'fact_plate_read').sql.value()['date_key'],20261008)
        diag = self.fact(result,'fact_vehicle_colour').diagnostic.value()
        self.assertEqual(diag['time_basis'],'host_replay_receipt')
        self.assertEqual(diag['media_pts'],Decimal('.08'))
        self.assertTrue(self.fact(result,'fact_vehicle_colour').delivery.value()['ts'].startswith('2026-10-08T23:00:01'))

    def test_unresolved_development_and_missing_resolution(self):
        for res in (None, self.res):
            if res is None:
                result = self.result(resolutions=None)
                self.assertBlocked(result,'fact_vehicle','MISSING_RESOLUTION')
            else:
                ctx = self.completed.vehicle.lifetime.context
                ctx = replace(ctx, environment='development', camera=replace(ctx.camera,camera_key=None),
                              resolution=replace(ctx.resolution,run_key=None,lane_key=None,source_media_key=None,
                              detector=replace(ctx.resolution.detector,model_key=None),synthetic=False))
                life = replace(self.completed.vehicle.lifetime,context=ctx)
                completed = replace(self.completed,vehicle=replace(self.completed.vehicle,lifetime=life),consumer_evidence=())
                result = self.result(completed, replace(res,run=replace(res.run,synthetic=False)))
                self.assertBlocked(result,'fact_vehicle','UNRESOLVED_DEVELOPMENT')
            self.assertEqual(result.ready_rows,())
            self.assertIsNotNone(self.fact(result,'fact_vehicle').delivery)

    def test_synthetic_resolution_not_allowed_in_production(self):
        ctx = self.completed.vehicle.lifetime.context
        ctx = replace(ctx,environment='production',resolution=replace(ctx.resolution,synthetic=False))
        life=replace(self.completed.vehicle.lifetime,context=ctx)
        completed=replace(self.completed,vehicle=replace(self.completed.vehicle,lifetime=life),consumer_evidence=())
        self.assertBlocked(self.result(completed),'fact_vehicle','SYNTHETIC_RESOLUTION')

    def test_run_and_camera_and_detector_resolution_mismatch(self):
        for run in (replace(self.res.run,run_id='other'),replace(self.res.run,run_key=999),
                    replace(self.res.run,camera=replace(self.res.run.camera,camera_key=42)),
                    replace(self.res.run,camera=replace(self.res.run.camera,timezone='UTC')),
                    replace(self.res.run,detector=replace(self.res.run.detector,model_key=999)),
                    replace(self.res.run,source_kind='stream'), replace(self.res.run,service_version='other')):
            with self.subTest(run=run):
                self.assertBlocked(self.result(resolutions=replace(self.res,run=run)), 'fact_vehicle','RUN_RESOLUTION_MISMATCH')

    def test_organization_resolution_mismatch(self):
        other=UUID(int=99)
        run=replace(self.res.run,org_id=other,camera=replace(self.res.run.camera,org_id=other))
        self.assertBlocked(self.result(resolutions=replace(self.res,run=run)),'fact_vehicle','RUN_RESOLUTION_MISMATCH')

    def test_consumer_parent_run_and_raw_lifetime_mismatch(self):
        colour=self.evidence('colour')
        for identity in (replace(colour.identity,service_track_id=2),replace(colour.identity,tracker_epoch=1),
                         replace(colour.identity,raw_track_id=55),replace(colour.identity,lifetime_sequence=2),
                         replace(colour.identity,context=replace(colour.identity.context,run_id='other'))):
            self.assertBlocked(self.result(self.with_evidence('colour',replace(colour,identity=identity))),
                               'fact_vehicle_colour','PARENT_MISMATCH')

    def test_missing_model_blocks_colour_and_plate(self):
        result=self.result(resolutions=replace(self.res,models=()))
        self.assertBlocked(result,'fact_vehicle_colour','MODEL_RESOLUTION_MISMATCH')
        self.assertBlocked(result,'fact_plate_read','MODEL_RESOLUTION_MISMATCH')
        self.assertTrue(self.fact(result,'fact_vehicle').ready)

    def test_parent_unavailable_blocks_recognition_projection(self):
        result=self.result(resolutions=replace(self.res,classes=()))
        self.assertBlocked(result,'fact_vehicle','MISSING_DIMENSION')
        self.assertBlocked(result,'fact_vehicle_colour','PARENT_NOT_READY')
        self.assertBlocked(result,'fact_plate_read','PARENT_NOT_READY')

    def test_date_registration_missing(self):
        res=replace(self.res,run=replace(self.res.run,date_keys=(20261008,)))
        result=self.result(resolutions=res)
        self.assertBlocked(result,'fact_vehicle_colour','MISSING_DATE')
        self.assertTrue(self.fact(result,'fact_plate_read').ready)

    def test_classification_aggregation_authority_preserved(self):
        completed=replace(self.completed,vehicle=replace(self.completed.vehicle,vehicle_type='van',type_confidence=.42))
        res=replace(self.res,classes=(Dimension('van',12,True,'vehicle'),))
        fact=self.fact(self.result(completed,res),'fact_vehicle')
        self.assertEqual(fact.delivery.value()['class']['name'],'van')
        self.assertEqual(fact.sql.value()['class_conf'],Decimal('.42'))

    def test_grouped_colours_preserved_and_missing_alias_rejected(self):
        for label in ('silver/gray','brown/beige'):
            colour=replace(self.evidence('colour'),label=label)
            fact=self.fact(self.result(self.with_evidence('colour',colour)),'fact_vehicle_colour')
            self.assertTrue(fact.ready,fact.issues)
            self.assertEqual(fact.delivery.value()['colour'],label)
        res=replace(self.res,colours=(Dimension('grey',21,True),))
        self.assertBlocked(self.result(resolutions=res),'fact_vehicle_colour','MISSING_DIMENSION')

    def test_plate_unresolved_category_not_guessed(self):
        plate=self.evidence('plate');a=plate.ledger[0]
        result=json.loads(a.result_json);result['plate_category']='private'
        plate=replace(plate,ledger=(replace(a,result_json=json.dumps(result).encode()),))
        fact=self.assertBlocked(self.result(self.with_evidence('plate',plate)),'fact_plate_read','MISSING_DIMENSION')
        self.assertEqual(fact.delivery.value()['selected_attempt']['plate_category'],'private')
        res=replace(self.res,plate_categories=(Dimension('private',9,True),))
        self.assertTrue(self.fact(self.result(self.with_evidence('plate',plate),res),'fact_plate_read').ready)

    def test_selected_plate_attempt_crop_score_and_segment_mismatch(self):
        plate=self.evidence('plate');s=plate.selected
        for selected in (replace(s,attempt_id='absent'),replace(s,segment_id=9),replace(s,crop_score=1),
                         replace(s,crop=replace(s.crop,pixels=b'x'*12)),replace(s,text='OTHER'),
                         replace(s,model=RecognizerModel('other-model',3))):
            fact=self.fact(self.result(self.with_evidence('plate',replace(plate,selected=selected))),'fact_plate_read')
            self.assertFalse(fact.ready,fact.issues)

    def test_attempt_parent_cannot_borrow_ownership_from_counts(self):
        plate=self.evidence('plate');a=plate.ledger[0]
        plate=replace(plate,ledger=(replace(a,parent=replace(a.parent,service_track_id=2)),),detections=1000)
        self.assertBlocked(self.result(self.with_evidence('plate',plate)),'fact_plate_read','PARENT_MISMATCH')

    def test_unreadable_null_quality_and_measured_selection(self):
        plate=self.evidence('plate');a=replace(plate.ledger[0],result_json=None,status='failed',error='ocr_failed')
        selected=replace(plate.selected,text=None)
        plate=replace(plate,outcome='unreadable',ledger=(a,),selected=selected)
        fact=self.fact(self.result(self.with_evidence('plate',plate)),'fact_plate_read')
        self.assertTrue(fact.ready,fact.issues)
        for field in ('plate_text','plate_category_key','conf','crop_score','crop_media_key'):
            self.assertIsNone(fact.sql.value()[field])
        measured=replace(a,attempt_id='measured',submission_ordinal=20,crop_score=.1)
        plate=replace(plate,ledger=(a,measured))
        self.assertBlocked(self.result(self.with_evidence('plate',plate)),'fact_plate_read','UNREADABLE_SELECTION_MISMATCH')
        plate=replace(plate,selected=replace(selected,attempt_id='measured',crop_score=.1))
        fact=self.fact(self.result(self.with_evidence('plate',plate)),'fact_plate_read')
        self.assertTrue(fact.ready,fact.issues)
        self.assertEqual(fact.delivery.value()['selected_attempt']['submission_ordinal'],2)

    def test_zero_operations_distinct_omissions(self):
        for reason,counts in (('no_detection',(0,0,0,0)),('unassociated_crop',(1,0,0,1)),
                              ('ambiguous_association',(1,0,1,0)),('failure_before_model',(1,1,0,0)),
                              ('rejected_crop',(1,0,0,0))):
            p=replace(self.evidence('plate'),outcome='omitted',selected=None,ledger=(),omission_reason=reason,
                      detections=counts[0],admitted_crops=counts[1],ambiguous_crops=counts[2],unassociated_crops=counts[3])
            fact=self.fact(self.result(self.with_evidence('plate',p)),'fact_plate_read')
            self.assertEqual(fact.diagnostic.value()['reason'],reason,fact.issues)
            self.assertIsNone(fact.delivery)

    def test_submission_failure_and_cancelled_are_not_execution(self):
        for status in ('submission_failed','cancelled'):
            p=self.evidence('plate');a=replace(p.ledger[0],status=status,executions=(),result_json=None)
            p=replace(p,ledger=(a,),outcome='omitted',selected=None,omission_reason='failure_before_model')
            fact=self.fact(self.result(self.with_evidence('plate',p)),'fact_plate_read')
            self.assertEqual(fact.diagnostic.value()['reason'],'failure_before_model')
            p=replace(p,ledger=(replace(a,executions=(Execution('private_local',OCR),)),))
            self.assertBlocked(self.result(self.with_evidence('plate',p)),'fact_plate_read','FABRICATED_EXECUTION')

    def test_unsettled_and_worker_unknown_do_not_emit_plate_rows(self):
        for status in ('submitted','settlement_timeout','failed'):
            p=self.evidence('plate');a=replace(p.ledger[0],status=status,execution_unknown=True)
            p=replace(p,ledger=(a,),selected=None,outcome='omitted',omission_reason='unsettled_operation',settlement_incomplete=True)
            fact=self.fact(self.result(self.with_evidence('plate',p)),'fact_plate_read')
            self.assertEqual(fact.diagnostic.value()['reason'],'unsettled_operation',fact.issues)
            self.assertEqual(fact.diagnostic.value()['settlement']['unresolved_attempts'],1)
            self.assertIsNone(fact.sql)

    def test_inconsistent_settlement_rejected(self):
        p=replace(self.evidence('plate'),settlement_incomplete=True)
        self.assertBlocked(self.result(self.with_evidence('plate',p)),'fact_plate_read','SETTLEMENT_MISMATCH')

    def test_quarantine_all_facts(self):
        life=replace(self.completed.vehicle.lifetime,closure=VehicleClosure.CLOCK_DISCONTINUITY,quarantined=True)
        c=replace(self.completed,vehicle=replace(self.completed.vehicle,lifetime=life))
        result=self.result(c)
        self.assertEqual(result.ready_rows,())
        for f in result.facts:
            self.assertEqual(f.diagnostic.value()['reason'],'clock_discontinuity')

    def test_incomplete_owner_and_consumer_closure_mismatch(self):
        c=replace(self.completed,vehicle=replace(self.completed.vehicle,lifetime=replace(self.completed.vehicle.lifetime,closure=None)))
        self.assertBlocked(self.result(c),'fact_vehicle','INCOMPLETE_LIFETIME')
        colour=replace(self.evidence('colour'),closure=VehicleClosure.SERVICE_STOP)
        self.assertBlocked(self.result(self.with_evidence('colour',colour)),'fact_vehicle_colour','PARENT_MISMATCH')

    def test_colour_support_and_missing_attribution(self):
        colour=self.evidence('colour')
        self.assertBlocked(self.result(self.with_evidence('colour',replace(colour,support_receipt=colour.first_vehicle_receipt))),
                           'fact_vehicle_colour','COLOUR_SUPPORT_MISMATCH')
        self.assertBlocked(self.result(self.with_evidence('colour',replace(colour,executed_model=None))),
                           'fact_vehicle_colour','MISSING_MODEL_EVIDENCE')

    def test_colour_undecided_error_support_last_attempt_and_explicit_nulls(self):
        for state in ('undecided','error'):
            colour=replace(self.evidence('colour'),state=state,label=None,confidence=None,samples=(),local_errors=1)
            f=self.fact(self.result(self.with_evidence('colour',colour)),'fact_vehicle_colour')
            self.assertTrue(f.ready,f.issues)
            row=f.sql.value()
            self.assertIsNone(row['colour_key']);self.assertIsNone(row['conf'])
            self.assertEqual(row['samples'],0)
            self.assertEqual(f.delivery.value()['support']['ts'],row['ts'])

    def test_colour_agent_supports_actual_submitted_crop(self):
        colour=self.evidence('colour')
        colour=replace(colour,source='agent_override',executed_model=AGENT,agent_attempts=1,
                       agent_crop=colour.best_crop,support_receipt=colour.best_crop.receipt)
        f=self.fact(self.result(self.with_evidence('colour',colour)),'fact_vehicle_colour')
        self.assertTrue(f.ready,f.issues)
        self.assertEqual(f.sql.value()['date_key'],20261008)
        self.assertEqual(f.sql.value()['model_key'],82)
        colour=replace(colour,support_receipt=colour.last_vehicle_receipt)
        self.assertBlocked(self.result(self.with_evidence('colour',colour)),'fact_vehicle_colour','COLOUR_SUPPORT_MISMATCH')

    def test_failed_colour_agent_preserves_local_evidence(self):
        colour=replace(self.evidence('colour'),agent_attempts=1,agent_error='agent_failure',agent_crop=self.evidence('colour').best_crop)
        f=self.fact(self.result(self.with_evidence('colour',colour)),'fact_vehicle_colour')
        self.assertTrue(f.ready,f.issues)
        self.assertEqual(f.sql.value()['model_key'],81)
        self.assertEqual(f.sql.value()['date_key'],20261009)

    def test_diagnostic_epoch_and_attempt_id_mapping(self):
        p=self.evidence('plate')
        f=self.fact(self.result(),'fact_plate_read')
        UUID(f.delivery.value()['selected_attempt']['attempt_uid'])
        self.assertEqual(f.delivery.value()['selected_attempt']['submission_ordinal'],1)
        self.assertEqual(f.metadata.value()['native_submission_ordinal'],17)
        self.assertEqual(f.metadata.value()['native_segment_id'],8)
        self.assertEqual(f.metadata.value()['native_attempt_id'],p.ledger[0].attempt_id)
        self.assertEqual(f.diagnostic.value()['raw_tracker']['epoch'],1)
        policy=self.builder.identity_policy
        self.assertNotEqual(policy.attempt_uid(p.identity,8,p.selected.attempt_id),policy.attempt_uid(p.identity,9,p.selected.attempt_id))

    def test_identity_retry_and_payload_conflict(self):
        first=self.result();second=self.result(prior=first)
        for f in second.ready_rows:
            self.assertEqual(f.retry,'identical')
        c=replace(self.completed,vehicle=replace(self.completed.vehicle,type_confidence=.7))
        third=self.result(c,prior=first)
        v=self.fact(third,'fact_vehicle')
        self.assertEqual(v.delivery.value()['uid'],self.fact(first,'fact_vehicle').delivery.value()['uid'])
        self.assertEqual(v.retry,'conflict')
        self.assertEqual(v.diagnostic.value()['error_code'],'PAYLOAD_CONFLICT')
        self.assertEqual(third.ready_rows,())

    def test_changed_sql_resolution_is_conflict_not_new_uid(self):
        first=self.result()
        res=replace(self.res,colours=(Dimension('silver/gray',31,True),))
        result=self.result(resolutions=res,prior=first)
        f=self.fact(result,'fact_vehicle_colour')
        self.assertEqual(f.retry,'conflict')
        self.assertEqual(f.delivery.digest,self.fact(first,'fact_vehicle_colour').delivery.digest)
        self.assertNotEqual(f.sql.digest,self.fact(first,'fact_vehicle_colour').sql.digest)

    def test_readiness_from_previously_unresolved_natural_payload(self):
        first=self.result(resolutions=None)
        second=self.result(prior=first)
        self.assertEqual([f.retry for f in second.ready_rows],['identical']*3)

    def media(self):
        crop=self.evidence('plate').selected.crop
        return MediaResolution(201,ORG,51,41,self.res.run.run_id,self.res.run.camera.camera_id,crop.receipt.utc,
                               'plate_crop','synthetic-tests','crops/selected.jpg','image/jpeg',200,2,2,'b'*64,
                               1,crop.receipt.frame_index,sha256(crop.pixels).hexdigest())

    def test_registered_media_and_missing_media_null(self):
        res=replace(self.res,media=self.res.media+(self.media(),))
        f=self.fact(self.result(resolutions=res),'fact_plate_read')
        self.assertTrue(f.ready,f.issues)
        self.assertEqual(f.sql.value()['crop_media_key'],201)
        self.assertEqual(f.delivery.value()['selected_attempt']['crop_media']['sha256'],'b'*64)
        self.assertEqual(f.diagnostic.value()['media_status'],'stored')
        missing=self.fact(self.result(),'fact_plate_read')
        self.assertIsNone(missing.sql.value()['crop_media_key'])
        self.assertEqual(missing.diagnostic.value()['reason'],'media_unavailable')

    def test_media_owner_receipt_and_crop_fingerprint_mismatch(self):
        media=self.media()
        for m in (replace(media,org_id=UUID(int=7)),replace(media,run_key=999),replace(media,camera_key=999),
                  replace(media,run_id='other'),replace(media,ts=media.ts+timedelta(seconds=1)),
                  replace(media,crop_sha256='c'*64),replace(media,width=3),replace(media,frame_index=1)):
            res=replace(self.res,media=self.res.media+(m,))
            self.assertBlocked(self.result(resolutions=res),'fact_plate_read','MEDIA_MISMATCH')

    def test_source_media_missing_and_owner_mismatch(self):
        self.assertBlocked(self.result(resolutions=replace(self.res,media=())),'fact_vehicle','MISSING_SOURCE_MEDIA')
        clip=replace(self.res.media[0],org_id=UUID(int=7))
        self.assertBlocked(self.result(resolutions=replace(self.res,media=(clip,))),'fact_vehicle','SOURCE_MEDIA_MISMATCH')

    def test_media_path_not_registration_and_no_classification_crop(self):
        with self.assertRaises(ValueError):replace(self.media(),object_key='/tmp/crop.jpg')
        with self.assertRaises(ValueError):replace(self.media(),object_key='https://example/crop.jpg')
        media=replace(self.media(),kind='vehicle_crop')
        self.assertBlocked(self.result(resolutions=replace(self.res,media=self.res.media+(media,))),'fact_vehicle','UNSUPPORTED_VEHICLE_MEDIA')

    def test_unsupported_manufacturer_is_not_structural_example(self):
        f=self.fact(self.result(),'fact_vehicle_make')
        self.assertEqual(f.retry,'omitted')
        self.assertIsNone(f.sql);self.assertIsNone(f.delivery)
        self.assertEqual(f.diagnostic.value()['reason'],'unsupported_capability')

    def test_immutability_no_consumption_and_no_io_in_construction(self):
        before=self.completed
        with patch('builtins.open',side_effect=AssertionError('I/O forbidden')):
            result=self.result()
        self.assertIs(self.completed,before)
        f=self.fact(result,'fact_vehicle')
        with self.assertRaises(FrozenInstanceError):f.retry='changed'
        mutated=f.delivery.value();mutated['class']['name']='changed'
        self.assertEqual(f.delivery.value()['class']['name'],'car')
        mutated=f.snapshot();mutated['resolved_sql_row']['track_id']=9
        self.assertEqual(f.sql.value()['track_id'],1)

    def test_mutable_completed_evidence_rejected(self):
        completed=replace(self.completed,consumer_evidence=list(self.completed.consumer_evidence))
        self.assertBlocked(self.result(completed),'fact_vehicle','MUTABLE_EVIDENCE')

    def test_exact_decimal_precision_and_nonfinite_rejected(self):
        with localcontext() as ctx:
            ctx.prec=3
            completed=replace(self.completed,vehicle=replace(self.completed.vehicle,type_confidence=Decimal('0.12345678901234567890123456781')))
            f=self.fact(self.result(completed),'fact_vehicle')
        self.assertEqual(f.delivery.value()['class_conf'],Decimal('0.12345678901234567890123456781'))
        c=replace(self.completed,vehicle=replace(self.completed.vehicle,type_confidence=float('nan')))
        self.assertBlocked(self.result(c),'fact_vehicle','INVALID_SCORE')

    def test_invalid_timestamp_precision_and_external_schema_refs_rejected(self):
        d=self.fact(self.result(),'fact_vehicle').delivery.value()
        d['ts']='2026-10-08T22:59:59.1234567Z'
        with self.assertRaises(Exception):self.validation.delivery('fact_vehicle',d)
        with self.assertRaises(ValueError):ContractValidation({'$ref':'https://example.invalid/schema'}, {})

    def test_plate_result_duplicate_json_keys_rejected(self):
        p=self.evidence('plate');a=replace(p.ledger[0],result_json=b'{"status":"accepted","status":"rejected"}')
        self.assertBlocked(self.result(self.with_evidence('plate',replace(p,ledger=(a,)))),
                           'fact_plate_read','INVALID_EVIDENCE_OR_SCHEMA')

    def test_agent_plate_admission_and_executed_vlm_task(self):
        p=self.evidence('plate');a=p.ledger[0]
        agent=RecognizerModel('synthetic-agent-prompt',4,'vlm')
        result=json.loads(a.result_json)
        result.update(source='agent',route='agent',evidence={'text_complete':True})
        a=replace(a,executions=(Execution('agent',agent),),result_json=json.dumps(result).encode())
        p=replace(p,ledger=(a,),selected=replace(p.selected,route='agent',model=agent))
        res=replace(self.res,models=self.res.models+(ResolvedModel(RecognitionIdentity(agent.model_name,4,'vlm'),84),))
        fact=self.fact(self.result(self.with_evidence('plate',p),res),'fact_plate_read')
        self.assertTrue(fact.ready,fact.issues)
        self.assertIsNone(fact.delivery.value()['selected_attempt']['structural_valid'])
        result['evidence']['text_complete']='true'
        p=replace(p,ledger=(replace(a,result_json=json.dumps(result).encode()),))
        self.assertBlocked(self.result(self.with_evidence('plate',p),res),'fact_plate_read','PLATE_READ_ADMISSION')

    def test_legacy_agent_ocr_attribution_blocked_without_guessing(self):
        p=self.evidence('plate');a=p.ledger[0]
        legacy=RecognizerModel('synthetic-agent-with-incorrect-task',4)
        p=replace(p,ledger=(replace(a,executions=(Execution('agent',legacy),)),),
                  selected=replace(p.selected,route='agent',model=legacy))
        self.assertBlocked(self.result(self.with_evidence('plate',p)),'fact_plate_read','PLATE_MODEL_ROUTE_MISMATCH')

    def test_unreadable_cannot_hide_an_admitted_read(self):
        p=self.evidence('plate')
        p=replace(p,outcome='unreadable',selected=replace(p.selected,text=None))
        self.assertBlocked(self.result(self.with_evidence('plate',p)),'fact_plate_read','UNREADABLE_READ_CONFLICT')

    def test_local_private_read_requires_structural_admission(self):
        p=self.evidence('plate');a=p.ledger[0];result=json.loads(a.result_json)
        result['evidence']['local']['parser_status']='invalid'
        p=replace(p,ledger=(replace(a,result_json=json.dumps(result).encode()),))
        self.assertBlocked(self.result(self.with_evidence('plate',p)),'fact_plate_read','PLATE_READ_ADMISSION')

    def test_invalid_quality_and_inconsistent_owned_bytes(self):
        p=self.evidence('plate');a=p.ledger[0]
        for bad in (-.1,float('inf'),3.5e38):
            changed=replace(p,ledger=(replace(a,crop_score=bad),),selected=replace(p.selected,crop_score=bad))
            self.assertBlocked(self.result(self.with_evidence('plate',changed)),'fact_plate_read','INVALID_SCORE')
        changed=replace(p,ledger=(replace(a,crop=replace(a.crop,pixels=b'x')),))
        self.assertBlocked(self.result(self.with_evidence('plate',changed)),'fact_plate_read','INVALID_CROP')

    def test_registration_integer_ranges_duplicates_and_model_collision(self):
        with self.assertRaises(ValueError):Dimension('car',32768,True)
        with self.assertRaises(ValueError):replace(self.res.run,lane_key=32768)
        with self.assertRaises(ValueError):replace(self.res.run,run_key=True)
        with self.assertRaises(ValueError):replace(self.res.run,date_keys=(20260230,))
        with self.assertRaises(ValueError):replace(self.res,models=(ResolvedModel(LOCAL,91),))
        with self.assertRaises(ValueError):replace(self.res,classes=self.res.classes*2)
        with self.assertRaises(ValueError):replace(self.res,models=list(self.res.models))
        with self.assertRaises(ValueError):replace(self.media(),bytes=-1)

    def test_detector_key_does_not_resolve_recognition_identity(self):
        models=tuple(replace(m,identity=RecognitionIdentity('wrong-colour-identity',2,'classify')) if m.key == 81 else m for m in self.res.models)
        self.assertBlocked(self.result(resolutions=replace(self.res,models=models)),
                           'fact_vehicle_colour','MODEL_RESOLUTION_MISMATCH')

    def test_camera_version_crossing_blocks_owner(self):
        life=self.completed.vehicle.lifetime;ctx=life.context
        camera=replace(ctx.camera,valid_to=life.last_receipt.utc)
        ctx=replace(ctx,camera=camera)
        c=replace(self.completed,vehicle=replace(self.completed.vehicle,lifetime=replace(life,context=ctx)),consumer_evidence=())
        res=replace(self.res,run=replace(self.res.run,camera=camera))
        self.assertBlocked(self.result(c,res),'fact_vehicle','CAMERA_VERSION_MISMATCH')

    def test_row_policy_change_cannot_silently_allocate_new_identity_on_retry(self):
        first=self.result()
        other=RowBuilder(identity_policy=UUID5Policy(UUID(int=17)),validation=self.validation)
        result=other.construct(self.completed,self.res,prior=first)
        self.assertEqual(self.fact(result,'fact_vehicle').retry,'conflict')
        self.assertEqual(result.ready_rows,())

    def test_raw_reuse_new_service_lifetime_has_distinct_row_identity(self):
        life=self.completed.vehicle.lifetime
        c=replace(self.completed,vehicle=replace(self.completed.vehicle,lifetime=replace(life,service_track_id=2,lifetime_sequence=2)),consumer_evidence=())
        first=self.fact(self.result(),'fact_vehicle')
        second=self.fact(self.result(c),'fact_vehicle')
        self.assertNotEqual(first.delivery.value()['uid'],second.delivery.value()['uid'])
        self.assertEqual(second.diagnostic.value()['raw_tracker'],first.diagnostic.value()['raw_tracker'])

    def test_omitted_operation_preserves_native_mapping(self):
        p=self.evidence('plate');a=replace(p.ledger[0],executions=(),result_json=None,status='submission_failed')
        p=replace(p,outcome='omitted',selected=None,ledger=(a,),omission_reason='failure_before_model')
        f=self.fact(self.result(self.with_evidence('plate',p)),'fact_plate_read')
        m=f.metadata.value()['attempt_mappings'][0]
        self.assertEqual(m['native_submission_ordinal'],17)
        self.assertEqual(m['native_segment_id'],8)
        self.assertEqual(m['mapped_submission_ordinal'],1)
        UUID(m['attempt_uid'])

    def test_native_negative_pts_preserved_only_in_metadata(self):
        life=self.completed.vehicle.lifetime
        first=replace(life.first_receipt,media_pts=-.1)
        c=replace(self.completed,vehicle=replace(self.completed.vehicle,lifetime=replace(life,first_receipt=first)),consumer_evidence=())
        f=self.fact(self.result(c),'fact_vehicle')
        self.assertTrue(f.ready,f.issues)
        self.assertIsNone(f.diagnostic.value()['media_pts'])
        self.assertEqual(f.metadata.value()['native_media_pts'],Decimal('-.1'))

    def test_missing_colour_capability_and_zero_attempt_omission(self):
        for reason in ('unsupported_capability','disabled_capability','rejected_crop'):
            colour=replace(self.evidence('colour'),state='omitted',omission_reason=reason,label=None,confidence=None,
                           source=None,support_receipt=None,samples=(),attempts=0,executed_model=None)
            f=self.fact(self.result(self.with_evidence('colour',colour)),'fact_vehicle_colour')
            self.assertEqual(f.diagnostic.value()['reason'],reason,f.issues)
            self.assertIsNone(f.sql)

    def test_person_is_not_a_vehicle_fact_even_with_a_supplied_class(self):
        c=replace(self.completed,vehicle=replace(self.completed.vehicle,vehicle_type='person'))
        res=replace(self.res,classes=(Dimension('person',12,True,'vehicle'),))
        self.assertBlocked(self.result(c,res),'fact_vehicle','UNSUPPORTED_CLASS')

    def test_incomplete_omission_has_explicit_construction_diagnostic(self):
        colour=replace(self.evidence('colour'),state='omitted',omission_reason=None,label=None,confidence=None,
                       source=None,support_receipt=None,samples=(),attempts=0,executed_model=None)
        self.assertBlocked(self.result(self.with_evidence('colour',colour)),
                           'fact_vehicle_colour','INVALID_OMISSION')

    def test_duplicate_consumer_and_frame_count_inconsistency(self):
        c=replace(self.completed,consumer_evidence=self.completed.consumer_evidence*2)
        self.assertBlocked(self.result(c),'fact_vehicle','DUPLICATE_CONSUMER')
        life=replace(self.completed.vehicle.lifetime,frames_observed=100)
        c=replace(self.completed,vehicle=replace(self.completed.vehicle,lifetime=life))
        self.assertBlocked(self.result(c),'fact_vehicle','FRAME_COUNT_MISMATCH')

    def test_nonzero_real_underflow_rejected_and_explicit_zero_preserved(self):
        c=replace(self.completed,vehicle=replace(self.completed.vehicle,type_confidence=Decimal('1e-1000')))
        self.assertBlocked(self.result(c),'fact_vehicle','INVALID_SCORE')
        c=replace(self.completed,vehicle=replace(self.completed.vehicle,type_confidence=0))
        f=self.fact(self.result(c),'fact_vehicle')
        self.assertTrue(f.ready,f.issues)
        self.assertEqual(f.sql.value()['class_conf'],0)

    def test_large_native_segment_and_global_ordinal_do_not_become_sql_ordinals(self):
        p=self.evidence('plate');a=replace(p.ledger[0],segment_id=2**40,submission_ordinal=2**41)
        p=replace(p,ledger=(a,),selected=replace(p.selected,segment_id=a.segment_id))
        f=self.fact(self.result(self.with_evidence('plate',p)),'fact_plate_read')
        self.assertTrue(f.ready,f.issues)
        self.assertEqual(f.delivery.value()['selected_attempt']['submission_ordinal'],1)
        self.assertEqual(f.metadata.value()['native_segment_id'],2**40)
        self.assertEqual(f.metadata.value()['native_submission_ordinal'],2**41)

    def test_uncertainty_and_counts_do_not_coerce_boolean_values(self):
        p=self.evidence('plate');a=replace(p.ledger[0],execution_unknown=0)
        self.assertBlocked(self.result(self.with_evidence('plate',replace(p,ledger=(a,)))),
                           'fact_plate_read','PLATE_LEDGER_MISMATCH')
        c=replace(self.evidence('colour'),local_errors=False)
        self.assertBlocked(self.result(self.with_evidence('colour',c)),
                           'fact_vehicle_colour','INVALID_EVIDENCE_OR_SCHEMA')

    def test_same_frame_receipts_cannot_diverge_across_consumers(self):
        middle=FrameReceipt(1,START+timedelta(seconds=1),101,.04)
        altered=replace(middle,utc=middle.utc+timedelta(microseconds=1))
        colour=self.evidence('colour')
        sample=replace(colour.samples[0],receipt=middle)
        colour=replace(colour,samples=(colour.samples[0],sample,colour.samples[-1]))
        p=self.evidence('plate');crop=replace(p.selected.crop,receipt=altered)
        p=replace(p,ledger=(replace(p.ledger[0],crop=crop),),selected=replace(p.selected,crop=crop))
        c=self.with_evidence('colour',colour)
        c=replace(c,consumer_evidence=tuple((n,p if n == 'plate' else e) for n,e in c.consumer_evidence))
        result=self.result(c)
        self.assertEqual(result.ready_rows,())
        self.assertBlocked(result,'fact_plate_read','FRAME_RECEIPT_MISMATCH')

    def test_actual_coordinator_completion_builds_without_consuming_before_acceptance(self):
        from vehicle.license_plate_service.run_video_pipeline.tests.test_shared_plate import SharedPlateTests, LOCAL as EXECUTED_OCR
        from vehicle.vehicle_color_service.vehicle_color_service.shared import SharedColourPipeline
        harness=SharedPlateTests('test_single_owner_exact_receipt_and_model')
        def colour_factory(ctx, **kwargs):
            return SharedColourPipeline(ctx,**kwargs,local_model=LOCAL,agent_enabled=False)
        target='vehicle.license_plate_service.run_video_pipeline.tests.test_shared_plate.SharedColourPipeline'
        with patch(target,side_effect=colour_factory):
            owner,plate,coord,executor,colour=harness.setup_owner(colour=True)
        self.addCleanup(plate.shutdown)
        ctx=owner.pipeline.lifecycle.context
        run=RunResolution(ctx.org_id,ctx.run_id,ctx.resolution.run_key,ctx.camera,
            ctx.resolution.lane_key,'vehicle',ctx.resolution.detector,'file',ctx.resolution.source_media_key,
            ctx.started_at,ctx.service_version,(20261008,20261009),'synthetic coordinator test',True)
        clip=replace(self.res.media[0],run_id=ctx.run_id,camera_id=ctx.camera.camera_id,ts=ctx.started_at)
        models=(ResolvedModel(LOCAL,81),ResolvedModel(RecognitionIdentity(EXECUTED_OCR.model_name,
                EXECUTED_OCR.version,EXECUTED_OCR.task),83))
        res=replace(self.res,run=run,models=models,colours=(Dimension('red',21,True),),media=(clip,))
        accepted=[]; retained=[]
        def completed(evidence):
            self.assertEqual(len(owner.pending_finalized_observations(owner=coord)),1)
            before=(len(plate._pending),len(colour._pending))
            proposal=self.builder.construct(evidence,res)
            self.assertEqual((len(plate._pending),len(colour._pending)),before)
            self.assertEqual(len(proposal.ready_rows),3,[(f.row_kind,f.issues) for f in proposal.facts])
            accepted.append(proposal);retained.append(evidence)
        coord.on_completed=completed
        for index in range(3):harness.frame(coord,index)
        coord.finish();coord.flush_closures()
        self.assertEqual(len(accepted),1)
        self.assertEqual(owner.pipeline.detector_tracker.process.call_count,3)
        self.assertEqual(plate.detector.predict.call_count,3)
        self.assertEqual((len(plate._pending),len(colour._pending)),(0,0))
        retry=self.builder.construct(retained[0],res,prior=accepted[0])
        self.assertEqual([f.retry for f in retry.ready_rows],['identical']*3)
        self.assertEqual(self.fact(retry,'fact_vehicle_colour').sql.value()['samples'],3)

    def test_missing_owner_receipt_and_untyped_closure_are_blocked(self):
        life=self.completed.vehicle.lifetime
        for changed,code in ((replace(life,first_receipt=None),'MISSING_RECEIPT'),
                             (replace(life,closure='untyped'),'INCOMPLETE_LIFETIME')):
            c=replace(self.completed,vehicle=replace(self.completed.vehicle,lifetime=changed),consumer_evidence=())
            result=self.result(c)
            self.assertBlocked(result,'fact_vehicle',code)
            self.assertEqual(result.ready_rows,())
        self.assertIsNone(self.fact(self.result(replace(self.completed,
            vehicle=replace(self.completed.vehicle,lifetime=replace(life,first_receipt=None)),
            consumer_evidence=())),'fact_vehicle').diagnostic.value()['frame_index'])

    def test_date_registration_validates_mathematical_calendar_key(self):
        registered=replace(self.res.run,date_keys=(10101,99991231))
        self.assertEqual(registered.date_keys,(10101,99991231))
        with self.assertRaises(ValueError):replace(self.res.run,date_keys=(2026011,))

    def test_duplicate_media_object_identity_rejected(self):
        media=self.media()
        with self.assertRaises(ValueError):
            replace(self.res,media=self.res.media+(media,replace(media,media_key=202)))

    def test_incomplete_native_read_does_not_fabricate_nullable_fields(self):
        p=self.evidence('plate');a=p.ledger[0]
        for field in ('text_confidence','plate_category'):
            result=json.loads(a.result_json);result.pop(field)
            changed=replace(p,ledger=(replace(a,result_json=json.dumps(result).encode()),))
            self.assertBlocked(self.result(self.with_evidence('plate',changed)),
                               'fact_plate_read','MISSING_PLATE_RESULT_FIELDS')
        result=json.loads(a.result_json);result['text_confidence']=None
        changed=replace(p,ledger=(replace(a,result_json=json.dumps(result).encode()),))
        self.assertBlocked(self.result(self.with_evidence('plate',changed)),
                           'fact_plate_read','INVALID_SCORE')

    def test_review_accepted_payload_disappearance_conflicts(self):
        prior=self.result();p=self.evidence('plate')
        omitted=replace(p,outcome='omitted',ledger=(),selected=None,omission_reason='no_detection',
                        detections=0,admitted_crops=0,ambiguous_crops=0,unassociated_crops=0)
        invalid=replace(p,selected=replace(p.selected,text='invalid-selected-text'))
        a=replace(p.ledger[0],status='settlement_timeout',execution_unknown=True)
        unsettled=replace(p,outcome='omitted',ledger=(a,),selected=None,
                          omission_reason='unsettled_operation',settlement_incomplete=True)
        life=replace(self.completed.vehicle.lifetime,closure=VehicleClosure.CLOCK_DISCONTINUITY,quarantined=True)
        quarantined=replace(self.completed,vehicle=replace(self.completed.vehicle,lifetime=life))
        cases=((self.with_evidence('plate',omitted),'no_detection'),
               (self.with_evidence('plate',invalid),'construction_blocked'),
               (self.with_evidence('plate',unsettled),'unsettled_operation'),
               (quarantined,'clock_discontinuity'))
        old=self.fact(prior,'fact_plate_read')
        for completed,reason in cases:
            with self.subTest(reason=reason):
                f=self.fact(self.result(completed,prior=prior),'fact_plate_read')
                self.assertEqual(f.retry,'conflict')
                self.assertFalse(f.ready)
                self.assertEqual(f.diagnostic.value()['row_uid'],old.delivery.value()['uid'])
                self.assertEqual(f.metadata.value()['proposal_diagnostic']['reason'],reason)
                self.validation.diagnostic(f.diagnostic.value())
        self.assertEqual(self.fact(self.result(prior=prior),'fact_plate_read').retry,'identical')
        self.assertEqual(self.fact(self.result(self.with_evidence('plate',omitted)),'fact_plate_read').retry,'omitted')

    def test_review_read_uses_last_matching_execution(self):
        p=self.evidence('plate');a=p.ledger[0];second=RecognizerModel('synthetic-second-ocr',4)
        p=replace(p,ledger=(replace(a,executions=a.executions+(Execution('private_local',second),)),))
        self.assertBlocked(self.result(self.with_evidence('plate',p)),
                           'fact_plate_read','RESULT_EXECUTION_MISMATCH')
        p=replace(p,selected=replace(p.selected,model=second))
        res=replace(self.res,models=self.res.models+(ResolvedModel(RecognitionIdentity(second.model_name,4,'ocr'),84),))
        self.assertTrue(self.fact(self.result(self.with_evidence('plate',p),res),'fact_plate_read').ready)

    def test_review_mixed_routes_choose_last_execution_of_result_route(self):
        p=self.evidence('plate');a=p.ledger[0];agent=RecognizerModel('synthetic-mixed-agent',4,'vlm')
        p=replace(p,ledger=(replace(a,executions=a.executions+(Execution('agent',agent),)),))
        self.assertTrue(self.fact(self.result(self.with_evidence('plate',p)),'fact_plate_read').ready)
        result=json.loads(a.result_json);result.update(route='agent',source='agent',evidence={'text_complete':True})
        p=replace(p,ledger=(replace(p.ledger[0],result_json=json.dumps(result).encode()),),
                  selected=replace(p.selected,route='agent',model=agent))
        res=replace(self.res,models=self.res.models+(ResolvedModel(RecognitionIdentity(agent.model_name,4,'vlm'),84),))
        p=replace(p,ledger=(replace(p.ledger[0],executions=p.ledger[0].executions+(Execution('private_local',OCR),)),))
        self.assertTrue(self.fact(self.result(self.with_evidence('plate',p),res),'fact_plate_read').ready)

    def test_review_oversized_numeric_scores_have_diagnostics(self):
        for value in (10**1000,-10**1000,Decimal('1e1000'),Decimal('NaN'),Decimal('sNaN'),float('inf')):
            with self.subTest(kind=type(value).__name__):
                c=replace(self.completed,vehicle=replace(self.completed.vehicle,type_confidence=value))
                self.assertBlocked(self.result(c),'fact_vehicle','INVALID_SCORE')
        p=self.evidence('plate');huge=10**1000
        p=replace(p,ledger=(replace(p.ledger[0],crop_score=huge),),selected=replace(p.selected,crop_score=huge))
        self.assertBlocked(self.result(self.with_evidence('plate',p)),'fact_plate_read','INVALID_SCORE')

    def test_review_lost_sql_projection_conflicts_and_preserves_resolution_reason(self):
        prior=self.result()
        new=self.result(resolutions=None,prior=prior)
        self.assertEqual(new.ready_rows,())
        for kind in ('fact_vehicle','fact_vehicle_colour','fact_plate_read'):
            f=self.fact(new,kind)
            self.assertEqual(f.retry,'conflict')
            self.assertIsNotNone(f.delivery)
            self.assertEqual(f.metadata.value()['proposal_diagnostic']['error_code'],'MISSING_RESOLUTION')
            self.assertEqual(f.diagnostic.value()['row_uid'],self.fact(prior,kind).delivery.value()['uid'])

    def test_review_parent_blocking_survives_retry_comparison(self):
        prior=self.result()
        c=replace(self.completed,vehicle=replace(self.completed.vehicle,type_confidence=.7))
        new=self.result(c,prior=prior)
        for kind in ('fact_vehicle_colour','fact_plate_read'):
            f=self.fact(new,kind)
            self.assertEqual(f.retry,'conflict')
            self.assertIsNone(f.sql)
            self.assertEqual(f.metadata.value()['proposal_diagnostic']['error_code'],'PARENT_NOT_READY')

    def test_review_numeric_fix_does_not_swallow_unrelated_overflow(self):
        policy=Mock()
        policy.row_uid.side_effect=OverflowError('unrelated identity policy bug')
        builder=RowBuilder(identity_policy=policy,validation=self.validation)
        with self.assertRaisesRegex(OverflowError,'identity policy bug'):
            builder.construct(self.completed,self.res)


if __name__ == '__main__':
    unittest.main()
