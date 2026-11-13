"""Explicit local durable acceptance only; no transport or evidence recovery.

SQLite owns transaction serialization. RowBuilder remains the payload retry
comparison authority; terminal omission decisions are immutable as well.
"""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import sqlite3
from jsonschema.exceptions import ValidationError
from uuid import UUID, uuid5

from contracts.db1 import reference, row_policies
from contracts.db1.reference import canonical_json_bytes, strict_loads
from .db1_rows import Construction, FactResult, FrozenJSON, RowBuilder, UUID5Policy
from .db1_rows.builder import KINDS
from .db1_delivery import AcceptedEntry, _pack


class OutboxError(RuntimeError):
    """Safe messages contain no payload, plate text or credentials."""


class StorageError(OutboxError):
    pass


class IncompatibleStore(OutboxError):
    pass


class CorruptStore(OutboxError):
    pass


class CapacityExceeded(OutboxError):
    pass


class AdmissionRejected(OutboxError):
    def __init__(self, proposal, *, code='CONSTRUCTION_INELIGIBLE'):
        super().__init__('construction rejected; evidence must remain pending')
        self.code = code
        self.proposal = proposal  # Explicit review only; never logged or persisted.


@dataclass(frozen=True, slots=True)
class Acceptance:
    entry: AcceptedEntry
    identical: bool


def logical_identity(completed):
    life = completed.vehicle.lifetime
    return FrozenJSON.of([str(life.context.org_id), life.context.run_id,
                          life.context.camera.camera_id, life.service_track_id])




def _same_decisions(old, new):
    # RowBuilder compares natural and SQL bytes. Omissions have no payload;
    # preserve their complete accepted terminal diagnostic decision separately.
    return all((a.delivery, a.sql) == (b.delivery, b.sql)
               and (a.sql is not None or (a.diagnostic, a.metadata, a.issues) ==
                    (b.diagnostic, b.metadata, b.issues))
               for a, b in zip(old.facts, new.facts))


