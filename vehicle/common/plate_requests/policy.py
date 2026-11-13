"""Plate-owned progressive decisions using the existing canonical scalar policy."""
from copy import deepcopy
from dataclasses import replace
from contracts.db1.reference import canonical_json_bytes
from ..db1_rows.core import require
from ..plate_metadata import PlateMetadata, PlateTranscript, PlateCropAttempt, PlateOperation
from ..redis_scalar.plate_codec import unpack_transcript, pack_transcript, claims
from ..redis_scalar.codec import unpack_life
from vehicle.license_plate_service.run_video_pipeline.scalar_policy import needs_region_ocr
from vehicle.license_plate_service.run_video_pipeline.text_policy import OCRResult, normalize_plate_text
from .codec import digest, STREAMS


def common(body):return {k:body[k] for k in ('life','session','batch_id')}


def transcript(state,*,sealed=False):
    crop=state['crop'];life=unpack_life(crop['life'])
    base=unpack_transcript(crop['transcript'],life)
    attempts=[]
    for offer in crop['offers']:
        operations=[]
        for rid,r in state['requests'].items():
            if r['attempt_id']!=offer['attempt_id']:continue
            admission=state['admissions'].get(rid)
            if not admission or not admission['accepted']:continue
            evidence=state['evidence'].get(rid)
            if evidence:
                # Reuse the v1 scalar decoder and canonical transcript validation.
                op=evidence['operation']
            else:
                op=dict(operation_id=rid,sequence=r['sequence'],dependencies=[d['operation_id'] for d in r['dependencies']],
                    kind=r['operation'],outcome='pending',model=None,raw_text=None,confidence=None,marker=None,route='private_local')
            operations.append(op)
        if operations:
            attempts.append(dict(attempt_id=offer['attempt_id'],submission_ordinal=offer['ordinal'],candidate_id=offer['candidate_id'],
                expected_operation_ids=sorted(o['operation_id'] for o in operations),operations=sorted(operations,key=lambda o:o['sequence'])))
    raw=pack_transcript(base)
    raw.update(expected_attempt_ids=sorted(a['attempt_id'] for a in attempts),attempts=attempts,
               admission_complete=sealed,settlement='settled' if sealed else 'open')
    if sealed and base.capability!='enabled':
        return PlateTranscript(base.identity,base.capability,(),(),(),True,True,'settled')
    t=unpack_transcript(raw,life)
    PlateMetadata().evaluate(life,t)
    return t


def next_kind(operations,policy,regional_order):
    """Canonical whole/marker prerequisites, then the shared regional predicate."""
    by_kind={o.kind:o for o in operations}
    whole=by_kind.get('whole_ocr')
    if whole is None:return 'whole_ocr',()
    if whole.outcome in {'pending','unknown'}:return None,None
    if whole.outcome!='succeeded':return None,()
    marker=by_kind.get('marker')
    if marker is None:return 'marker',(whole.operation_id,)
    if marker.outcome in {'pending','unknown'}:return None,None
    if marker.outcome!='succeeded':return None,()
    read=OCRResult(whole.raw_text,normalize_plate_text(whole.raw_text),whole.confidence)
    if not needs_region_ocr(read,marker.marker,policy=policy):return None,()
    for kind in regional_order:
        op=by_kind.get(kind)
        if op is None:return kind,(whole.operation_id,marker.operation_id)
        if op.outcome in {'pending','unknown'}:return None,None
    return None,()


