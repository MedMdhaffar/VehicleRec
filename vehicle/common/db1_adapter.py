"""Narrow PostgreSQL 16 delivery of accepted local constructions.

No connection creation, authorization, inference, DDL or outbox mutation here.
The separately proposed db1_delivery supporting schema must be provisioned first.
"""
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.pq import TransactionStatus
from psycopg.rows import tuple_row
from jsonschema.exceptions import ValidationError

from contracts.db1.reference import canonical_json_bytes, parse_timestamp, strict_loads
from .db1_delivery import AcceptedEntry, _pack
from .db1_rows import FrozenJSON, Resolutions
from .db1_rows.builder import KINDS


class DeliveryError(RuntimeError):
    pass


class DeliveryConflict(DeliveryError):
    pass


class InvalidAcceptance(DeliveryError):
    pass


class ReferenceRejected(DeliveryError):
    pass


class HistoryMismatch(DeliveryError):
    pass


class DatabaseDeliveryError(DeliveryError):
    def __init__(self, sqlstate, *, commit_outcome):
        super().__init__('database delivery failed; retain local pending entry')
        self.sqlstate, self.commit_outcome = sqlstate, commit_outcome


@dataclass(frozen=True, slots=True)
class TrustedTenant:
    """Host assertion from authentication/authorization, never payload-derived."""
    org_id: UUID
    authority: str

    def __post_init__(self):
        if not isinstance(self.org_id, UUID) or not isinstance(self.authority, str) or not self.authority.strip():
            raise ValueError('explicit trusted tenant UUID and authority required')


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    disposition: str  # new | identical; returned only after successful commit
    acceptance_sha256: str
    fact_count: int
    omission_count: int


def require(condition, error, message):
    if not condition:
        raise error(message)


def one(cursor, table, key, value):
    cursor.execute(sql.SQL('SELECT * FROM public.{} WHERE {}=%s').format(
        sql.Identifier(table), sql.Identifier(key)), (value,))
    rows = cursor.fetchall()
    require(len(rows) == 1, ReferenceRejected, 'required registration missing or ambiguous')
    columns = [c.name for c in cursor.description]
    return dict(zip(columns, rows[0]))


def equal_fields(actual, expected):
    for key, value in expected.items():
        got = actual[key]
        if isinstance(got, UUID):
            got = str(got)
        if isinstance(value, UUID):
            value = str(value)
        require(got == value, ReferenceRejected, 'registered reference differs from supplied resolution')


def check_manufacturer_reference(cursor, manufacturer_key, vehicle_model_key):
    """Structural reference check, not enabling the unsupported make producer.

    Supervisor v0 has a direct manufacturer FK, including with a null model.
    """
    require(vehicle_model_key is None or manufacturer_key is not None,
            ReferenceRejected, 'vehicle model requires manufacturer')
    if manufacturer_key is not None:
        one(cursor, 'dim_manufacturer', 'manufacturer_key', manufacturer_key)
    if vehicle_model_key is not None:
        model = one(cursor, 'dim_vehicle_model', 'vehicle_model_key', vehicle_model_key)
        require(model['manufacturer_key'] == manufacturer_key, ReferenceRejected,
                'vehicle model manufacturer mismatch')


