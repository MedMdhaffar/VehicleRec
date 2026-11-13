"""Bounded scalar operational view; never exports readings, pixels or secrets."""
from collections import Counter
from ..plate_requests.codec import STREAMS


def run_status(source,engine,publisher,*,admitted_frames,errors=None,database=None):
    p=publisher
    requests=accepted=rejected=seals=handoffs=0;executions=Counter()
    if p is not None:
        for plan in p.plans.values():
            s=plan.session;requests+=len(s.ledger);accepted+=len(s.accepted);rejected+=len(s.ledger)-len(s.accepted)
            seals+=s.seal is not None;handoffs+=s.completed
            executions.update(r.outcome for r in s.adapter.records())
    starts=Counter(e['kind'] for e in engine.audit.events if e['phase']=='start')
    retired=Counter(e['kind'] for e in engine.audit.events if e['phase']=='retired')
    streams={};redis_status='unavailable'
    if p is not None:
        try:
            for stream in [*STREAMS.values(),'engine.lifecycle.plate','engine.evidence.colour','results.colour','results.plate','db1.delivery.v1']:
                key=p.journal.key(stream)
                groups=p.client.xinfo_groups(key) if p.client.exists(key) else []
                streams[stream]=dict(entries=p.client.xlen(key),groups=[dict(name=g['name'].decode() if isinstance(g['name'],bytes) else g['name'],
                    pending=g['pending'],lag=g.get('lag')) for g in groups])
            redis_status='observed'
        except Exception:redis_status='unavailable'
    metadata=[];diagnostics={}
    if p is not None:
        try:
            from ..redis_scalar.codec import bounded_json,vehicle_key
            import re
            for sid,plan in p.plans.items():
                raw=p.client.hget(p.journal.prefix+'plate_requests:journal','vehicle:'+vehicle_key(plan.original.owner.lifetime))
                state=bounded_json(raw,max_bytes=20*1024*1024,max_depth=40,max_nodes=500000,max_string=4096) if raw else {}
                reason=state.get('reason')
                metadata.append(dict(service_track_id=sid,state=state.get('status','not_observed'),
                    reason=reason if isinstance(reason,str) and re.fullmatch('[A-Za-z0-9_]{1,64}',reason) else None,
                    conflict_reported=bool(state.get('conflict_sha256'))))
            diagnostics={role:p.client.xlen(p.journal.key('diagnostics.'+role)) for role in ['colour','plate_requests','aggregator_plate']}
        except Exception:metadata=[]
    buffers=engine.plate.buffers
    db=dict(enabled=False,new=0,identical=0,conflicts=0,unknown_commits=0,ack_callbacks_completed=0,native_validated=False)
    db.update(database or {})
    return dict(schema_version='waycon-run-status-v1',org_id=str(engine.colour.pipeline.observation_clock.context.org_id),
        run_id=engine.colour.pipeline.observation_clock.context.run_id,camera_id=engine.colour.pipeline.observation_clock.context.camera.camera_id,
        session=p.session if p is not None else None,protocol='vehicle-plate-requests-v2',manufacturer='unsupported',
        frames=dict(decoded=source.decoded,admitted=admitted_frames,source_opens=source.opens),
        lifetimes=dict(active=len(engine.colour.working),closed=len(engine.colour.closed_ids),waiting=len(engine.pending),finalized=len(p.confirmed) if p else 0),
        recognition=dict(requests=requests,accepted=accepted,rejected=rejected,execution_outcomes={k:executions[k] for k in ['pending','unknown','not_started','succeeded','failed']},
            native_starts=dict(starts),known_retirements=dict(retired),native_active=engine.audit.active,max_native_active=engine.audit.max_active,
            evidence_confirmed=sum(1 for identity in p.writer.confirmed if identity.startswith('evidence-')) if p else 0,
            generation_seals=seals,confirmed_terminal_handoffs=handoffs),
        resources=dict(retained_crops=len(buffers),retained_crop_bytes=sum(c.binding.size_bytes for c in buffers),
            cleanup_quarantine=sum(bool(b.cleanup_pending) for b in engine.plate.bindings.values()),cleanup_errors=list(getattr(engine,'cleanup_errors',[]))),
        publication=dict(fifo_pending=len(p.writer.pending) if p else 0,retained_bytes=p.writer.bytes if p else 0,
            final_natural_deliveries=streams.get('db1.delivery.v1',{}).get('entries')),
        database=db,metadata=dict(plate=metadata,diagnostic_counts=diagnostics),
        redis=dict(status=redis_status,streams=streams),errors=dict(errors or {}))