def reduce_event(codec,message,previous,*,regional_order=('left_ocr','right_ocr')):
    require(regional_order in {('left_ocr','right_ocr'),('right_ocr','left_ocr')},'INVALID_POLICY','permitted regional order required')
    s=deepcopy(previous) if previous else dict(version='plate-progressive-state-v2',status='waiting',requests={},admissions={},evidence={})
    require(s['version']=='plate-progressive-state-v2','STATE_VERSION_CONFLICT','explicit v2 state required')
    body=message.body;kind=message.kind
    require(kind in {'crop','admission','evidence','sealed'},'WRONG_STREAM','Plate accepts Engine scalar events only')
    slot={'crop':'crop','admission':'admissions','evidence':'evidence','sealed':'sealed'}[kind]
    if kind in {'crop','sealed'}:
        old=s.get(slot)
        require(old is None or digest(old)==digest(body),'PAYLOAD_CONFLICT','immutable Engine evidence changed')
        if old is not None:return s,[]
    else:
        rid=body['request']['request_id'];old=s[slot].get(rid)
        require(old is None or digest(old)==digest(body),'PAYLOAD_CONFLICT','immutable admission/evidence changed')
        if old is not None:return s,[]
        require(len(s[slot])<32,'CAPACITY_EXCEEDED','bounded reordered dependency waits')
    require(s['status']!='terminal','PAYLOAD_CONFLICT','late evidence cannot change accepted terminal')
    if s['status']=='blocked':
        return s,[]  # sticky rejection/conflict; no later semantic completion
    if kind in {'crop','sealed'}:s[slot]=body
    else:s[slot][rid]=body
    if 'crop' not in s:return s,[]  # persisted bounded dependency wait
    crop=s['crop']
    for value in [s.get('sealed')]+list(s['admissions'].values())+list(s['evidence'].values()):
        if value is None:continue
        require(digest(common(value))==digest(common(crop)),'PARENT_MISMATCH','full lifetime/session/batch binding changed')
        if 'request' in value:
            rid=value['request']['request_id']
            require(rid in s['requests'] and digest(s['requests'][rid])==digest(value['request']),
                    'UNEXPECTED_WORK','Engine evidence must match a retained Plate request')
    if any(not a['accepted'] for a in s['admissions'].values()):
        s['status']='blocked';s['reason']='request_rejected';return s,[]
    for rid,r in s['requests'].items():
        admission=s['admissions'].get(rid);evidence=s['evidence'].get(rid)
        if not admission or not evidence:return s,[]
        require(admission['revision']==r['sequence'],'REVISION_CONFLICT','admitted revision differs from request sequence')
        for dependency in r['dependencies']:
            d=s['evidence'].get(dependency['operation_id'])
            require(d is not None and d['generation']==dependency['generation'] and d['operation']['outcome']=='succeeded',
                    'INVALID_DEPENDENCY','exact successful evidence generation required')
    t=transcript(s)
    if 'seal' in s:
        if 'sealed' not in s:return s,[]
        response=s['sealed']
        require(digest(response['seal'])==digest(s['seal']),'SEAL_MISMATCH','Engine seal set/revision differs')
        final=transcript(s,sealed=True)
        require(digest(pack_transcript(final))==digest(response['transcript']),
                'SEAL_MISMATCH','Engine transcript differs from original admitted evidence')
        result=PlateMetadata().evaluate(message.life,final).result
        require(result is not None,'EXECUTION_WAITING','enabled unfinished work cannot finalize')
        terminal=dict(life=crop['life'],batch_id=crop['batch_id'],revision=0,transcript=pack_transcript(final),
            claims=claims(result),provenance_sha256=result.provenance.digest)
        raw=codec.v1.make('plate.terminal',terminal)
        s.update(status='terminal',terminal_sha256=digest(terminal))
        return s,[('results.plate',raw)]
    attempts={a.attempt_id:a for a in t.attempts}
    for offer in crop['offers']:
        a=attempts.get(offer['attempt_id'])
        kind,deps=next_kind(a.operations if a else (),t.policy,regional_order)
        if deps is None:return s,[]
        if kind is None:continue
        if kind not in crop['capabilities']:
            s.update(status='blocked',reason='unsupported_capability');return s,[]
        if len(s['requests'])>=crop['operation_budget']:
            s.update(status='blocked',reason='operation_budget');return s,[]
        r=dict(**common(crop),attempt_id=offer['attempt_id'],ordinal=offer['ordinal'],candidate_id=offer['candidate_id'],
            sequence=len(s['requests'])+1,operation=kind,
            dependencies=[dict(operation_id=d,generation=s['evidence'][d]['generation']) for d in sorted(deps)])
        r['request_id']=codec.request_id(r)
        s['requests'][r['request_id']]=r
        return s,[(STREAMS['request'],codec.make('request',r))]
    seal=dict(**common(crop),revision=len(s['requests']),request_ids=sorted(s['requests']))
    s['seal']=seal;s['status']='sealing'
    return s,[(STREAMS['seal'],codec.make('seal',seal))]
