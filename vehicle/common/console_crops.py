"""Run-local display artifacts. Never changes canonical recognition or DB1 data."""
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil

from .console_runs import atomic_json

REASONS = {'no_detection':'no_plate_detected', 'unassociated_crop':'not_associated',
           'ambiguous_association':'not_associated', 'rejected_crop':'crop_rejected'}


def plate_diagnostic(reason, candidates, *, complete):
    """No candidates alone is not proof that detection completed successfully."""
    reason = REASONS.get(reason, 'unknown') if complete else 'unknown'
    frames = [c['receipt']['frame_index'] for c in candidates]
    return dict(reason=reason, frame=min(frames) if frames else None)


def diagnostic_for_result(plate):
    t = plate.transcript
    diagnostic = plate_diagnostic(plate.omission_reason,
        [dict(receipt=dict(frame_index=c.receipt.frame_index)) for c in t.candidates],
        complete=t.capability == 'enabled' and t.detection_complete and t.admission_complete)
    if plate.selected is not None:diagnostic['frame'] = plate.selected.receipt.frame_index
    return diagnostic


def manifest(directory):
    path = directory/'crops.json'
    if path.is_symlink():return {}
    try:
        value = json.loads(path.read_text())
        return value['tracks'] if value['schema_version'] == 'console-crops-v1' else {}
    except (OSError, ValueError, KeyError):return {}


def thumbnail_path(directory, track_id, kind):
    if kind not in {'vehicle', 'plate'} or track_id < 1:raise FileNotFoundError()
    track = manifest(directory).get(str(track_id), {})
    item = track.get('vehicle', {}) if kind == 'vehicle' else track.get('plate', {}).get('crop', {})
    if item.get('status') != 'available':raise FileNotFoundError()
    folder = directory/'crops'
    path = folder/f'track-{track_id}-{kind}.png'
    if folder.is_symlink() or path.is_symlink() or not path.is_file():raise FileNotFoundError()
    return path


def _public_crop(directory, track_id, kind, item):
    public = {k:item[k] for k in ('status','source','frame','sha256') if k in item}
    try:
        thumbnail_path(directory, track_id, kind)
        public.update(status='available', url=f'/api/t1/runs/{directory.name}/tracks/{track_id}/crops/{kind}/')
    except (OSError, ValueError):public.update(status='unavailable', url=None)
    return public


def enrich_status(directory, status):
    tracks = manifest(directory)
    for row in status.get('rows', []):
        sid = row['track_id']
        track = tracks.get(str(sid), {})
        row['vehicle_crop'] = _public_crop(directory, sid, 'vehicle', track.get('vehicle', {}))
        plate = track.get('plate', {})
        row['plate']['crop'] = _public_crop(directory, sid, 'plate', plate.get('crop', {}))
        if 'diagnostic' in plate:
            row['plate']['diagnostic'] = plate['diagnostic']
        elif 'diagnostic' not in row['plate']:
            # Legacy runs retain original omission/provenance, not crop pixels.
            try:
                bundle = json.loads((directory/f'delivery-{sid}.json').read_text())
                t = bundle['plate_provenance']
                reason = next((o['reason'] for o in bundle['omissions'] if o['domain'] == 'plate'), None)
                row['plate']['diagnostic'] = plate_diagnostic(reason, t['candidates'],
                    complete=t['capability'] == 'enabled' and t['detection_complete'] and t['admission_complete'])
            except (OSError, ValueError, KeyError):
                row['plate']['diagnostic'] = dict(reason='unknown', frame=None)
    return status


