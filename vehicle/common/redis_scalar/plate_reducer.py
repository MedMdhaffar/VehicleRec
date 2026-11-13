"""Version-separated immutable Plate/v1 reducers. No I/O or alternate policy."""
from contracts.db1.reference import canonical_json_bytes
from ..db1_rows.core import require, verify_shared_receipts
from .plate_codec import STATE_VERSIONS, read_plate, claims, join_bodies, terminal_result
from .codec import unpack_colour, unpack_class_samples


def equal(a,b):return canonical_json_bytes(a)==canonical_json_bytes(b)


def completion_extends(old,new):
    """One completion of a frozen expected-work plan, not a general revision log."""
    for k in ('parent','capability','policy'):
        require(equal(old[k],new[k]),'PAYLOAD_CONFLICT','original transcript binding changed')
    require(old['expected_attempt_ids']==new['expected_attempt_ids'],'PAYLOAD_CONFLICT','expected crop plan changed')
    for flag in ('detection_complete','admission_complete'):
        require(not old[flag] or new[flag],'PAYLOAD_CONFLICT','completeness regressed')
    require(old['settlement']=='open' or old['settlement']==new['settlement'],
            'PAYLOAD_CONFLICT','settlement changed')
    candidates={c['candidate_id']:c for c in new['candidates']}
    require(all(candidates.get(c['candidate_id'])==c for c in old['candidates']),
            'PAYLOAD_CONFLICT','accepted candidate evidence disappeared/changed')
    attempts={a['attempt_id']:a for a in new['attempts']}
    for a in old['attempts']:
        b=attempts.get(a['attempt_id'])
        require(b is not None,'PAYLOAD_CONFLICT','accepted crop attempt disappeared')
        for key in ('attempt_id','submission_ordinal','candidate_id','expected_operation_ids'):
            require(equal(a[key],b[key]),'PAYLOAD_CONFLICT','original crop plan changed')
        operations={o['operation_id']:o for o in b['operations']}
        for o in a['operations']:
            n=operations.get(o['operation_id'])
            require(n is not None,'PAYLOAD_CONFLICT','original operation disappeared')
            if o['outcome'] not in {'pending','unknown'}:
                require(equal(o,n),'PAYLOAD_CONFLICT','known operation result changed')
            else:
                for key in ('operation_id','sequence','dependencies','kind','route'):
                    require(equal(o[key],n[key]),'PAYLOAD_CONFLICT','original operation request changed')
                require(o['model'] is None or equal(o['model'],n['model']),
                        'PAYLOAD_CONFLICT','known execution model changed')


def reduce_plate(codec,role,message,previous):
    require(role in STATE_VERSIONS,'WRONG_STREAM','explicit v1 worker role required')
    state=dict(previous or {})
    require(not state or state.get('state_version')==STATE_VERSIONS[role],
            'STATE_VERSION_CONFLICT','retained v0/unknown state cannot be upgraded')
    if not state:state={'state_version':STATE_VERSIONS[role]}
    life,body=message.life,message.body
    if role=='plate':
        require(message.kind=='plate.batch','WRONG_STREAM','Plate accepts transcript batches only')
        require(set(state)<={'state_version','batch_id','revisions','status'},'STATE_VERSION_CONFLICT','unknown Plate state shape')
        require('batch_id' not in state or state['batch_id']==body['batch_id'],
                'PAYLOAD_CONFLICT','accepted original Plate batch identity changed')
        revisions=dict(state.get('revisions',{}));r=str(body['revision'])
        entry=dict(event_id=message.event_id,body=body)
        if r in revisions:
            require(equal(revisions[r],entry),'PAYLOAD_CONFLICT','immutable revision changed')
            return state,[]
        require(state.get('status')!='terminal','PAYLOAD_CONFLICT','accepted terminal cannot be replaced')
        if r=='1':
            require('0' in revisions,'WAITING_DEPENDENCY','completion awaits initial recoverable handoff')
            require(state.get('status')=='waiting','REVISION_CONFLICT','completion requires retained initial wait')
            require(equal(revisions['0']['body']['life'],body['life']),'PARENT_MISMATCH','original lifetime changed')
            completion_extends(revisions['0']['body']['transcript'],body['transcript'])
        evaluation=read_plate(body,life)
        require(r=='0' or evaluation.state=='terminal','REVISION_CONFLICT','single completion must settle all expected work')
        revisions[r]=entry
        state.update(batch_id=body['batch_id'],revisions=revisions,status=evaluation.state)
        if evaluation.result is None:return state,[]
        result=evaluation.result
        terminal=dict(body,claims=claims(result),provenance_sha256=result.provenance.digest)
        return state,[('results.plate',codec.make('plate.terminal',terminal))]
    require(message.kind in {'vehicle.closed.plate','colour.terminal','plate.terminal'},
            'WRONG_STREAM','explicit v1 join inputs required')
    require(set(state)<={'state_version','inputs','status','delivery'},'STATE_VERSION_CONFLICT','unknown v1 join state shape')
    inputs=dict(state.get('inputs',{}))
    slot={'vehicle.closed.plate':'closed','colour.terminal':'colour','plate.terminal':'plate'}[message.kind]
    entry=dict(event_id=message.event_id,body=body)
    require(slot not in inputs or equal(inputs[slot],entry),'PAYLOAD_CONFLICT','accepted component identity/content changed')
    for accepted in inputs.values():
        require(equal(accepted['body']['life'],body['life']),'PARENT_MISMATCH','original lifetime differs')
    inputs[slot]=entry
    # Validate correlations as soon as independently checkable; a mismatched
    # terminal/closure cannot poison the immutable join before a third arrives.
    if 'closed' in inputs:
        c=inputs['closed']['body']
        for domain in ('colour','plate'):
            if domain in inputs:
                require(c[domain+'_batch_id']==inputs[domain]['body']['batch_id'],
                        'PAYLOAD_CONFLICT','original component batch identity differs')
        if 'plate' in inputs:
            require(c['plate']==inputs['plate']['body']['transcript']['capability'],
                    'CAPABILITY_RESULT_CONFLICT','original Plate capability differs')
    # Check every available original receipt now, before accepting a partial
    # join; a third component must not be needed to discover a poisoned slot.
    receipts=[life]
    if 'closed' in inputs:
        receipts.append(unpack_class_samples(inputs['closed']['body']['classes'],life))
    if 'colour' in inputs:
        colour_result=unpack_colour(inputs['colour']['body']['result'],life)
        receipts.extend((colour_result.evidence,colour_result.original_measurements()))
    if 'plate' in inputs:
        receipts.append(terminal_result(inputs['plate']['body'],life).transcript)
    verify_shared_receipts(tuple(receipts),{})
    state['inputs']=inputs
    if set(inputs)!={'closed','colour','plate'}:
        require('delivery' not in state,'PAYLOAD_CONFLICT','accepted delivery cannot disappear')
        state['status']='waiting'
        return state,[]
    closed,colour,plate=(inputs[k]['body'] for k in ('closed','colour','plate'))
    bundle=join_bodies(codec,closed,colour,plate)
    if 'delivery' in state:
        require(equal(state['delivery'],bundle.payload.value()),'PAYLOAD_CONFLICT','accepted natural output changed')
        return state,[]
    state.update(status='ready',delivery=bundle.payload.value())
    output=codec.make('db1.delivery.v1',dict(life=closed['life'],closed=closed,colour=colour,plate=plate,
        bundle=bundle.payload.value(),bundle_sha256=bundle.digest))
    return state,[('db1.delivery.v1',output)]
