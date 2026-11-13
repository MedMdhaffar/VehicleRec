"""Replaceable Redis natural-delivery consumer; PostgreSQL ACK follows commit.

Connections and approved registration records are injected by an authorized host.
No DSN/environment discovery, DDL, SQLite, inference or registration provisioning.
"""
from dataclasses import dataclass, replace
from hashlib import sha256
from .db1_context import RunContext, ResolutionInfo
from .db1_rows import FrozenJSON, RowBuilder, Resolutions
from .db1_rows.builder import KINDS, FactResult, Construction
from .db1_rows.core import require
from .db1_delivery import AcceptedEntry, _pack
from .db1_adapter import DB1Adapter, TrustedTenant, DeliveryResult
from .redis_scalar.codec import unpack_colour
from .redis_scalar.plate_codec import terminal_result


def registered_context(original,resolutions,*,environment):
    """Resolve explicitly supplied claims without changing natural identities."""
    require(isinstance(resolutions,Resolutions),'MISSING_RESOLUTION','explicit approved resolution source required')
    require(environment in {'test','production'},'UNRESOLVED_DEVELOPMENT','consumer must declare its registered target environment')
    r=resolutions.run;c=original.camera
    require(r.org_id==original.org_id and r.run_id==original.run_id
            and r.camera.org_id==c.org_id and r.camera.camera_id==c.camera_id and r.camera.timezone==c.timezone
            and r.camera.valid_from==c.valid_from and r.camera.valid_to==c.valid_to
            and (r.detector.model_name,r.detector.version,r.detector.task)==
                (original.resolution.detector.model_name,original.resolution.detector.version,original.resolution.detector.task)
            and r.started_at==original.started_at and r.service_version==original.service_version
            and r.source_kind==('file' if original.source_kind=='file' else 'stream'),
            'RUN_RESOLUTION_MISMATCH','supplied registrations differ from original natural run')
    info=ResolutionInfo(r.run_key,r.lane_key,r.lane_name,r.detector,r.source_media_key,r.provenance,r.synthetic)
    return RunContext(r.org_id,r.camera,r.run_id,original.source_kind,r.started_at,r.service_version,info,environment)


