import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase
import numpy as np
from common.console_runs import initial_status, atomic_json
from common.console_crops import ConsoleCrops


class ConsoleCropAPITests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        environment = patch.dict(os.environ,{'T1_CONSOLE_RUN_ROOT':self.tmp.name})
        environment.start();self.addCleanup(environment.stop)
        self.runs = []
        for colour in (30,180):
            run_id = str(uuid4());directory = Path(self.tmp.name)/run_id;directory.mkdir()
            status = initial_status(run_id,'mp4',False,4)
            status.update(status='completed',rows=[dict(run_id=run_id,track_id=1,plate=dict(status='omitted'))])
            atomic_json(directory/'status.json',status)
            crops = ConsoleCrops(directory)
            crops.vehicle(1,0,np.full((16,16,3),colour,np.uint8),'vehicle_observation')
            self.runs.append((run_id,directory))

    def url(self,run_id,track=1,kind='vehicle'):
        return f'/api/t1/runs/{run_id}/tracks/{track}/crops/{kind}/'

    def test_run_and_track_scoped_pngs_and_public_metadata(self):
        images = []
        for run_id,directory in self.runs:
            response = self.client.get(self.url(run_id))
            self.assertEqual(response.status_code,200)
            self.assertEqual(response['Content-Type'],'image/png')
            self.assertEqual(response['Cache-Control'],'no-store')
            self.assertEqual(response['X-Content-Type-Options'],'nosniff')
            images.append(b''.join(response.streaming_content))
            row = self.client.get(f'/api/t1/runs/{run_id}/').json()['rows'][0]
            self.assertEqual(row['vehicle_crop']['url'],self.url(run_id))
            self.assertEqual(row['vehicle_crop']['frame'],0)
            self.assertNotIn(str(directory),json.dumps(row))
            self.assertEqual(row['plate']['diagnostic']['reason'],'unknown')
        self.assertNotEqual(images[0],images[1])

    def test_no_cross_run_fallback_or_arbitrary_files_or_symlinks(self):
        first,other = self.runs
        self.assertEqual(self.client.get(self.url(other[0],2)).status_code,404)
        self.assertEqual(self.client.get(self.url(other[0],kind='plate')).status_code,404)
        self.assertEqual(self.client.get(self.url(other[0],kind='config')).status_code,404)
        path = other[1]/'crops/track-1-vehicle.png'
        path.unlink()
        self.assertEqual(self.client.get(self.url(other[0])).status_code,404)
        path.symlink_to(first[1]/'crops/track-1-vehicle.png')
        self.assertEqual(self.client.get(self.url(other[0])).status_code,404)