class ConsoleCrops:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.folder = self.directory/'crops'
        self.folder.mkdir(mode=0o700, exist_ok=True)
        self.tracks = manifest(self.directory)
        self.pool = {}
        self.current = None

    def save(self):
        atomic_json(self.directory/'crops.json', dict(schema_version='console-crops-v1', tracks=self.tracks))

    def image(self, name, pixels):
        from PIL import Image
        if (pixels.dtype.name != 'uint8' or pixels.ndim != 3 or pixels.shape[2] != 3
                or pixels.nbytes > 8*1024*1024 or min(pixels.shape[:2]) < 1):
            raise ValueError('invalid_console_crop')
        path = self.folder/name
        temp = path.with_suffix('.tmp')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as output, Image.fromarray(pixels[:, :, ::-1]) as image:
            image.save(output, format='PNG')
        temp.replace(path)
        return sha256(pixels.tobytes()).hexdigest()

    def vehicle(self, sid, frame, pixels, source):
        if type(sid) not in (int,float) or int(sid) != sid or sid < 1:raise ValueError('invalid_track')
        sid = int(sid)
        track = self.tracks.setdefault(str(sid), {})
        if pixels.nbytes > 8*1024*1024:return
        if source != 'manufacturer' and 'vehicle' in track:return
        digest = self.image(f'track-{sid}-vehicle.png', pixels)
        track['vehicle'] = dict(status='available', source=source, frame=frame, sha256=digest)
        self.save()

    def bind_manufacturer(self, ledger, runtime):
        observe = ledger.observe
        def observing(life, receipt, bbox, crop, frame_sha256):
            self.vehicle(life.service_track_id, receipt.frame_index, crop, 'vehicle_observation')
            self.current = (life.service_track_id, receipt.frame_index)
            try:return observe(life, receipt, bbox, crop, frame_sha256)
            finally:self.current = None
        ledger.observe = observing
        if runtime is not None:
            infer = runtime.infer
            def inferring(crop):
                # Exact BGR pixels handed to the real runtime, before inference.
                self.vehicle(*self.current, crop, 'manufacturer')
                return infer(crop)
            runtime.infer = inferring

    def bind_plate(self, ledger):
        prepare = ledger.prepare
        def preparing(closed, **kwargs):
            adapter = prepare(closed, **kwargs)
            sid = closed.owner.service_track_id
            for _, resource in ledger.lives[sid].best.values():
                c = resource.binding.candidate
                key = (sid, c.candidate_id)
                if key in self.pool or resource.released:continue
                import numpy as np
                pixels = np.frombuffer(resource.read(), dtype=np.uint8).reshape(c.crop_size[1], c.crop_size[0], 3)
                name = f'pool-{sid}-{c.segment_id}.png'
                digest = self.image(name, pixels)
                if digest != c.crop_sha256:raise ValueError('plate_crop_digest_mismatch')
                self.pool[key] = dict(name=name, sha256=digest, frame=c.receipt.frame_index)
            return adapter
        ledger.prepare = preparing

    def plate(self, sid, result):
        plate = dict(diagnostic=diagnostic_for_result(result), terminal=True)
        if result.selected is not None:
            candidate_id = next(a.candidate_id for a in result.transcript.attempts
                                if a.attempt_id == result.selected.attempt_id)
            candidate = next(c for c in result.transcript.candidates if c.candidate_id == candidate_id)
            selected = self.pool.get((sid, candidate_id))
            if selected is not None and selected['sha256'] == candidate.crop_sha256:
                shutil.copyfile(self.folder/selected['name'], self.folder/f'track-{sid}-plate.tmp')
                temporary = self.folder/f'track-{sid}-plate.tmp'
                os.chmod(temporary, 0o600)
                temporary.replace(self.folder/f'track-{sid}-plate.png')
                plate['crop'] = dict(status='available', source='selected_plate',
                                     frame=selected['frame'], sha256=selected['sha256'])
        self.tracks.setdefault(str(sid), {})['plate'] = plate
        self.save()

    def diagnostics(self, ledger):
        changed = False
        for sid, life in ledger.lives.items():
            track = self.tracks.setdefault(str(sid), {})
            if track.get('plate', {}).get('terminal'):continue
            diagnostic = dict(reason='unknown', frame=None)
            # An admitted crop can still be awaiting selection. Earlier rejected
            # candidates do not establish the reason for its missing selection.
            if not life.best:
                entries = [e for e in ledger.associations if sid in e['projections']]
                rejected = [e for e in entries if e['status'] in {'rejected_no_geometry','rejected_size'}
                            and e['association']['selected_track_id'] == sid]
                unassociated = [e for e in entries if e['association']['status'] in {'unassociated','ambiguous'}]
                if rejected:diagnostic = dict(reason='crop_rejected', frame=rejected[0]['frame'])
                elif unassociated:diagnostic = dict(reason='not_associated', frame=unassociated[0]['frame'])
            plate = dict(diagnostic=diagnostic)
            if track.get('plate') != plate:
                track['plate'] = plate;changed = True
        if changed:self.save()

    def close(self):
        # Delete only the exact private pool files created by this instance.
        for item in self.pool.values():
            (self.folder/item['name']).unlink(missing_ok=True)


def recover_manufacturer_mp4(directory):
    """Recover only hash-verifiable crops from a settled MP4. No inference."""
    import sqlite3
    import cv2
    directory = Path(directory).resolve(strict=True)
    status = json.loads((directory/'status.json').read_text())
    if status['source_kind'] != 'mp4' or status['status'] not in {'completed','stopped','failed'}:
        raise ValueError('settled_mp4_required')
    try:
        from .console_runs import _process_identity
        owner = json.loads((directory/'owner.json').read_text())
        ticks, state = _process_identity(owner['pid'])
        if state != 'Z' and ticks == owner.get('start_ticks', ticks):raise ValueError('worker_still_alive')
    except FileNotFoundError:pass
    journal = directory/'manufacturer.sqlite'
    if not journal.is_file():return dict(recovered=0, unavailable=len(status['rows']))
    with sqlite3.connect(journal.as_uri()+'?mode=ro', uri=True) as db:
        bindings = [json.loads(b) for b, result in db.execute('SELECT binding,result FROM attempts') if result is not None]
    wanted = {row['track_id'] for row in status['rows']}
    by_frame = {}
    for b in bindings:
        vehicle = b['vehicle']
        if vehicle['context']['run_id'] != directory.name or vehicle['service_track_id'] not in wanted:
            raise ValueError('crop_run_track_mismatch')
        frame = b['receipt']['frame_index']
        if type(frame) not in (int,float) or int(frame) != frame or not 0 <= frame < status.get('decoded_frames',status['frames']):
            raise ValueError('crop_frame_outside_run')
        by_frame.setdefault(int(frame), []).append(b)
    store = ConsoleCrops(directory)
    capture = cv2.VideoCapture(str(directory/'input.mp4'))
    recovered = []
    try:
        for frame in range(max(by_frame, default=-1)+1):
            ok, pixels = capture.read()
            if not ok:break
            for b in by_frame.get(frame, []):
                if sha256(pixels.tobytes()).hexdigest() != b['frame_sha256']:continue
                if any(int(v) != v for v in b['bbox']):continue
                x1,y1,x2,y2 = map(int,b['bbox'])
                crop = pixels[y1:y2,x1:x2].copy()
                if list(crop.shape) != b['shape'] or sha256(crop.tobytes()).hexdigest() != b['crop_sha256']:continue
                sid = int(b['vehicle']['service_track_id'])
                store.vehicle(sid, frame, crop, 'manufacturer')
                recovered.append(sid)
    finally:capture.release()
    return dict(recovered=len(recovered), unavailable=len(wanted-set(recovered)),
                recovered_track_ids=sorted(recovered), verified_original_pixels=True)