class LocalOutbox:
    def __init__(self, *, path, namespace: UUID, validation,
                 max_entries=10000, max_payload_bytes=64 * 1024 * 1024,
                 lock_timeout=2.0):
        if not isinstance(namespace, UUID):
            raise ValueError('explicit stable UUID namespace required')
        if not isinstance(path, (str, Path)) or not str(path) or str(path) == ':memory:':
            raise ValueError('explicit durable filesystem path required')
        for value in (max_entries, max_payload_bytes):
            if type(value) is not int or value < 1:
                raise ValueError('positive entry and logical byte limits required')
        if isinstance(lock_timeout, bool) or not isinstance(lock_timeout, (int, float)) or not .001 <= lock_timeout <= 60:
            raise ValueError('lock timeout must be finite, in [0.001,60] seconds')
        self.path = Path(path).absolute()
        self.builder = RowBuilder(identity_policy=UUID5Policy(namespace), validation=validation)
        self.validation = validation
        self.max_entries, self.max_payload_bytes = max_entries, max_payload_bytes
        self.connection = None
        self.compatibility = canonical_json_bytes(dict(
            format='db1-local-outbox-v1', schema_version=1,
            identity_policy='UUID5Policy-v1', namespace=str(namespace),
            canonicalization='P1-exact-v1',
            row_schema_sha256=sha256(canonical_json_bytes(validation._row.schema)).hexdigest(),
            diagnostic_schema_sha256=sha256(canonical_json_bytes(validation._diagnostic.schema)).hexdigest(),
            reference_sha256=sha256(Path(reference.__file__).read_bytes()).hexdigest(),
            row_policies_sha256=sha256(Path(row_policies.__file__).read_bytes()).hexdigest(),
            builder_sha256=sha256(Path(__import__(RowBuilder.__module__, fromlist=['x']).__file__).read_bytes()).hexdigest()))
        try:
            self.connection = sqlite3.connect(str(self.path), timeout=lock_timeout, isolation_level=None)
            c = self.connection
            c.execute(f'PRAGMA busy_timeout={int(lock_timeout * 1000)}')
            mode = c.execute('PRAGMA journal_mode=WAL').fetchone()[0]
            c.execute('PRAGMA synchronous=FULL')
            c.execute('PRAGMA wal_autocheckpoint=1000')
            self.settings = dict(journal_mode=mode, synchronous=c.execute('PRAGMA synchronous').fetchone()[0],
                                 busy_timeout=c.execute('PRAGMA busy_timeout').fetchone()[0],
                                 wal_autocheckpoint=c.execute('PRAGMA wal_autocheckpoint').fetchone()[0])
            if mode != 'wal' or self.settings['synchronous'] != 2 or self.settings['busy_timeout'] != int(lock_timeout * 1000) or self.settings['wal_autocheckpoint'] != 1000:
                raise IncompatibleStore('required SQLite settings are not effective')
            if c.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise CorruptStore('SQLite integrity check failed')
            c.execute('BEGIN IMMEDIATE')
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if not tables:
                if c.execute('PRAGMA user_version').fetchone()[0] != 0:
                    raise IncompatibleStore('unexpected empty store version')
                c.execute('CREATE TABLE compatibility (id INTEGER PRIMARY KEY CHECK(id=1), metadata BLOB NOT NULL)')
                c.execute('CREATE TABLE accepted (sequence INTEGER PRIMARY KEY, identity BLOB NOT NULL UNIQUE, payload BLOB NOT NULL, digest TEXT NOT NULL, payload_bytes INTEGER NOT NULL CHECK(payload_bytes>0))')
                c.execute('INSERT INTO compatibility VALUES (1,?)', (self.compatibility,))
                c.execute('PRAGMA user_version=1')
            elif tables != {'compatibility', 'accepted'}:
                raise IncompatibleStore('unexpected local store tables')
            self._check_compatibility()
            # Validate every accepted record at reopening, streaming one at a time.
            for row in c.execute('SELECT sequence,identity,payload,digest,payload_bytes FROM accepted ORDER BY sequence'):
                self._decode(row)
            self._check_capacity(0, 0)
            c.commit()
        except sqlite3.Error as error:
            self.close()
            raise StorageError('SQLite opening failed') from error
        except BaseException:
            self.close()
            raise

    def _check_compatibility(self):
        if self.connection.execute('PRAGMA user_version').fetchone()[0] != 1:
            raise IncompatibleStore('unsupported local schema version')
        rows = self.connection.execute('SELECT id,metadata FROM compatibility').fetchall()
        if rows != [(1, self.compatibility)]:
            raise IncompatibleStore('identity, canonicalization or contract compatibility mismatch')

    def _validate_projection(self, fact, parent):
        # Reuse P1 equality/date/route/parent checks on retained mappings. These
        # lookup views assert stored consistency, not fresh platform registration.
        d, sql = fact.delivery.value(), fact.sql.value()
        lookup = dict(run=dict(org_id=d['org_id'], run_id=d['run_id'],
                              camera_id=d['camera_id'], timezone=d['camera_timezone'],
                              run_key=parent['run_key'], camera_key=parent['camera_key'],
                              date_keys=[sql['date_key']]), parent=parent, media=None)
        if fact.row_kind == 'fact_vehicle':
            lookup['class'] = dict(**d['class'], class_key=sql['class_key'])
            media, media_key = d['best_media'], sql['best_media_key']
        elif fact.row_kind == 'fact_vehicle_colour':
            lookup['model'] = dict(**d['model'], model_key=sql['model_key'])
            lookup['catalogue'] = dict(name=d['colour'], key=sql['colour_key'], approved=True) if d['colour'] is not None else None
            media, media_key = None, None
        else:
            attempt = d['selected_attempt']
            lookup['model'] = dict(**attempt['model'], model_key=sql['model_key'])
            lookup['catalogue'] = dict(name=attempt['plate_category'], key=sql['plate_category_key'], approved=True) if attempt['plate_category'] is not None else None
            media, media_key = attempt['crop_media'], sql['crop_media_key']
        lookup['media'] = dict(**media, media_key=media_key) if media is not None else None
        self.validation.snapshot(fact.snapshot(), lookup)

    def _validate_admission(self, proposal):
        if not isinstance(proposal, Construction) or tuple(f.row_kind for f in proposal.facts) != KINDS:
            raise CorruptStore('invalid construction shape')
        for fact in proposal.facts:
            diag = fact.diagnostic.value()
            self.validation.diagnostic(diag)
            if fact.metadata.value().get('quarantined') is not False:
                raise AdmissionRejected(proposal)
            if fact.retry in {'blocked', 'conflict'} or diag['record_type'] == 'quarantine' or diag.get('settlement', {}).get('incomplete'):
                raise AdmissionRejected(proposal, code='PAYLOAD_CONFLICT' if fact.retry == 'conflict' else 'CONSTRUCTION_INELIGIBLE')
            if fact.sql is not None:
                if fact.delivery is None or fact.retry not in {'new', 'identical'} or diag['record_type'] != 'row':
                    raise AdmissionRejected(proposal)
                self.validation._row.validate(fact.snapshot())
                if diag['payload_sha256'] != fact.delivery.digest or diag['row_uid'] != fact.delivery.value()['uid']:
                    raise CorruptStore('row diagnostic digest/identity mismatch')
            elif fact.delivery is not None or fact.retry != 'omitted' or diag['record_type'] != 'omission' or diag['reason'] == 'unsettled_operation':
                raise AdmissionRejected(proposal)
        if not proposal.facts[0].ready:
            raise AdmissionRejected(proposal)
        parent = proposal.facts[0].sql.value()
        for fact in proposal.ready_rows:
            self._validate_projection(fact, parent)

    def _decode(self, row):
        sequence, identity, payload, digest, size = row
        try:
            if type(sequence) is not int or sequence < 1 or type(payload) is not bytes or len(payload) != size or sha256(payload).hexdigest() != digest:
                raise ValueError('record envelope hash or size mismatch')
            data = strict_loads(payload)
            if canonical_json_bytes(data) != payload or set(data) != {'format', 'acceptance', 'identity', 'facts'} or data['format'] != 'db1-local-outbox-v1' or data['acceptance'] != 'accepted_pending_transport':
                raise ValueError('invalid envelope')
            logical = FrozenJSON.of(data['identity'])
            if logical.canonical != identity or len(data['identity']) != 4:
                raise ValueError('logical identity mismatch')
            facts = []
            for f in data['facts']:
                if set(f) != {'row_kind','delivery','sql','diagnostic','metadata','issues','retry','decision'}:
                    raise ValueError('fact shape mismatch')
                channels = {}
                for name in ('delivery','sql','diagnostic','metadata'):
                    channel = f[name]
                    if channel is None:
                        channels[name] = None
                        continue
                    if set(channel) != {'canonical','sha256'}:
                        raise ValueError('channel shape mismatch')
                    value = FrozenJSON(channel['canonical'].encode('utf-8'))
                    if value.digest != channel['sha256'] or canonical_json_bytes(strict_loads(value.canonical)) != value.canonical:
                        raise ValueError('channel hash/canonical mismatch')
                    channels[name] = value
                if channels['diagnostic'] is None or channels['metadata'] is None or not isinstance(f['issues'], list) or any(not isinstance(i,str) for i in f['issues']):
                    raise ValueError('invalid diagnostic/metadata/issues')
                fact = FactResult(f['row_kind'], **channels, issues=tuple(f['issues']), retry=f['retry'])
                if f['decision'] != ('row' if fact.ready else 'terminal_omission'):
                    raise ValueError('decision mismatch')
                if fact.delivery is not None:
                    natural = fact.delivery.value()
                    org, run, camera, track = logical.value()
                    expected_uid = str(uuid5(self.builder.identity_policy.namespace,
                                            canonical_json_bytes([org, camera, run, track, 'row', fact.row_kind]).decode()))
                    if natural['uid'] != expected_uid:
                        raise ValueError('identity policy mismatch')
                    if [natural[k] for k in ('org_id','run_id','camera_id','track_id')] != logical.value():
                        raise ValueError('fact parent mismatch')
                diag = fact.diagnostic.value()
                if [diag['run_id'], diag['track_id']] != [logical.value()[1], logical.value()[3]]:
                    raise ValueError('diagnostic owner mismatch')
                facts.append(fact)
            construction = Construction(tuple(facts))
            self._validate_admission(construction)
            return AcceptedEntry(sequence, logical, construction, size)
        except (ValueError, TypeError, KeyError, AttributeError, OutboxError, ValidationError) as error:
            raise CorruptStore('stored acceptance failed validation') from None

    def _load(self, identity):
        row = self.connection.execute('SELECT sequence,identity,payload,digest,payload_bytes FROM accepted WHERE identity=?', (identity.canonical,)).fetchone()
        return self._decode(row) if row else None

    def load(self, identity):
        """Recover immutable accepted proposals, never native crops/unfinished OCR."""
        try:
            self._check_compatibility()
            return self._load(identity)
        except sqlite3.Error as error:
            raise StorageError('SQLite read failed') from error

    def _check_capacity(self, extra_entries, extra_bytes):
        entries, size = self.connection.execute('SELECT COUNT(*),COALESCE(SUM(payload_bytes),0) FROM accepted').fetchone()
        if entries + extra_entries > self.max_entries or size + extra_bytes > self.max_payload_bytes:
            raise CapacityExceeded('local outbox logical capacity exhausted')

    def _before_commit(self):
        """Fault-injection seam; production implementation does nothing."""

    def _after_commit(self):
        """Fault-injection seam for durable commit/normal-return boundary."""

    def accept(self, completed, resolutions):
        """Construct/recheck/commit one vehicle atomically under the write lock."""
        c = self.connection
        try:
            c.execute('BEGIN IMMEDIATE')
            self._check_compatibility()
            identity = logical_identity(completed)
            prior = self._load(identity)
            proposal = self.builder.construct(completed, resolutions,
                                             prior=prior.construction if prior else None)
            self._validate_admission(proposal)
            if prior:
                if not _same_decisions(prior.construction, proposal):
                    raise AdmissionRejected(proposal, code='ACCEPTED_DECISION_CONFLICT')
                result = Acceptance(prior, True)
            else:
                payload = _pack(identity, proposal)
                self._check_capacity(1, len(payload))
                cursor = c.execute('INSERT INTO accepted(identity,payload,digest,payload_bytes) VALUES (?,?,?,?)',
                                   (identity.canonical, payload, sha256(payload).hexdigest(), len(payload)))
                result = Acceptance(self._decode((cursor.lastrowid, identity.canonical, payload,
                                                   sha256(payload).hexdigest(), len(payload))), False)
            self._before_commit()
            c.commit()
        except sqlite3.Error as error:
            if c.in_transaction:
                c.rollback()
            raise StorageError('SQLite acceptance failed; retry against durable history') from error
        except BaseException:
            if c.in_transaction:
                c.rollback()
            raise
        self._after_commit()
        return result

    def inspect_pending(self, *, after_sequence=0, limit=100):
        """Keyset page, max 1000 vehicles; no acknowledgment or deletion."""
        if type(after_sequence) is not int or after_sequence < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('nonnegative cursor and page limit 1..1000 required')
        try:
            self._check_compatibility()
            rows = self.connection.execute('SELECT sequence,identity,payload,digest,payload_bytes FROM accepted WHERE sequence>? ORDER BY sequence LIMIT ?', (after_sequence, limit)).fetchall()
            return tuple(self._decode(row) for row in rows)
        except sqlite3.Error as error:
            raise StorageError('SQLite inspection failed') from error

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()


class OutboxCompletionSink:
    def __init__(self, outbox, *, resolutions):
        if not isinstance(outbox, LocalOutbox) or not callable(resolutions):
            raise ValueError('explicit outbox and supplied resolution callback required')
        self.outbox, self.resolutions = outbox, resolutions

    def __call__(self, completed):
        # Returning means committed/recovered acceptance, not transport delivery.
        # Acknowledge/release exclusively belong to ObservationCoordinator.
        return self.outbox.accept(completed, self.resolutions(completed))
