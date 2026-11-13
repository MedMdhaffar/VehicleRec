from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np
from PIL import Image
from vehicle.common.console_crops import ConsoleCrops, diagnostic_for_result, enrich_status, thumbnail_path
from vehicle.common.manufacturer.engine import ManufacturerLedger
from vehicle.common.tests.test_colour_aggregator import fixture
from vehicle.common.tests.test_manufacturer import Runtime
from vehicle.common.tests.test_plate_metadata import transcript
from vehicle.common.plate_metadata import PlateMetadata
from vehicle.common.plate_crop_smoke import EngineCrop
from vehicle.common.engine_plate import CropBinding


class ConsoleCropTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.store = ConsoleCrops(self.directory)
        self.owner, _, _ = fixture()

    def pixels(self, kind, sid=1):
        with Image.open(thumbnail_path(self.directory, sid, kind)) as image:
            return np.array(image)[:, :, ::-1]

    def test_exact_runtime_crop_replaces_fallback_and_never_a_later_observation(self):
        runtime = Runtime()
        ledger = ManufacturerLedger(enabled=True, runtime=runtime, journal=self.directory/'ledger.sqlite')
        self.addCleanup(ledger.close)
        self.store.bind_manufacturer(ledger, runtime)
        life = self.owner.lifetime
        small = np.zeros((24,24,3), np.uint8)
        selected = np.full((64,64,3), (10,20,30), np.uint8)
        ledger.observe(life, life.first_receipt, (0,0,24,24), small, 'a'*64)
        receipt = replace(life.first_receipt, frame_index=2)
        ledger.observe(life, receipt, (0,0,64,64), selected, 'b'*64)
        ledger.observe(life, replace(receipt,frame_index=3), (0,0,64,64), small.repeat(3,axis=0).repeat(3,axis=1), 'c'*64)
        self.assertEqual(runtime.calls, 1)
        self.assertTrue(np.array_equal(self.pixels('vehicle'), selected))
        meta = self.store.tracks['1']['vehicle']
        self.assertEqual((meta['source'],meta['frame']), ('manufacturer',2))
        self.assertEqual(meta['sha256'], ledger.terminal(life)['binding']['crop_sha256'])
        self.assertEqual(thumbnail_path(self.directory,1,'vehicle').stat().st_mode & 0o777, 0o600)

    def test_disabled_keeps_one_real_vehicle_observation_with_frame_zero(self):
        ledger = ManufacturerLedger()
        self.store.bind_manufacturer(ledger, None)
        pixels = np.full((32,32,3), 17, np.uint8)
        ledger.observe(self.owner.lifetime,self.owner.lifetime.first_receipt,(0,0,32,32),pixels,'a'*64)
        self.assertTrue(np.array_equal(self.pixels('vehicle'),pixels))
        self.assertEqual(self.store.tracks['1']['vehicle']['source'],'vehicle_observation')
        self.assertEqual(self.store.tracks['1']['vehicle']['frame'],0)
        self.assertEqual(ledger.calls,0)

    def test_plate_selection_uses_selected_attempt_not_another_candidate(self):
        t = transcript(self.owner)
        pixels = np.full((t.candidates[0].crop_size[1],t.candidates[0].crop_size[0],3),(2,40,90),np.uint8)
        candidate = replace(t.candidates[0],crop_sha256=sha256(pixels.tobytes()).hexdigest())
        t = replace(t,candidates=(candidate,)+t.candidates[1:])
        resource = EngineCrop(CropBinding(candidate,'fixture',pixels.nbytes),pixels.tobytes())
        ledger = SimpleNamespace(prepare=lambda closed,**kwargs:'adapter',lives={1:SimpleNamespace(best={1:(0,resource)})})
        self.store.bind_plate(ledger)
        self.assertEqual(ledger.prepare(SimpleNamespace(owner=SimpleNamespace(service_track_id=1))), 'adapter')
        resource.release()  # Same lifetime as real publisher cleanup.
        result = PlateMetadata().evaluate(self.owner.lifetime,t).result
        self.store.plate(1,result)
        self.assertTrue(np.array_equal(self.pixels('plate'),pixels))
        self.assertEqual(self.store.tracks['1']['plate']['crop']['frame'],result.selected.receipt.frame_index)
        self.store.close()
        self.assertFalse(any(self.store.folder.glob('pool-*')))
        self.assertTrue(thumbnail_path(self.directory,1,'plate').exists())

    def test_absence_reasons_require_complete_pipeline_evidence(self):
        t = transcript(self.owner)
        empty = replace(t,candidates=(),attempts=(),expected_attempt_ids=())
        for candidate_t, reason in [(empty,'no_plate_detected'),
            (replace(empty,candidates=(replace(t.candidates[0],association='unassociated',crop_accepted=False),)),'not_associated'),
            (replace(empty,candidates=(replace(t.candidates[0],crop_accepted=False),)),'crop_rejected')]:
            result = PlateMetadata().evaluate(self.owner.lifetime,candidate_t).result
            self.assertEqual(diagnostic_for_result(result)['reason'],reason)
        from vehicle.common.console_crops import plate_diagnostic
        self.assertEqual(plate_diagnostic('no_detection',[],complete=False)['reason'],'unknown')
        self.assertEqual(plate_diagnostic('unsettled_operation',[],complete=True)['reason'],'unknown')

    def test_legacy_omission_read_is_allowlisted_and_no_media_is_invented(self):
        (self.directory/'delivery-1.json').write_text(json.dumps(dict(omissions=[dict(domain='plate',reason='unassociated_crop')],
            plate_provenance=dict(capability='enabled',detection_complete=True,admission_complete=True,
                candidates=[dict(receipt=dict(frame_index=0))]),secret='not public')))
        status = enrich_status(self.directory,dict(rows=[dict(track_id=1,plate=dict(status='omitted'))]))
        row = status['rows'][0]
        self.assertEqual(row['plate']['diagnostic'],dict(reason='not_associated',frame=0))
        self.assertEqual(row['vehicle_crop']['status'],'unavailable')
        self.assertNotIn('not public',json.dumps(status))

    def test_rejected_geometry_diagnostic_survives_without_a_terminal_plate_result(self):
        ledger = SimpleNamespace(lives={1:SimpleNamespace(best={}),2:SimpleNamespace(best={})}, associations=[
            dict(frame=3,projections=[1],status='rejected_no_geometry',
                 association=dict(status='associated',selected_track_id=1))])
        self.store.diagnostics(ledger)
        self.assertEqual(self.store.tracks['1']['plate']['diagnostic'],dict(reason='crop_rejected',frame=3))
        self.assertEqual(self.store.tracks['2']['plate']['diagnostic']['reason'],'unknown')
        ledger.lives[1].best = {1:'admitted and waiting'}
        self.store.diagnostics(ledger)
        self.assertEqual(self.store.tracks['1']['plate']['diagnostic']['reason'],'unknown')