class NaturalConsumer:
    def __init__(self,*,codec,tenant,trusted_run,trusted_camera,resolutions,connection_factory,
                 environment,adapter=None,hook=None,plate_reader=None):
        require(isinstance(tenant,TrustedTenant) and tenant.org_id==codec.org,
                'ORG_MISMATCH','trusted tenant authority required')
        require(type(trusted_run) is str and bool(trusted_run) and type(trusted_camera) is str and bool(trusted_camera),
                'PARENT_MISMATCH','trusted run/camera configuration required')
        require(isinstance(resolutions,Resolutions) and callable(connection_factory),
                'MISSING_RESOLUTION','explicit resolution records and application connection factory required')
        self.codec,self.tenant,self.run,self.camera=codec,tenant,trusted_run,trusted_camera
        self.resolutions,self.connection_factory,self.environment=resolutions,connection_factory,environment
        self.builder=RowBuilder(identity_policy=codec.identity_policy,validation=codec.validation)
        self.adapter=adapter or DB1Adapter(validation=codec.validation)
        self.hook=hook or (lambda phase:None)
        self.plate_reader=terminal_result if plate_reader is None else plate_reader
        require(callable(self.plate_reader),'INVALID_METADATA','explicit terminal reader required')
        self.last_commit=None # observed adapter result, distinct from Redis ACK

    def prepare(self,raw):
        m=self.codec.decode(raw)
        require(m.kind=='db1.delivery.v1','WRONG_STREAM','complete canonical natural delivery required')
        life=m.life;original=life.context
        require((original.org_id,original.run_id,original.camera.camera_id)==(self.tenant.org_id,self.run,self.camera),
                'PARENT_MISMATCH','delivery differs from trusted tenant/run/camera')
        context=registered_context(original,self.resolutions,environment=self.environment)
        resolved_life=replace(life,context=context)
        bundle=m.body['bundle'];rows={f['row_kind']:f['delivery'] for f in bundle['facts']}
        require(len(rows)==len(bundle['facts']) and 'fact_vehicle' in rows,'INVALID_DELIVERY','unique eligible parent/facts required')
        omissions={o['domain']:o['reason'] for o in bundle['omissions']}
        require(len(omissions)==len(bundle['omissions']),'INVALID_OMISSION','unique terminal omissions required')
        colour=unpack_colour(m.body['colour']['result'],life)
        plate=self.plate_reader(m.body['plate'],life)
        require(not life.quarantined,'CLOCK_QUARANTINE','quarantined delivery cannot commit')
        receipts={'fact_vehicle':life.first_receipt,'fact_vehicle_colour':colour.evidence.support_receipt,
                  'fact_plate_read':plate.selected.receipt if plate.selected else None}
        facts=[]
        for kind,domain in zip(KINDS,(None,'colour','plate','manufacturer')):
            base=rows.get(kind);receipt=receipts.get(kind) or life.first_receipt
            selected=None;media_status='not_requested'
            if base is not None:
                require(kind!='fact_vehicle_make','UNSUPPORTED_MANUFACTURER','manufacturer production unavailable')
                # Natural metadata makes no encoded object registration claim.
                require(not any(v.kind=='plate_crop' and v.service_track_id==life.service_track_id for v in self.resolutions.media),
                        'UNSUPPORTED_MEDIA','consumer cannot invent registered crop support')
                sql=self.builder.project_natural(kind,base,self.resolutions,context,resolved_life)
                natural=FrozenJSON.of(base);projection=FrozenJSON.of(sql)
                reason='media_unavailable' if kind in {'fact_vehicle','fact_plate_read'} else 'row_finalized'
                media_status='unavailable' if kind in {'fact_vehicle','fact_plate_read'} else 'not_requested'
                if kind=='fact_plate_read':
                    a=base['selected_attempt'];selected=dict(attempt_uid=a['attempt_uid'],route=a['route'],model_name=a['model']['model_name'],
                        model_version=a['model']['version'],attempts=base['attempts'],samples=None)
                if kind=='fact_vehicle_colour':
                    selected=dict(attempt_uid=None,route=base['support']['source'],model_name=base['model']['model_name'],
                        model_version=base['model']['version'],attempts=colour.evidence.attempts,samples=base['samples'])
                record_type,retry='row','new'
            else:
                require(domain in omissions and omissions[domain]!='unsettled_operation','INCOMPLETE_DELIVERY','absent or unsettled terminal cannot commit')
                natural=projection=None;reason=omissions[domain];record_type,retry='omission','omitted'
            diag=dict(schema_version='db1-diagnostics-v1',record_type=record_type,row_kind=kind,
                row_uid=base['uid'] if base else None,run_id=original.run_id,track_id=life.service_track_id,reason=reason,
                time_basis='host_replay_receipt' if original.source_kind=='file' else 'host_frame_receipt',
                frame_index=receipt.frame_index,media_pts=receipt.media_pts if receipt.media_pts is not None and receipt.media_pts>=0 else None,
                raw_tracker=dict(raw_id=life.raw_track_id,epoch=life.tracker_epoch+1),association=None,selected=selected,
                error_code=None,media_status=media_status,payload_sha256=natural.digest if natural else None)
            self.codec.validation.diagnostic(diag)
            metadata=dict(org_id=str(original.org_id),quarantined=False,native_tracker_epoch=life.tracker_epoch,
                lifetime_sequence=life.lifetime_sequence,closure_cause=life.closure.value)
            if kind=='fact_vehicle':
                # The receipt binds the COMPLETE original natural envelope as
                # well as resolved projections. Changed scalar provenance cannot
                # be hidden behind identical analytical rows.
                metadata.update(natural_delivery=m.canonical.decode(),natural_delivery_sha256=sha256(m.canonical).hexdigest())
            facts.append(FactResult(kind,natural,projection,FrozenJSON.of(diag),FrozenJSON.of(metadata),(),retry))
        construction=Construction(tuple(facts));identity=FrozenJSON.of([str(original.org_id),original.run_id,original.camera.camera_id,life.service_track_id])
        entry=AcceptedEntry(1,identity,construction,len(_pack(identity,construction)))
        self.adapter.prepare(entry,self.tenant)
        return entry

    def process(self,raw,ack,*,on_commit=None):
        self.last_commit=None
        entry=self.prepare(raw)
        # A fresh application connection after ambiguity re-enters the adapter's
        # locked acceptance/history reconciliation. Never infer commit from XACK
        # or a prior network exception, and never retry a native model here.
        with self.connection_factory() as connection:
            result=self.adapter.deliver(connection,entry,tenant=self.tenant,resolutions=self.resolutions)
            require(isinstance(result,DeliveryResult),'COMMIT_UNCONFIRMED','adapter must confirm committed result')
            self.last_commit=result
        require(isinstance(result,DeliveryResult),'COMMIT_UNCONFIRMED','adapter must confirm committed result')
        if on_commit is not None:on_commit(result)
        self.hook('committed')
        ack()
        self.hook('acknowledged')
        return result