class DB1Adapter:
    def __init__(self, *, validation, lock_timeout_ms=2000, statement_timeout_ms=10000):
        for timeout in (lock_timeout_ms, statement_timeout_ms):
            if type(timeout) is not int or not 1 <= timeout <= 60000:
                raise ValueError('explicit bounded database timeout 1..60000 ms required')
        from .db1_rows import ContractValidation
        if not isinstance(validation,ContractValidation):
            raise ValueError('caller-loaded P1 validation required')
        self.validation = validation
        self.lock_timeout_ms, self.statement_timeout_ms = lock_timeout_ms, statement_timeout_ms
        self.compatibility = canonical_json_bytes(dict(format='db1-delivery-receipts-v1',
            equality='exact-canonical-accepted-envelope-v1',
            row_schema_sha256=sha256(canonical_json_bytes(validation._row.schema)).hexdigest(),
            diagnostic_schema_sha256=sha256(canonical_json_bytes(validation._diagnostic.schema)).hexdigest()))

    def prepare(self, entry, tenant):
        """Pure admission; no trusted context inferred from the accepted payload."""
        try:
            require(isinstance(tenant, TrustedTenant), InvalidAcceptance, 'trusted tenant context required')
            require(isinstance(entry, AcceptedEntry) and entry.acceptance == 'accepted_pending_transport',
                    InvalidAcceptance, 'immutable accepted outbox entry required')
            identity = entry.logical_identity.value()
            require(isinstance(identity, list) and len(identity) == 4 and identity[0] == str(tenant.org_id),
                    InvalidAcceptance, 'trusted tenant differs from accepted parent')
            facts = entry.construction.facts
            require(tuple(f.row_kind for f in facts) == KINDS and facts[0].ready,
                    InvalidAcceptance, 'complete accepted construction with eligible parent required')
            for fact in facts:
                for channel in (fact.delivery, fact.sql, fact.diagnostic, fact.metadata):
                    if channel is not None:
                        require(type(channel) is FrozenJSON and canonical_json_bytes(strict_loads(channel.canonical)) == channel.canonical,
                                InvalidAcceptance, 'noncanonical retained channel')
                diag = fact.diagnostic.value()
                self.validation.diagnostic(diag)
                require(fact.metadata.value().get('quarantined') is False and
                        not diag.get('settlement', {}).get('incomplete') and fact.retry not in {'blocked', 'conflict'},
                        InvalidAcceptance, 'ineligible or unsettled construction')
                require([diag['run_id'], diag['track_id']] == [identity[1], identity[3]],
                        InvalidAcceptance, 'diagnostic parent mismatch')
                if fact.sql is None:
                    require(fact.delivery is None and fact.retry == 'omitted' and
                            diag['record_type'] == 'omission' and diag['reason'] != 'unsettled_operation',
                            InvalidAcceptance, 'invalid terminal omission')
                    continue
                require(fact.row_kind != 'fact_vehicle_make', InvalidAcceptance,
                        'manufacturer production remains unsupported')
                require(fact.delivery is not None and fact.retry in {'new', 'identical'} and diag['record_type'] == 'row',
                        InvalidAcceptance, 'invalid accepted row decision')
                self.validation._row.validate(fact.snapshot())
                natural = fact.delivery.value()
                require([natural[k] for k in ('org_id','run_id','camera_id','track_id')] == identity and
                        diag['row_uid'] == natural['uid'] and diag['payload_sha256'] == fact.delivery.digest,
                        InvalidAcceptance, 'row parent or diagnostic hash mismatch')
            payload = _pack(entry.logical_identity, entry.construction)
            require(len(payload) == entry.stored_bytes, InvalidAcceptance, 'accepted envelope size mismatch')
            return payload
        except (ValueError, TypeError, KeyError, AttributeError, ValidationError):
            raise InvalidAcceptance('accepted entry failed contract validation') from None

    def _media(self, cursor, key, natural, resolutions, parent, kind):
        if key is None:
            require(natural is None, ReferenceRejected, 'media payload requires registered key')
            return None
        supplied = [m for m in resolutions.media if m.media_key == key]
        require(len(supplied) == 1, ReferenceRejected, 'explicit media resolution required')
        media = supplied[0]
        require(media.kind == kind and media.org_id == parent.org_id and
                media.camera_key == parent.camera.camera_key and media.run_id == parent.run_id and
                media.camera_id == parent.camera.camera_id and
                (media.run_key == parent.run_key or kind == 'clip' and media.run_key is None),
                ReferenceRejected, 'media ownership or kind mismatch')
        if natural is not None:
            require(FrozenJSON.of(media.natural()) == FrozenJSON.of(natural),
                    ReferenceRejected, 'supplied media differs from supporting payload')
        actual = one(cursor, 'media_object', 'media_key', key)
        equal_fields(actual, dict(org_id=media.org_id, camera_key=media.camera_key, run_key=media.run_key,
                    ts=media.ts, kind=kind, bucket=media.bucket, object_key=media.object_key,
                    content_type=media.content_type, bytes=media.bytes, width=media.width,
                    height=media.height, sha256=media.sha256))
        return dict(**media.natural(), media_key=key)

    def validate_references(self, cursor, entry, resolutions):
        require(isinstance(resolutions, Resolutions), ReferenceRejected, 'explicit resolution records required')
        parent = resolutions.run
        identity = entry.logical_identity.value()
        require([str(parent.org_id),parent.run_id,parent.camera.camera_id] == identity[:3],
                ReferenceRejected, 'run resolution differs from accepted identity')
        one(cursor, 'dim_org', 'org_id', parent.org_id)
        camera = one(cursor, 'dim_camera', 'camera_key', parent.camera.camera_key)
        equal_fields(camera, dict(org_id=parent.org_id,camera_id=parent.camera.camera_id,
                     timezone=parent.camera.timezone,valid_from=parent.camera.valid_from,valid_to=parent.camera.valid_to))
        cursor.execute('SELECT camera_key FROM public.dim_camera WHERE org_id=%s AND camera_id=%s AND valid_from<=%s AND (valid_to IS NULL OR valid_to>%s)',
                       (parent.org_id,parent.camera.camera_id,parent.started_at,parent.started_at))
        require(cursor.fetchall() == [(parent.camera.camera_key,)], ReferenceRejected, 'camera SCD2 resolution missing or ambiguous')
        run = one(cursor, 'dim_run', 'run_key', parent.run_key)
        equal_fields(run, dict(org_id=parent.org_id,run_id=parent.run_id,camera_key=parent.camera.camera_key,
                    lane_key=parent.lane_key,model_key=parent.detector.model_key,source_kind=parent.source_kind,
                    source_media_key=parent.source_media_key,started_at=parent.started_at,service_version=parent.service_version))
        lane = one(cursor, 'dim_lane', 'lane_key', parent.lane_key)
        equal_fields(lane,dict(name='vehicle',service='vehicle_service'))
        detector = one(cursor, 'dim_model', 'model_key', parent.detector.model_key)
        equal_fields(detector,dict(model_name=parent.detector.model_name,version=parent.detector.version,task='detect'))
        if parent.source_kind == 'file':
            require(parent.source_media_key is not None, ReferenceRejected, 'file run requires registered clip')
            self._media(cursor,parent.source_media_key,None,resolutions,parent,'clip')
        parent_sql = entry.construction.facts[0].sql.value()
        for fact in entry.construction.ready_rows:
            d, row = fact.delivery.value(), fact.sql.value()
            supporting_key=row.get('best_media_key') if fact.row_kind=='fact_vehicle' else row.get('crop_media_key')
            if supporting_key is not None:
                support=[m for m in resolutions.media if m.media_key==supporting_key]
                require(len(support)==1 and support[0].service_track_id==identity[3] and
                        support[0].frame_index==fact.diagnostic.value()['frame_index'],
                        ReferenceRejected,'supplied crop support belongs to another vehicle or frame')
            for timestamp in ([d['first_ts'],d['last_ts']] if fact.row_kind == 'fact_vehicle' else [d['ts']]):
                instant = parse_timestamp(timestamp)
                require(instant >= parent.started_at and instant >= camera['valid_from'] and
                        (camera['valid_to'] is None or instant < camera['valid_to']) and
                        (run['ended_at'] is None or instant <= run['ended_at']),
                        ReferenceRejected, 'support timestamp outside run or camera version')
            date = one(cursor,'dim_date','date_key',row['date_key'])
            require(date['date'].strftime('%Y%m%d') == str(row['date_key']) and row['date_key'] in parent.date_keys,
                    ReferenceRejected, 'calendar registration mismatch')
            lookup = dict(run=dict(org_id=str(parent.org_id),run_id=parent.run_id,camera_id=parent.camera.camera_id,
                timezone=parent.camera.timezone,run_key=parent.run_key,camera_key=parent.camera.camera_key,date_keys=list(parent.date_keys)),
                parent=parent_sql,media=None)
            if fact.row_kind == 'fact_vehicle':
                dim = one(cursor,'dim_class','class_key',row['class_key'])
                matches = [c for c in resolutions.classes if c.key == row['class_key'] and c.name == d['class']['name'] and c.approved and c.group == 'vehicle']
                require(len(matches)==1,ReferenceRejected,'explicit approved class mapping required')
                lookup['class'] = dict(name=dim['name'],class_group=dim['class_group'],class_key=dim['class_key'])
                lookup['media'] = self._media(cursor,row['best_media_key'],d['best_media'],resolutions,parent,'vehicle_crop')
            else:
                model = one(cursor,'dim_model','model_key',row['model_key'])
                expected = d['model'] if fact.row_kind == 'fact_vehicle_colour' else d['selected_attempt']['model']
                matches = [m for m in resolutions.models if m.key == row['model_key'] and m.identity.policy() == expected]
                require(len(matches)==1,ReferenceRejected,'explicit executed model mapping required')
                lookup['model'] = {k:model[k] for k in ('model_name','version','task','model_key')}
                is_colour = fact.row_kind == 'fact_vehicle_colour'
                name = d['colour'] if is_colour else d['selected_attempt']['plate_category']
                key = row['colour_key'] if is_colour else row['plate_category_key']
                table, field = ('dim_colour','colour_key') if is_colour else ('dim_plate_category','plate_category_key')
                if name is None:
                    lookup['catalogue'] = None
                else:
                    registry = resolutions.colours if is_colour else resolutions.plate_categories
                    require(len([v for v in registry if v.name==name and v.key==key and v.approved])==1,
                            ReferenceRejected,'explicit approved category mapping required')
                    dim = one(cursor,table,field,key)
                    lookup['catalogue'] = dict(name=dim['name'],key=dim[field],approved=True)
                if not is_colour:
                    lookup['media'] = self._media(cursor,row['crop_media_key'],d['selected_attempt']['crop_media'],resolutions,parent,'plate_crop')
            try:
                self.validation.snapshot(fact.snapshot(),lookup)
            except (ValueError,ValidationError):
                raise ReferenceRejected('registered snapshot semantics mismatch') from None

    def _verify_fact(self, cursor, fact):
        # Receipts establish exact equality; REAL values only verify row integrity.
        expected = fact.sql.value()
        cursor.execute(sql.SQL('SELECT {} FROM public.{} WHERE uid=%s').format(
            sql.SQL(',').join(map(sql.Identifier,expected)),sql.Identifier(fact.row_kind)),(expected['uid'],))
        actual = cursor.fetchone()
        require(actual is not None,HistoryMismatch,'receipt exists without analytical fact')
        for (key,want),got in zip(expected.items(),actual):
            if isinstance(got,UUID):got=str(got)
            if isinstance(got,datetime):want=parse_timestamp(want)
            if key in {'class_conf','conf','crop_score'} and want is not None:
                # Let PostgreSQL cast the exact same Decimal parameter as on
                # INSERT. Decimal -> Python double -> binary32 can double-round.
                cursor.execute('SELECT %s::real IS NOT DISTINCT FROM %s::real',(got,want))
                require(cursor.fetchone()==(True,),HistoryMismatch,'analytical REAL differs from accepted receipt')
            else:
                require(got==want,HistoryMismatch,'analytical fact differs from accepted receipt')

    def _apply(self, cursor, entry, payload):
        facts = entry.construction.ready_rows
        identity = entry.logical_identity.canonical
        org = entry.logical_identity.value()[0]
        lock_names = [b'vehicle:'+identity] + [b'uid:'+f.row_kind.encode()+b':'+f.sql.value()['uid'].encode() for f in facts]
        locks = sorted({int.from_bytes(sha256(name).digest()[:8], 'big', signed=True) for name in lock_names})
        for lock in locks:
            cursor.execute('SELECT pg_advisory_xact_lock(%s)',(lock,))
        cursor.execute('SELECT payload,digest FROM db1_delivery.acceptance WHERE org_id=%s AND logical_identity=%s',(org,identity))
        prior=cursor.fetchone()
        digest=sha256(payload).hexdigest()
        if prior:
            stored, stored_digest=bytes(prior[0]),prior[1]
            require(sha256(stored).hexdigest()==stored_digest,HistoryMismatch,'stored acceptance digest mismatch')
            require(stored==payload,DeliveryConflict,'accepted vehicle payload or decisions changed')
            for fact in facts:
                cursor.execute('SELECT logical_identity,delivery,projection,delivery_sha256,projection_sha256 FROM db1_delivery.row_receipt WHERE row_kind=%s AND uid=%s',
                               (fact.row_kind,fact.sql.value()['uid']))
                receipt=cursor.fetchone()
                require(receipt is not None and bytes(receipt[0])==identity and bytes(receipt[1])==fact.delivery.canonical and
                        bytes(receipt[2])==fact.sql.canonical and receipt[3]==fact.delivery.digest and receipt[4]==fact.sql.digest,
                        HistoryMismatch,'row receipt missing or inconsistent')
                self._verify_fact(cursor,fact)
            return 'identical'
        for fact in facts:
            uid=fact.sql.value()['uid']
            cursor.execute('SELECT 1 FROM db1_delivery.row_receipt WHERE row_kind=%s AND uid=%s',(fact.row_kind,uid))
            require(cursor.fetchone() is None,DeliveryConflict,'row identity already has acceptance history')
            cursor.execute(sql.SQL('SELECT 1 FROM public.{} WHERE uid=%s').format(sql.Identifier(fact.row_kind)),(uid,))
            require(cursor.fetchone() is None,HistoryMismatch,'existing fact has no trustworthy acceptance history')
        vehicle=facts[0].sql.value()
        cursor.execute('SELECT 1 FROM public.fact_vehicle WHERE run_key=%s AND track_id=%s',(vehicle['run_key'],vehicle['track_id']))
        require(cursor.fetchone() is None,HistoryMismatch,'vehicle exists without matching acceptance history')
        cursor.execute('INSERT INTO db1_delivery.acceptance(org_id,logical_identity,payload,digest) VALUES (%s,%s,%s,%s)',(org,identity,payload,digest))
        for fact in facts:  # Parent precedes children; all in the same transaction.
            row=fact.sql.value()
            cursor.execute(sql.SQL('INSERT INTO public.{} ({}) VALUES ({})').format(sql.Identifier(fact.row_kind),
                sql.SQL(',').join(map(sql.Identifier,row)),sql.SQL(',').join(sql.Placeholder() for _ in row)),tuple(row.values()))
            cursor.execute('INSERT INTO db1_delivery.row_receipt(org_id,logical_identity,row_kind,uid,delivery,projection,delivery_sha256,projection_sha256) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                (org,identity,fact.row_kind,row['uid'],fact.delivery.canonical,fact.sql.canonical,fact.delivery.digest,fact.sql.digest))
        return 'new'

    def deliver(self, connection, entry, *, tenant, resolutions):
        payload=self.prepare(entry,tenant)
        phase='transaction'
        try:
            if connection.closed:
                raise DatabaseDeliveryError(None,commit_outcome='not_committed')
            require(connection.autocommit and connection.info.transaction_status == TransactionStatus.IDLE,
                    InvalidAcceptance,'adapter requires idle autocommit connection; no caller transaction')
            require(160000<=connection.info.server_version<170000,InvalidAcceptance,'PostgreSQL 16 required')
            with connection.transaction():
                with connection.cursor(row_factory=tuple_row) as cursor:
                    # Exact supervisor RLS mechanism, scoped to this transaction.
                    cursor.execute("SELECT set_config('app.org_id', %s, true)",(str(tenant.org_id),))
                    cursor.execute("SELECT set_config('search_path','pg_catalog',true)")
                    cursor.execute("SELECT set_config('synchronous_commit','on',true)")
                    cursor.execute("SELECT set_config('lock_timeout',%s,true),set_config('statement_timeout',%s,true)",
                                   (str(self.lock_timeout_ms),str(self.statement_timeout_ms)))
                    cursor.execute('SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname IN (current_user,session_user)')
                    roles=cursor.fetchall()
                    require(bool(roles) and all(not superuser and not bypass for superuser,bypass in roles),
                            InvalidAcceptance,'normal NOBYPASSRLS session and execution role required')
                    cursor.execute("SELECT n.nspname,c.relname,c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE (n.nspname='public' AND c.relname=ANY(%s)) OR (n.nspname='db1_delivery' AND c.relname=ANY(%s))",
                                   (['dim_camera','dim_run','media_object',*KINDS],['acceptance','row_receipt']))
                    secured=cursor.fetchall()
                    require(len(secured)==9 and all(enabled and forced for _,_,enabled,forced in secured),
                            InvalidAcceptance,'required tenant tables must enable and force RLS')
                    cursor.execute('SHOW transaction_isolation')
                    require(cursor.fetchone()[0]=='read committed',InvalidAcceptance,'read committed isolation required for locked retry recheck')
                    cursor.execute('SELECT metadata FROM db1_delivery.compatibility WHERE singleton=true')
                    require(cursor.fetchone()==(self.compatibility,),InvalidAcceptance,'proposed acceptance schema compatibility mismatch')
                    self.validate_references(cursor,entry,resolutions)
                    disposition=self._apply(cursor,entry,payload)
                phase='commit'
            return DeliveryResult(disposition,sha256(payload).hexdigest(),len(entry.construction.ready_rows),
                                  len(entry.construction.facts)-len(entry.construction.ready_rows))
        except psycopg.errors.UniqueViolation:
            raise DeliveryConflict('persistent row identity collision; retain local entry') from None
        except psycopg.Error as error:
            raise DatabaseDeliveryError(error.sqlstate,commit_outcome='unknown' if phase=='commit' else 'not_committed') from None
