"""Injected scalar Plate transcripts; no recognizer, broker or database execution."""
import json
import subprocess
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from uuid import UUID

from vehicle.common.plate_metadata import (PlateMetadata, PlateModel, PlateCandidate,
    PlateOperation, PlateCropAttempt, PlateTranscript, MarkerScalar)
from vehicle.common.aggregator import Aggregator, Branch
from vehicle.common.colour_metadata import ColourMetadata
from vehicle.common.db1_rows import ContractValidation, UUID5Policy, NaturalPlateBuilder
from vehicle.common.db1_rows.core import Blocked, FrozenJSON
from vehicle.common.tests.test_colour_aggregator import fixture

ROOT = Path(__file__).resolve().parents[3]
MODEL = PlateModel('original-paddle-local-digest', 2, 'ocr')
MARKER = PlateModel('original-marker-local-digest', 1, 'detect')


def transcript(owner, *, text='123 تونس 4567', region=False):
    from vehicle.common.observation_evidence import VehicleIdentity
    identity = VehicleIdentity.from_lifetime(owner.lifetime)
    c = PlateCandidate(identity, 'candidate-1', 1, 11, owner.lifetime.first_receipt,
        'associated', True, 'engine-crop-1', 'a'*64, (10,10,110,40), (100,30), 20., .9, 'original-quality-v1')
    ops = [PlateOperation('whole',1,(),'whole_ocr','succeeded',MODEL,
                         '123456' if region else text, .96),
           PlateOperation('marker',2,(),'marker','succeeded',MARKER,
                          marker=MarkerScalar(True,.98,(40,0,60,30),50.))]
    if region:
        ops += [PlateOperation('left',3,('whole','marker'),'left_ocr','succeeded',MODEL,'123',.97),
                PlateOperation('right',4,('whole','marker'),'right_ocr','succeeded',replace(MODEL,version=3),'456',.98)]
    attempt = PlateCropAttempt('crop-attempt-1',7,c.candidate_id,tuple(o.operation_id for o in ops),tuple(ops))
    return PlateTranscript(identity,'enabled',(c,),(attempt.attempt_id,),(attempt,),True,True,'settled')


class PlateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validation = ContractValidation(json.loads((ROOT/'contracts/db1/rows.v0.schema.json').read_text()),
            json.loads((ROOT/'contracts/db1/diagnostics.v1.schema.json').read_text()))
        cls.policy = UUID5Policy(UUID('98c2adf9-264e-4ac2-b6a1-3239fa02ecbb'))

    def setUp(self):
        self.owner,self.measurements,self.classes = fixture()
        self.colour = ColourMetadata().finalize(self.owner.lifetime,self.measurements)
        self.service = PlateMetadata()
        self.aggregator = Aggregator(identity_policy=self.policy, validation=self.validation)
        self.t = transcript(self.owner)

    def evaluate(self, t=None):
        return self.service.evaluate(self.owner.lifetime, self.t if t is None else t)

    def join(self, result, *, prior=None):
        return self.aggregator.join_with_plate(self.owner,self.classes,(
            Branch('colour','enabled',self.colour),Branch('plate','enabled',result),
            Branch('manufacturer','unsupported')),prior=prior)

    def operations(self, operations, **changes):
        a = replace(self.t.attempts[0],operations=tuple(operations),**changes)
        return replace(self.t,attempts=(a,))

    def test_supported_whole_read_and_natural_parent_colour_plate(self):
        result = self.evaluate().result
        self.assertEqual((result.outcome,result.selected.text),('read','123 تونس 4567'))
        value = self.join(result).delivery.payload.value()
        self.assertEqual(value['schema_version'],'natural-db1-bundle-v1')
        self.assertEqual([f['row_kind'] for f in value['facts']],['fact_vehicle','fact_vehicle_colour','fact_plate_read'])
        self.assertEqual(value['omissions'],[dict(domain='manufacturer',reason='unsupported_capability')])
        for fact in value['facts']:
            self.validation.delivery(fact['row_kind'],fact['delivery'])
        plate = value['facts'][-1]['delivery']
        self.assertEqual(plate['selected_attempt']['model'],MODEL.policy())
        self.assertIsNone(plate['selected_attempt']['crop_media'])
        self.assertEqual(plate['ts'],value['facts'][0]['delivery']['ts'])
        self.assertNotEqual(plate['ts'],value['facts'][1]['delivery']['ts'])
        self.assertEqual(plate['attempts'],1)

    def test_region_fusion_is_one_crop_and_last_matching_actual_model(self):
        result = self.evaluate(transcript(self.owner,region=True)).result
        self.assertEqual((result.outcome,result.selected.text,result.selected.operation_id),('read','123 تونس 456','right'))
        self.assertEqual(result.selected.model.version,3)
        fact = self.join(result).delivery.payload.value()['facts'][-1]['delivery']
        self.assertEqual(fact['attempts'],1)
        self.assertEqual(fact['selected_attempt']['submission_ordinal'],1)
        self.assertEqual(fact['selected_attempt']['model']['version'],3)

    def test_invalid_ambiguous_incomplete_text_known_execution_unreadable(self):
        for text in ('','123456','123 تونس','AB12','1234 تونس 4567'):
            with self.subTest(text=text):
                result = self.evaluate(transcript(self.owner,text=text)).result
                self.assertEqual(result.outcome,'unreadable')
                self.assertIsNone(result.selected.text)
                self.assertIsNone(result.selected.confidence)
                self.validation.delivery('fact_plate_read',self.join(result).delivery.payload.value()['facts'][-1]['delivery'])

    def test_region_mismatch_or_low_confidence_cannot_invent_read(self):
        t = transcript(self.owner,region=True)
        for changed in (replace(t.attempts[0].operations[-1],raw_text='9999'),
                        replace(t.attempts[0].operations[-1],confidence=.1)):
            a = replace(t.attempts[0],operations=t.attempts[0].operations[:-1]+(changed,))
            self.assertEqual(self.evaluate(replace(t,attempts=(a,))).result.outcome,'unreadable')

    def test_best_segment_quality_null_and_deterministic_tie(self):
        c = self.t.candidates[0]; a = self.t.attempts[0]
        for quality,expected in ((None,a.attempt_id),(20.,a.attempt_id),(21.,'second')):
            c2 = replace(c,candidate_id='c2',segment_id=2,quality=quality,
                         receipt=self.measurements[1].receipt,crop_ref='crop2',crop_sha256='b'*64)
            ops2 = tuple(replace(o,operation_id='second-'+o.operation_id) for o in a.operations)
            a2 = replace(a,attempt_id='second',submission_ordinal=20,candidate_id='c2',
                         expected_operation_ids=tuple(o.operation_id for o in ops2),operations=ops2)
            t = replace(self.t,candidates=(c2,c),expected_attempt_ids=('second',a.attempt_id),attempts=(a2,a))
            selected = self.evaluate(t).result.selected
            self.assertEqual(selected.attempt_id,expected)
            if expected == 'second':
                self.assertEqual(selected.receipt,c2.receipt)
                self.assertEqual(selected.crop_score,21.)

    def test_same_segment_recheck_uses_legacy_replacement_policy(self):
        c = self.t.candidates[0]; a=self.t.attempts[0]
        c2=replace(c,candidate_id='second',quality=30.,receipt=self.measurements[1].receipt)
        ops=tuple(replace(o,operation_id='second-'+o.operation_id,
                         raw_text='124 تونس 4567' if o.kind == 'whole_ocr' else None) for o in a.operations)
        a2=replace(a,attempt_id='second',submission_ordinal=8,candidate_id='second',
                   expected_operation_ids=tuple(o.operation_id for o in ops),operations=ops)
        t=replace(self.t,candidates=(c,c2),expected_attempt_ids=(a.attempt_id,'second'),attempts=(a,a2))
        self.assertEqual(self.evaluate(t).result.selected.attempt_id,a.attempt_id)
        ops=tuple(replace(o,raw_text='123 تونس 4567') if o.kind=='whole_ocr' else o for o in ops)
        self.assertEqual(self.evaluate(replace(t,attempts=(a,replace(a2,operations=ops)))).result.selected.attempt_id,'second')

    def test_later_failed_ocr_cannot_relabel_old_whole_result(self):
        ops=self.t.attempts[0].operations+(PlateOperation('retry',3,(),'whole_ocr','failed',replace(MODEL,version=9)),)
        t=self.operations(ops,expected_operation_ids=('whole','marker','retry'))
        result=self.evaluate(t).result
        self.assertEqual(result.outcome,'unreadable')
        self.assertEqual((result.selected.operation_id,result.selected.model.version),('retry',9))

    def test_forged_terminal_selected_text_model_or_confidence_is_blocked(self):
        result=self.evaluate().result
        for change in (dict(text='999 تونس 9999'),dict(model=replace(MODEL,version=90)),dict(confidence=.2)):
            with self.assertRaises(Blocked):
                self.join(replace(result,selected=replace(result.selected,**change)))

    def test_duplicate_attempt_operation_expected_ids_and_sequences(self):
        a=self.t.attempts[0]; ops=a.operations
        bads=(replace(self.t,attempts=(a,a)),
              self.operations(ops+(ops[0],)),
              replace(self.t,expected_attempt_ids=(a.attempt_id,a.attempt_id)),
              self.operations((ops[0],replace(ops[1],sequence=1))))
        for bad in bads:
            with self.subTest(bad=bad),self.assertRaises(Blocked): self.evaluate(bad)

    def test_wrong_parent_association_and_geometry(self):
        c=self.t.candidates[0]
        for change in (dict(identity=replace(c.identity,service_track_id=99)),dict(association='ambiguous'),
                       dict(bbox=(1,1,0,0)),dict(crop_sha256='not-a-hash')):
            with self.assertRaises(Blocked): self.evaluate(replace(self.t,candidates=(replace(c,**change),)))

    def test_rejected_attempt_receipt_conflicts_with_class_same_frame(self):
        c=replace(self.t.candidates[0],candidate_id='rejected',receipt=replace(self.measurements[1].receipt,monotonic=101.5),crop_accepted=False)
        t=replace(self.t,candidates=self.t.candidates+(c,))
        # Candidate receipt is independently in lifetime, but conflicts cross-component.
        result=self.evaluate(t).result
        with self.assertRaises(Blocked) as error: self.join(result)
        self.assertEqual(error.exception.code,'FRAME_RECEIPT_MISMATCH')

    def test_missing_expected_outcomes_and_admission_closure_do_not_settle(self):
        a=self.t.attempts[0]
        for t in (replace(self.t,detection_complete=False),replace(self.t,admission_complete=False),
                  replace(self.t,settlement='open'),replace(self.t,attempts=()),
                  self.operations(a.operations[:-1]),
                  self.operations((replace(a.operations[0],outcome='pending',model=None,raw_text=None,confidence=None),a.operations[1]))):
            with self.subTest(t=t):
                evaluation=self.evaluate(t)
                self.assertEqual((evaluation.state,evaluation.result),('waiting',None))
                self.assertEqual(self.join(None).state,'waiting')

    def test_known_failure_before_and_after_model(self):
        a=self.t.attempts[0]
        before=tuple(replace(o,outcome='not_started',model=None,raw_text=None,confidence=None,marker=None) for o in a.operations)
        result=self.evaluate(self.operations(before)).result
        self.assertEqual((result.outcome,result.omission_reason),('omitted','failure_before_model'))
        after=(replace(a.operations[0],outcome='failed',raw_text=None,confidence=None),before[1])
        result=self.evaluate(self.operations(after)).result
        self.assertEqual(result.outcome,'unreadable')
        self.assertEqual(result.selected.model,MODEL)

    def test_unknown_execution_never_becomes_unreadable(self):
        a=self.t.attempts[0]
        unknown=replace(a.operations[0],outcome='unknown',raw_text=None,confidence=None,model=None)
        t=self.operations((unknown,a.operations[1]))
        with self.assertRaises(Blocked): self.evaluate(t)
        result=self.evaluate(replace(t,settlement='uncertain')).result
        self.assertEqual((result.outcome,result.omission_reason),('omitted','unsettled_operation'))
        self.assertIsNone(result.selected)
        bundle=self.join(result).delivery.payload.value()
        self.assertNotIn('fact_plate_read',[f['row_kind'] for f in bundle['facts']])
        self.assertIn(dict(domain='plate',reason='unsettled_operation'),bundle['omissions'])

    def test_no_detection_requires_complete_known_work_and_emits_no_plate_row(self):
        t=replace(self.t,candidates=(),attempts=(),expected_attempt_ids=())
        result=self.evaluate(t).result
        self.assertEqual((result.outcome,result.omission_reason),('omitted','no_detection'))
        self.assertEqual(len(self.join(result).delivery.payload.value()['facts']),2)
        self.assertEqual(self.evaluate(replace(t,detection_complete=False)).state,'waiting')

    def test_disabled_unsupported_and_rejected_association_omissions(self):
        for capability in ('disabled','unsupported'):
            result=self.evaluate(replace(self.t,capability=capability,candidates=(),attempts=(),expected_attempt_ids=())).result
            self.assertEqual(result.omission_reason,capability+'_capability')
        for association,reason in (('ambiguous','ambiguous_association'),('unassociated','unassociated_crop'),('associated','rejected_crop')):
            c=replace(self.t.candidates[0],association=association,crop_accepted=False)
            result=self.evaluate(replace(self.t,candidates=(c,),attempts=(),expected_attempt_ids=())).result
            self.assertEqual(result.omission_reason,reason)

    def test_unsupported_route_and_conflicting_operation_result(self):
        ops=self.t.attempts[0].operations
        for change in (dict(route='unsupported'),dict(outcome='failed'),dict(model=MARKER)):
            with self.assertRaises(Blocked): self.evaluate(self.operations((replace(ops[0],**change),ops[1])))
        for center in (True,'bad',float('nan')):
            marker=replace(ops[1],marker=replace(ops[1].marker,center_x=center))
            with self.assertRaises(Blocked):self.evaluate(self.operations((ops[0],marker)))

    def test_equivalent_order_retry_exact_bytes_hash_ids_and_original_provenance(self):
        t=transcript(self.owner,region=True); a=t.attempts[0]
        original=self.evaluate(t).result
        changed=replace(t,expected_attempt_ids=tuple(reversed(t.expected_attempt_ids)),
            candidates=tuple(reversed(t.candidates)),attempts=(replace(a,
                expected_operation_ids=tuple(reversed(a.expected_operation_ids)),
                operations=tuple(replace(o,dependencies=tuple(reversed(o.dependencies))) for o in reversed(a.operations))),))
        retry=self.evaluate(changed).result
        self.assertEqual(original,retry)
        prior=self.join(original).delivery
        again=self.join(retry,prior=prior).delivery
        self.assertEqual(prior.payload.canonical,again.payload.canonical)
        self.assertEqual(prior.digest,again.digest)

    def test_accepted_row_or_omission_disappearance_and_changed_provenance_conflict(self):
        read=self.evaluate().result; prior=self.join(read).delivery
        with self.assertRaises(Blocked): self.join(None,prior=prior)
        omitted=self.evaluate(replace(self.t,candidates=(),attempts=(),expected_attempt_ids=())).result
        with self.assertRaises(Blocked): self.join(omitted,prior=prior)
        omission_prior=self.join(omitted).delivery
        with self.assertRaises(Blocked): self.join(read,prior=omission_prior)
        with self.assertRaises(Blocked): self.join(None,prior=omission_prior)
        c=replace(self.t.candidates[0],crop_ref='different-same-fact')
        changed=self.evaluate(replace(self.t,candidates=(c,))).result
        with self.assertRaises(Blocked): self.join(changed,prior=prior)

    def test_natural_builder_canonical_omission_order_and_changed_omission_conflict(self):
        result=self.evaluate().result
        builder=NaturalPlateBuilder(identity_policy=self.policy,validation=self.validation)
        from dataclasses import replace
        owner=replace(self.owner,vehicle_type='car',type_confidence=.8)
        omissions=(('manufacturer','unsupported_capability'),('colour','disabled_capability'))
        a=builder.construct(owner,None,result,omissions=omissions)
        b=builder.construct(owner,None,result,omissions=tuple(reversed(omissions)),prior=a)
        self.assertEqual(a.digest,b.digest)
        with self.assertRaises(Blocked):builder.construct(owner,None,result,
            omissions=(('manufacturer','disabled_capability'),('colour','disabled_capability')),prior=a)

    def test_colour_only_v0_stays_byte_identical(self):
        branches=(Branch('colour','enabled',self.colour),Branch('plate','disabled'),Branch('manufacturer','unsupported'))
        v0=self.aggregator.join(self.owner,self.classes,branches).delivery
        self.assertEqual(v0.payload.value()['schema_version'],'natural-db1-bundle-v0')
        v1=self.aggregator.join_with_plate(self.owner,self.classes,branches).delivery
        self.assertEqual(v1.payload.value()['schema_version'],'natural-db1-bundle-v1')
        self.assertEqual(v0.payload.value()['facts'],v1.payload.value()['facts'])
        self.assertNotIn('plate_provenance',v0.payload.value())

    def test_pure_fresh_imports_block_heavy_and_runtime_modules(self):
        code = r'''
import sys, importlib.abc
blocked = ('cv2','numpy','torch','ultralytics','paddle','paddleocr','openai','requests','httpx','redis','sqlite3',
           'vehicle.common.db1_rows.builder','vehicle.common.db1_rows.records','vehicle.common.observation_coordinator',
           'vehicle.license_plate_service.run_video_pipeline.agent','vehicle.license_plate_service.run_video_pipeline.config')
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == n or fullname.startswith(n+'.') for n in blocked):
            raise AssertionError('forbidden import: '+fullname)
sys.meta_path.insert(0,Guard())
from vehicle.common.plate_metadata import PlateMetadata
from vehicle.common.aggregator import Aggregator
from vehicle.common.db1_rows import NaturalPlateBuilder
assert not any(any(n == b or n.startswith(b+'.') for b in blocked) for n in sys.modules)
print('pure imports verified')
'''
        run=subprocess.run([sys.executable,'-c',code],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertIn('pure imports verified',run.stdout)

    def test_legacy_local_natural_plate_projection_equivalence(self):
        # Compare the actual legacy canonical row projection, including UUIDs,
        # selected receipt, quality and last matching execution, without SQL I/O.
        from vehicle.common.db1_rows import RowBuilder
        from vehicle.common.observation_evidence import OwnedCrop
        from vehicle.license_plate_service.run_video_pipeline.shared_evidence import (
            FinalPlateEvidence, PlateAttempt, SelectedPlate, RecognizerModel, Execution)
        for region in (False,True):
            t=transcript(self.owner,region=region);a=t.attempts[0];c=t.candidates[0]
            result=self.evaluate(t).result;s=result.selected
            kinds={o.kind:o for o in a.operations}
            native,_=self.service._interpret(c,kinds,t.policy)
            crop=OwnedCrop(b'\0'*9000,(30,100,3),'|u1',c.bbox,c.receipt)
            executions=tuple(Execution('private_local',RecognizerModel(o.model.model_name,o.model.version))
                             for o in a.operations if o.kind != 'marker')
            attempt=PlateAttempt(a.attempt_id,a.submission_ordinal,t.identity,c.segment_id,c.raw_plate_id,
                crop,c.quality,'original','completed',executions,FrozenJSON.of(native).canonical)
            model=RecognizerModel(s.model.model_name,s.model.version)
            selected=SelectedPlate(a.attempt_id,c.segment_id,crop,c.quality,s.text,'private_local',model)
            e=FinalPlateEvidence(t.identity,self.owner.lifetime.first_receipt,self.owner.lifetime.last_receipt,
                self.owner.lifetime.closure,False,'read',None,(attempt,),selected,1,1,0,0,False)
            legacy=RowBuilder(identity_policy=self.policy,validation=self.validation)
            receipt,fields,*_=legacy._plate(e,self.owner.lifetime,t.identity)
            pure_receipt,pure_fields=result.natural_fields(self.owner.lifetime,self.policy)
            self.assertEqual(receipt,pure_receipt)
            self.assertEqual(FrozenJSON.of(fields),FrozenJSON.of(pure_fields))

    def test_capability_mismatch_malformed_records_bounds_and_unknown_status(self):
        disabled=self.evaluate(replace(self.t,capability='disabled',candidates=(),attempts=(),expected_attempt_ids=())).result
        with self.assertRaises(Blocked): self.join(disabled)
        bads=(replace(self.t,attempts=('bad',)),
              self.operations(('bad',)),
              replace(self.t,candidates=self.t.candidates*129),
              self.operations((replace(self.t.attempts[0].operations[0],outcome='timeout'),)),
              self.operations((replace(self.t.attempts[0].operations[0],raw_text='1'*4097),)),
              self.operations((replace(self.t.attempts[0].operations[0],confidence=float('nan')),)))
        for bad in bads:
            with self.subTest(bad=type(bad)),self.assertRaises(Blocked): self.evaluate(bad)

    def test_region_dependencies_and_expected_operation_identity_must_be_unambiguous(self):
        t=transcript(self.owner,region=True);a=t.attempts[0]
        bad=replace(a,operations=a.operations[:-1]+(replace(a.operations[-1],dependencies=('whole',)),))
        with self.assertRaises(Blocked): self.evaluate(replace(t,attempts=(bad,)))
        a2=replace(a,attempt_id='second',submission_ordinal=8,operations=())
        with self.assertRaises(Blocked):self.evaluate(replace(t,attempts=(a,a2),expected_attempt_ids=(a.attempt_id,'second')))

    def test_original_model_and_receipt_not_borrowed_from_later_failed_crop(self):
        c=self.t.candidates[0];a=self.t.attempts[0]
        c2=replace(c,candidate_id='second',receipt=self.measurements[2].receipt,quality=100.,segment_id=2)
        ops=(PlateOperation('later-failed',1,(),'whole_ocr','failed',replace(MODEL,version=99)),)
        a2=replace(a,attempt_id='later',submission_ordinal=9,candidate_id='second',expected_operation_ids=('later-failed',),operations=ops)
        t=replace(self.t,candidates=(c,c2),expected_attempt_ids=(a.attempt_id,'later'),attempts=(a,a2))
        result=self.evaluate(t).result
        self.assertEqual((result.selected.attempt_id,result.selected.model,result.selected.receipt),(a.attempt_id,MODEL,c.receipt))
        fact=self.join(result).delivery.payload.value()['facts'][-1]['delivery']
        self.assertEqual(fact['attempts'],2)
        self.assertEqual(fact['selected_attempt']['submission_ordinal'],1)

    def test_changed_omission_reason_conflicts_with_accepted_omission(self):
        no_detection=replace(self.t,candidates=(),attempts=(),expected_attempt_ids=())
        first=self.evaluate(no_detection).result;prior=self.join(first).delivery
        rejected=replace(self.t.candidates[0],crop_accepted=False)
        changed=self.evaluate(replace(self.t,candidates=(rejected,),attempts=(),expected_attempt_ids=())).result
        with self.assertRaises(Blocked) as error:self.join(changed,prior=prior)
        self.assertEqual(error.exception.code,'PAYLOAD_CONFLICT')

    def test_legacy_scalar_wrappers_and_native_decoder_compatibility(self):
        code=r'''
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path('vehicle/license_plate_service').resolve()))
from run_video_pipeline.pipeline import assemble_plate_detection, needs_region_ocr
from run_video_pipeline.routing import route_plate
from run_video_pipeline.ocr import PlateReader, OCRResult
from run_video_pipeline.scalar_policy import assemble_plate_fields, MarkerScalar, local_route
import numpy as np
crop=np.zeros((30,100,3),dtype=np.uint8)
detection=SimpleNamespace(bbox=(10,10,110,40),confidence=.9)
marker=MarkerScalar(True,.98,(40,0,60,30),50.)
for raw,left,right in [('123 تونس 4567','',''),('123456','123','456'),('123456','123','999'),('bad','','')]:
    reader=PlateReader.__new__(PlateReader)
    whole=reader._parse([dict(rec_text=raw,rec_score=.96)])
    left=OCRResult(left,left,.97);right=OCRResult(right,right,.98)
    legacy=assemble_plate_detection(detection,whole,marker,left,right)
    pure=assemble_plate_fields(detection.bbox,detection.confidence,whole,marker,left,right)
    assert legacy.model_dump()==pure
    eligible,confidence,reason=local_route(SimpleNamespace(**pure))
    route=route_plate(crop,legacy)
    assert route.route==('private_local' if eligible else 'agent')
    assert route.confidence==confidence and route.reason==reason
assert needs_region_ocr(OCRResult('123456','123456',.96),marker)
print('legacy wrappers and native decoding verified without model execution')
'''
        run=subprocess.run([sys.executable,'-c',code],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stderr)

    def test_v1_is_not_silently_accepted_by_colour_redis_codec(self):
        from vehicle.common.redis_scalar.worker import load_codec
        from vehicle.common.redis_scalar.synthetic import fixture as redis_fixture
        from vehicle.common.redis_scalar.reducer import reduce_message
        from contracts.db1.reference import canonical_json_bytes
        from hashlib import sha256
        codec=load_codec(self.t.identity.context.org_id,self.policy.namespace)
        # Use a current v0 message envelope then put the v1 shape under it.
        closed,batch=redis_fixture(codec)
        _,term=reduce_message(codec,'colour',codec.decode(batch),None)
        state,_=reduce_message(codec,'aggregator',codec.decode(closed),None)
        _,deliveries=reduce_message(codec,'aggregator',codec.decode(term[0][1]),state)
        message=codec.decode(deliveries[0][1])
        body=message.body
        v1=self.join(self.evaluate().result).delivery
        body['bundle']=v1.payload.value();body['bundle_sha256']=sha256(v1.payload.canonical).hexdigest()
        with self.assertRaises(Blocked) as error:
            codec.encode(message.kind,message.event_id,body)
        self.assertEqual(error.exception.code,'INVALID_DELIVERY')

    def test_changed_accepted_read_payload_conflicts_and_all_null_quality_ties(self):
        original=self.evaluate().result;prior=self.join(original).delivery
        changed=self.evaluate(transcript(self.owner,text='124 تونس 4567')).result
        with self.assertRaises(Blocked) as error:self.join(changed,prior=prior)
        self.assertEqual(error.exception.code,'PAYLOAD_CONFLICT')
        a=self.t.attempts[0];c=replace(self.t.candidates[0],quality=None)
        c2=replace(c,candidate_id='null-second',segment_id=2)
        ops=tuple(replace(o,operation_id='second-'+o.operation_id) for o in a.operations)
        a2=replace(a,attempt_id='second',candidate_id='null-second',submission_ordinal=8,
            expected_operation_ids=tuple(o.operation_id for o in ops),operations=ops)
        t=replace(self.t,candidates=(c2,c),attempts=(a2,a),expected_attempt_ids=('second',a.attempt_id))
        result=self.evaluate(t).result
        self.assertEqual(result.selected.attempt_id,a.attempt_id)
        self.assertIsNone(result.selected.crop_score)

    def regional_operations(self, operations):
        t = transcript(self.owner, region=True)
        a = replace(t.attempts[0], operations=tuple(operations),
                    expected_operation_ids=tuple(o.operation_id for o in operations))
        return replace(t, attempts=(a,))

    def test_regions_cannot_borrow_reliable_marker_from_another_execution(self):
        whole, marker, left, right = transcript(self.owner, region=True).attempts[0].operations
        weak = replace(marker, marker=replace(marker.marker, confidence=.1))
        changed = replace(marker, operation_id='marker-new', sequence=3,
                          model=replace(MARKER, version=8),
                          marker=MarkerScalar(True, .99, (20,0,30,30), 25.))
        # The strong marker exists before the sides execute, but their explicit
        # dependencies bind them to the original weak marker, not its replacement.
        t = self.regional_operations((whole, weak, changed,
            replace(left, sequence=4), replace(right, sequence=5)))
        result = self.evaluate(t).result
        self.assertEqual(result.outcome, 'unreadable')
        self.assertIsNone(result.selected.text)

    def test_regional_fusion_preserves_declared_marker_geometry(self):
        whole, marker, left, right = transcript(self.owner, region=True).attempts[0].operations
        changed = replace(marker, operation_id='marker-new', sequence=5,
            model=replace(MARKER, version=9), marker=MarkerScalar(True,.99,(20,0,30,30),25.))
        t = self.regional_operations((whole, marker, left, right, changed))
        result = self.evaluate(t).result
        self.assertEqual(result.outcome, 'read')
        # Inspect the actual scalar policy input/projection, not just final text:
        # geometry is an Engine output and must stay bound to the used execution.
        from unittest.mock import patch
        import vehicle.common.plate_metadata as boundary
        with patch.object(boundary, 'assemble_plate_fields', wraps=boundary.assemble_plate_fields) as fusion:
            self.evaluate(t)
        self.assertEqual(fusion.call_args.args[3].bbox, marker.marker.bbox)
        self.assertNotEqual(fusion.call_args.args[3].bbox, changed.marker.bbox)
        self.assertEqual(result.selected.model, right.model)

    def test_regional_fusion_rejects_mixed_dependency_generations(self):
        whole, marker, left, right = transcript(self.owner, region=True).attempts[0].operations
        for kind, original in (('marker',marker), ('whole_ocr',whole)):
            with self.subTest(kind=kind):
                newer = replace(original, operation_id='new-generation', sequence=3,
                                model=replace(original.model, version=17))
                dependencies = tuple('new-generation' if d == original.operation_id else d
                                     for d in right.dependencies)
                t = self.regional_operations((whole,marker,newer,replace(left,sequence=4),
                                               replace(right,sequence=5,dependencies=dependencies)))
                with self.assertRaises(Blocked) as error:
                    self.evaluate(t)
                self.assertEqual(error.exception.code, 'INVALID_DEPENDENCY')

    def test_regional_dependencies_must_identify_one_whole_and_one_marker(self):
        whole, marker, left, right = transcript(self.owner, region=True).attempts[0].operations
        for original in (whole, marker):
            with self.subTest(kind=original.kind):
                extra = replace(original, operation_id='extra-dependency', sequence=3)
                deps = left.dependencies + ('extra-dependency',)
                t = self.regional_operations((whole,marker,extra,
                    replace(left,sequence=4,dependencies=deps),
                    replace(right,sequence=5,dependencies=deps)))
                with self.assertRaises(Blocked) as error:
                    self.evaluate(t)
                self.assertEqual(error.exception.code, 'INVALID_DEPENDENCY')

    def test_both_regional_orders_attribute_last_supporting_model_and_retry_exactly(self):
        whole, marker, left, right = transcript(self.owner, region=True).attempts[0].operations
        left = replace(left, model=replace(MODEL,version=5))
        right = replace(right, model=replace(MODEL,version=9))
        accepted = None
        for first, last in ((left,right),(right,left)):
            with self.subTest(last=last.kind):
                first,last = replace(first,sequence=3),replace(last,sequence=4)
                t = self.regional_operations((whole,marker,first,last))
                result = self.evaluate(t).result
                self.assertEqual(result.outcome, 'read')
                self.assertEqual(result.selected.operation_id, last.operation_id)
                self.assertEqual(result.selected.model, last.model)
                prior = self.join(result).delivery
                fact = prior.payload.value()['facts'][-1]['delivery']
                self.assertEqual(fact['selected_attempt']['model'],last.model.policy())
                # Arrival/dependency ordering is not request/execution ordering.
                retry = self.regional_operations(tuple(replace(o,dependencies=tuple(reversed(o.dependencies)))
                                                        for o in reversed(t.attempts[0].operations)))
                delivery = self.join(self.evaluate(retry).result,prior=prior).delivery
                self.assertEqual(delivery.payload.canonical,prior.payload.canonical)
                self.assertEqual(delivery.digest,prior.digest)
                if accepted is not None:
                    # Changing execution order changes the original supporting
                    # model/provenance and must conflict with an accepted bundle.
                    with self.assertRaises(Blocked) as error:
                        self.join(result,prior=accepted)
                    self.assertEqual(error.exception.code,'PAYLOAD_CONFLICT')
                accepted = prior

    def test_later_failed_execution_blocks_stale_read_for_both_regional_orders(self):
        whole,marker,left,right = transcript(self.owner,region=True).attempts[0].operations
        for first,last in ((left,right),(right,left)):
            for kind in ('whole_ocr','left_ocr','right_ocr'):
                with self.subTest(last=last.kind,failed=kind):
                    failed = PlateOperation('later-failed',5,
                        ('whole','marker') if kind != 'whole_ocr' else (), kind,'failed',
                        replace(MODEL,version=99))
                    t = self.regional_operations((whole,marker,replace(first,sequence=3),
                                                  replace(last,sequence=4),failed))
                    result = self.evaluate(t).result
                    self.assertEqual(result.outcome,'unreadable')
                    self.assertEqual(result.selected.operation_id,failed.operation_id)
                    self.assertEqual(result.selected.model.version,99)

    def test_regional_fusion_preserves_declared_whole_confidence_and_model(self):
        whole,marker,left,right = transcript(self.owner,region=True).attempts[0].operations
        newer = replace(whole,operation_id='whole-new',sequence=3,confidence=.91,
                        model=replace(MODEL,version=22))
        t = self.regional_operations((whole,marker,newer,replace(left,sequence=4),replace(right,sequence=5)))
        result = self.evaluate(t).result
        self.assertEqual(result.outcome,'read')
        self.assertEqual(result.selected.confidence,whole.confidence)
        self.assertNotEqual(result.selected.confidence,newer.confidence)
        self.assertEqual(result.selected.model,right.model)

    def test_fresh_supported_whole_after_regions_remains_eligible(self):
        whole,marker,left,right = transcript(self.owner,region=True).attempts[0].operations
        fresh = replace(whole,operation_id='whole-new',sequence=5,raw_text='124 تونس 4567',
                        confidence=.97,model=replace(MODEL,version=22))
        t = self.regional_operations((whole,marker,left,right,fresh))
        result = self.evaluate(t).result
        self.assertEqual((result.outcome,result.selected.text),('read','124 تونس 4567'))
        self.assertEqual((result.selected.operation_id,result.selected.model),(fresh.operation_id,fresh.model))