@dataclass(frozen=True)
class DeliveryProgress:
    """One physical input's progress; never evidence for a later step/input."""
    entry_id: bytes
    raw: bytes
    commit: DeliveryResult | None = None
    ack_reply: int | None = None


class RedisDB1Consumer:
    def __init__(self,client,journal,consumer,*,name):
        require(type(name) is str and 0<len(name)<=64,'INVALID_WORKER','bounded consumer name required')
        self.client,self.journal,self.consumer,self.name=client,journal,consumer,name
        self.group='db1-natural-v1';self.cursor='0-0';self.prefer_pending=True
        self.last_delivery=None
        require(consumer.tenant.org_id==consumer.codec.org and journal.prefix.endswith(f':{{vehicle-v1.{consumer.codec.org}}}:'),
                'ORG_MISMATCH','Redis partition differs from trusted DB consumer')
        from redis.exceptions import ResponseError
        try:client.xgroup_create(journal.key('db1.delivery.v1'),self.group,id='0-0',mkstream=True)
        except ResponseError as error:
            if not str(error).startswith('BUSYGROUP'):raise

    def step(self,*,before_process=None):
        # Reset before *any* Redis call, including a failing read/claim. The
        # previous transaction cannot be attributed to an unrelated delivery.
        self.last_delivery=None
        self.consumer.last_commit=None # compatibility observation, not completion
        key=self.journal.key('db1.delivery.v1')
        for pending in (self.prefer_pending,not self.prefer_pending):
            if pending:
                reply=self.client.xautoclaim(key,self.group,self.name,0,start_id=self.cursor,count=1)
                self.cursor=reply[0];rows=reply[1]
            else:
                reply=self.client.xreadgroup(self.group,self.name,{key:'>'},count=1)
                rows=reply[0][1] if reply else []
            if not rows:continue
            self.prefer_pending=not pending
            entry,fields=rows[0]
            require(set(fields)=={b'json'},'INVALID_MESSAGE','closed final delivery fields required')
            if before_process is not None:before_process(entry,fields[b'json'])
            self.last_delivery=DeliveryProgress(entry,fields[b'json'])
            def committed(result):
                self.last_delivery=replace(self.last_delivery,commit=result)
            def ack():
                require(self.last_delivery.commit is not None,'COMMIT_UNCONFIRMED','ACK requires this input\'s confirmed commit')
                answer=self.client.xack(key,self.group,entry)
                require(type(answer) is int and answer in (0,1),'ACK_UNCONFIRMED','unexpected XACK reply')
                self.last_delivery=replace(self.last_delivery,ack_reply=answer)
            return self.consumer.process(fields[b'json'],ack,on_commit=committed)
        return None
