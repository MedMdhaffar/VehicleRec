"""Live Engine request ledger. No text interpretation and no database writes."""
from dataclasses import replace
from ..db1_rows.core import require, Blocked
from ..engine_plate import CropPlan, OperationPlan
from ..redis_scalar.codec import pack_life
from ..redis_scalar.plate_codec import pack_transcript, terminal_result
from ..plate_metadata import PlateTranscript
from .codec import digest
from .policy import common


class EngineSession:
    def __init__(self,codec,adapter,resources,offers,*,session,batch_id,bindings,
                 capabilities=('whole_ocr','marker','left_ocr','right_ocr'),operation_budget=32,capability='enabled'):
        require(adapter.progressive,'WRONG_VERSION','v2 requires progressive adapter')
        self.codec,self.adapter=codec,adapter
        self.resources=dict(resources);self.offers=tuple(offers);self.bindings=dict(bindings)
        self.ledger={};self.evidence={};self.started=set();self.accepted=[];self.seal=None;self.sealed=None
        self.completed=False;self.capability=capability
        t=adapter.snapshot()
        initial=PlateTranscript(t.identity,capability,t.candidates,(),(),True,False,'open')
        self.crop=dict(life=pack_life(adapter.lifetime),session=session,batch_id=batch_id,
            transcript=pack_transcript(initial),offers=list(offers),capabilities=list(capabilities),operation_budget=operation_budget)
        self.raw=codec.make('crop',self.crop)  # validate before accepting input

    def admission(self,message):
        require(message.kind=='request','WRONG_STREAM','recognition request required')
        r=message.body;rid=r['request_id']
        if rid in self.ledger:
            old,raw=self.ledger[rid]
            require(digest(old)==digest(r),'PAYLOAD_CONFLICT','request identity changed')
            return raw
        require(len(self.ledger)<64,'CAPACITY_EXCEEDED','bounded rejection/admission ledger full; do not ACK')
        reason='accepted'
        if r['session']!=self.crop['session']:reason='session_unavailable'
        elif digest(common(r))!=digest(common(self.crop)):reason='binding'
        elif self.seal is not None:reason='generation_closed'
        elif self.capability!='enabled' or r['operation'] not in self.crop['capabilities'] or r['operation'] not in self.bindings:reason='unsupported_capability'
        elif len(self.accepted)>=self.crop['operation_budget']:reason='capacity'
        elif r['sequence']!=len(self.accepted)+1:reason='dependency'
        else:
            offer=next((o for o in self.offers if o['attempt_id']==r['attempt_id']),None)
            resource=self.resources.get(r['candidate_id'])
            if not offer or any(r[k]!=offer[k] for k in ('attempt_id','ordinal','candidate_id')):reason='binding'
            elif resource is None or resource.released:reason='crop_unavailable'
            else:
                deps=r['dependencies'];expected=[]
                old=self.adapter._plans.get(r['attempt_id'])
                for d in deps:
                    evidence=self.evidence.get(d['operation_id'])
                    original=self.ledger.get(d['operation_id'])
                    if not evidence or not original or evidence['generation']!=d['generation'] or evidence['operation']['outcome']!='succeeded' or original[0]['attempt_id']!=r['attempt_id']:
                        reason='dependency';break
                    expected.append(evidence['operation']['kind'])
                if r['operation']=='whole_ocr' and (deps or old):reason='dependency'
                if r['operation']=='marker' and sorted(expected)!=['whole_ocr']:reason='dependency'
                if r['operation'] in {'left_ocr','right_ocr'} and sorted(expected)!=['marker','whole_ocr']:reason='dependency'
                if old and any(o.kind==r['operation'] for o in old.operations):reason='dependency'
                if reason=='accepted':
                    op=OperationPlan(rid,r['sequence'],tuple(d['operation_id'] for d in deps),r['operation'],self.bindings[r['operation']])
                    plan=CropPlan(r['attempt_id'],r['ordinal'],r['candidate_id'],(old.operations if old else ())+(op,),'plate-request-v2')
                    try:self.adapter.admit(resource.binding,resource,plan)
                    except Blocked as error:
                        reason={'CAPACITY_EXCEEDED':'capacity','UNSUPPORTED_PLAN':'unsupported_capability',
                                'INVALID_DEPENDENCY':'dependency'}.get(error.code,'binding')
        if reason=='accepted':self.accepted.append(rid)
        raw=self.codec.make('admission',dict(**common(self.crop),request=r,
            accepted=reason=='accepted',reason=reason,revision=len(self.accepted))) if reason not in {'session_unavailable','binding'} else self.codec.make(
                'admission',dict(**common(r),request=r,accepted=False,reason=reason,revision=len(self.accepted)))
        self.ledger[rid]=(r,raw)
        return raw

    def allow_execution(self,rid):
        """Called only after acceptance publication is confirmed; never on XREAD alone."""
        require(rid in self.accepted,'INVALID_ADMISSION','only admitted operations may start')
        self.started.add(rid)

    def step(self):
        # The adapter never sees unadmitted future operations. Execution cannot
        # overtake its admission publication, even after a lost reply.
        pending=[rid for rid in self.accepted if self.adapter._records[rid].outcome=='pending']
        if pending and pending[0] not in self.started:return False
        return self.adapter.step()

    def collect(self):
        self.adapter.poll()
        if self.adapter._active is not None:return []  # native return is not retirement
        t=self.adapter.snapshot();outputs=[]
        for a in pack_transcript(t)['attempts']:
            for op in a['operations']:
                rid=op['operation_id']
                if op['outcome']=='pending' or rid in self.evidence:continue
                body=dict(**common(self.crop),request=self.ledger[rid][0],operation=op,generation=digest(op),retired=True)
                self.codec.make('evidence',body)
                self.evidence[rid]=body;outputs.append(body)
        return outputs

    def finish_generation(self,message):
        require(message.kind=='seal','WRONG_STREAM','Plate generation seal required')
        seal=message.body
        require(digest(common(seal))==digest(common(self.crop)),'PARENT_MISMATCH','seal lifetime/session changed')
        require(self.seal is None or digest(self.seal)==digest(seal),'PAYLOAD_CONFLICT','generation seal changed')
        require(seal['request_ids']==sorted(self.accepted) and seal['revision']==len(self.accepted)
                and len(self.accepted)==len(self.ledger), 'SEAL_MISMATCH','exact accepted request set/revision required')
        require(set(self.evidence)==set(self.accepted),'WAITING_DEPENDENCY','seal awaits exact scalar handoffs')
        self.adapter.poll()
        require(not self.adapter.blockers,'ENGINE_EVIDENCE_CONFLICT','execution binding conflict blocks settlement')
        if self.adapter._active is not None:return None
        if any(r.outcome in {'pending','unknown'} for r in self.adapter.records()):return None
        self.seal=seal
        self.adapter.seal_admission()
        t=self.adapter.snapshot()
        if self.capability!='enabled':t=replace(t,capability=self.capability)
        body=dict(**common(self.crop),seal=seal,transcript=pack_transcript(t))
        raw=self.codec.make('sealed',body)
        require(self.sealed is None or self.sealed==raw,'PAYLOAD_CONFLICT','sealed evidence changed')
        self.sealed=raw
        return raw

    def complete(self,terminal):
        require(self.sealed is not None,'COMPLETION_MISMATCH','generation not settled')
        m=self.codec.v1.decode(terminal)
        require(m.kind=='plate.terminal' and m.body['batch_id']==self.crop['batch_id']
                and digest(m.body['life'])==digest(self.crop['life']),'COMPLETION_MISMATCH','terminal binding changed')
        t=terminal_result(m.body,m.life).transcript
        expected=self.adapter.snapshot()
        if self.capability!='enabled':expected=replace(expected,capability=self.capability)
        require(digest(pack_transcript(t))==digest(pack_transcript(expected)),
                'COMPLETION_MISMATCH','Plate terminal differs from original evidence')
        # Disabled sessions have no resources; adapter's native snapshot is enabled.
        self.adapter.confirm_progressive_terminal(self.adapter.snapshot())
        self.completed=True
        return self.adapter.cleanup()
