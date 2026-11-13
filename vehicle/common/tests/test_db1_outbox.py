"""Synthetic registrations, temporary SQLite and real child-process faults only."""
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import UUID

from contracts.db1.reference import canonical_json_bytes, strict_loads
from vehicle.common.db1_rows import ContractValidation, FrozenJSON
from vehicle.common.db1_outbox import (LocalOutbox, OutboxCompletionSink, AdmissionRejected,
                                      CapacityExceeded, CorruptStore, IncompatibleStore,
                                      StorageError, logical_identity)
from vehicle.common.observation_coordinator import ObservationCoordinator, PendingCleanup
from vehicle.common.vehicle_observation import VehicleClosure
from vehicle.common.tests.test_db1_rows import fixture, ROOT, NAMESPACE


def validation():
    return ContractValidation(json.loads((ROOT/'contracts/db1/rows.v0.schema.json').read_text()),
                              json.loads((ROOT/'contracts/db1/diagnostics.v1.schema.json').read_text()))


def changed(completed):
    return replace(completed, vehicle=replace(completed.vehicle, type_confidence=Decimal('.7')))


def second(completed):
    life = replace(completed.vehicle.lifetime, service_track_id=2)
    return replace(completed, vehicle=replace(completed.vehicle, lifetime=life), consumer_evidence=())


def worker(path, mode):
    completed, resolutions = fixture()
    with LocalOutbox(path=path, namespace=NAMESPACE, validation=validation(), lock_timeout=1) as store:
        if mode in {'concurrent_same', 'concurrent_changed'}:
            print('READY', flush=True)
            sys.stdin.readline()
        if mode == 'concurrent_changed':
            completed = changed(completed)
        if mode == 'kill_before_commit':
            store._before_commit = lambda: os.kill(os.getpid(), signal.SIGKILL)
        if mode == 'kill_after_commit':
            store._after_commit = lambda: os.kill(os.getpid(), signal.SIGKILL)
        try:
            result = store.accept(completed, resolutions)
            print('IDENTICAL' if result.identical else 'ACCEPTED', flush=True)
        except AdmissionRejected:
            print('REJECTED', flush=True)


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'outbox.sqlite'
        self.completed, self.res = fixture()

    def open(self, **kwargs):
        store = LocalOutbox(path=self.path, namespace=kwargs.pop('namespace', NAMESPACE),
                            validation=kwargs.pop('validation', validation()), **kwargs)
        self.addCleanup(store.close)
        return store

    def children(self, *modes):
        children = []
        for mode in modes:
            child = subprocess.Popen([sys.executable, '-m', __name__, '--worker', str(self.path), mode],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, env={**os.environ, 'PYTHONDONTWRITEBYTECODE':'1'}, cwd=ROOT)
            children.append(child)
            self.addCleanup(lambda p=child: p.kill() if p.poll() is None else None)
            self.assertEqual(child.stdout.readline().strip(), 'READY')
        for child in children:
            child.stdin.write('GO\n'); child.stdin.flush()
        results = []
        for child in children:
            out, err = child.communicate(timeout=15)
            self.assertEqual(child.returncode, 0, err)
            results.append(out.strip())
        return results

    def test_accept_exact_reload_atomic_facts_and_omission(self):
        completed = replace(self.completed, vehicle=replace(self.completed.vehicle,
                           type_confidence=Decimal('0.12345678901234567890123456781')))
        store = self.open()
        result = store.accept(completed, self.res)
        self.assertFalse(result.identical)
        original = result.entry
        self.assertEqual(len(original.construction.ready_rows), 3)
        self.assertEqual(original.construction.facts[-1].retry, 'omitted')
        self.assertEqual(store.settings, dict(journal_mode='wal', synchronous=2,
                         busy_timeout=2000, wal_autocheckpoint=1000))
        store.close()
        store = self.open()
        recovered = store.load(logical_identity(completed))
        self.assertEqual(recovered, original)
        self.assertEqual(recovered.construction.facts[0].sql.value()['class_conf'],
                         Decimal('0.12345678901234567890123456781'))
        plate = recovered.construction.facts[2]
        self.assertIsNone(plate.sql.value()['crop_media_key'])
        self.assertIsNone(plate.sql.value()['crop_score'])
        self.assertEqual(plate.delivery.value()['ts'], self.completed.vehicle.lifetime.first_receipt.utc.isoformat(timespec='microseconds').replace('+00:00','Z'))

    def test_identical_and_conflicting_retries_after_reopen(self):
        store = self.open(); original = store.accept(self.completed, self.res).entry; store.close()
        store = self.open()
        retry = store.accept(self.completed, self.res)
        self.assertTrue(retry.identical); self.assertEqual(retry.entry, original)
        with self.assertRaises(AdmissionRejected) as error:
            store.accept(changed(self.completed), self.res)
        self.assertTrue(any(f.retry == 'conflict' for f in error.exception.proposal.facts))
        self.assertEqual(store.inspect_pending(), (original,))
        with self.assertRaises(AdmissionRejected):
            store.accept(replace(self.completed, consumer_evidence=()), self.res)
        self.assertEqual(store.inspect_pending(), (original,))

    def test_unresolved_blocked_unsettled_quarantine_rejected(self):
        store = self.open()
        plate = dict(self.completed.consumer_evidence)['plate']
        unsettled = replace(plate, outcome='omitted', omission_reason='unsettled_operation',
                            selected=None, ledger=(replace(plate.ledger[0], status='settlement_timeout',
                            execution_unknown=True),), settlement_incomplete=True)
        candidates = [(self.completed, None), (changed(self.completed), replace(self.res, models=())),
                      (replace(self.completed, consumer_evidence=(('plate', unsettled),)), self.res),
                      (replace(self.completed, vehicle=replace(self.completed.vehicle,
                       lifetime=replace(self.completed.vehicle.lifetime, quarantined=True))), self.res)]
        for completed, res in candidates:
            with self.subTest(case=candidates.index((completed,res))):
                with self.assertRaises(AdmissionRejected): store.accept(completed, res)
                self.assertEqual(store.inspect_pending(), ())

    def test_first_terminal_omissions_accepted_with_parent(self):
        store = self.open()
        completed = replace(self.completed, consumer_evidence=())
        entry = store.accept(completed, self.res).entry
        self.assertEqual(len(entry.construction.ready_rows),1)
        self.assertEqual([f.retry for f in entry.construction.facts[1:]], ['omitted']*3)
        self.assertTrue(store.accept(completed,self.res).identical)
        # An accepted omission is a terminal decision; adding analytical output
        # later is rejected, even though RowBuilder ignores payload-free history.
        with self.assertRaises(AdmissionRejected) as rejected: store.accept(self.completed,self.res)
        self.assertEqual(rejected.exception.code,'ACCEPTED_DECISION_CONFLICT')

    def test_failure_before_commit_rolls_back_all_three_rows(self):
        store = self.open()
        with patch.object(store,'_before_commit',side_effect=RuntimeError('injected before commit')):
            with self.assertRaises(RuntimeError): store.accept(self.completed,self.res)
        self.assertEqual(store.inspect_pending(),())
        self.assertEqual(len(store.accept(self.completed,self.res).entry.construction.ready_rows),3)

    def test_real_process_kill_before_and_after_commit(self):
        for mode, count in (('kill_before_commit',0),('kill_after_commit',1)):
            with self.subTest(boundary=mode):
                if self.path.exists():
                    for path in self.path.parent.glob('outbox.sqlite*'):path.unlink()
                child = subprocess.run([sys.executable,'-m',__name__,'--worker',str(self.path),mode],
                                       capture_output=True,timeout=15,cwd=ROOT,
                                       env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
                self.assertEqual(child.returncode,-signal.SIGKILL,child.stderr)
                store = self.open()
                self.assertEqual(len(store.inspect_pending()),count)
                acceptance=store.accept(self.completed,self.res)
                self.assertEqual(acceptance.identical,bool(count))
                self.assertEqual(len(store.inspect_pending()),1)
                store.close()

    def test_commit_then_callback_error_recovers_one_acceptance(self):
        store = self.open(); sink = OutboxCompletionSink(store,resolutions=lambda c:self.res)
        with patch.object(store,'_after_commit',side_effect=RuntimeError('injected after commit before return')):
            with self.assertRaises(RuntimeError): sink(self.completed)
        self.assertEqual(len(store.inspect_pending()),1)
        self.assertTrue(sink(self.completed).identical)
        self.assertEqual(len(store.inspect_pending()),1)

    def test_concurrent_identical_submissions(self):
        self.assertEqual(sorted(self.children('concurrent_same','concurrent_same')),['ACCEPTED','IDENTICAL'])
        self.assertEqual(len(self.open().inspect_pending()),1)

    def test_concurrent_conflicting_submissions(self):
        self.assertEqual(sorted(self.children('concurrent_same','concurrent_changed')),['ACCEPTED','REJECTED'])
        self.assertEqual(len(self.open().inspect_pending()),1)

    def test_capacity_and_identical_retry_at_capacity(self):
        store = self.open(max_entries=1)
        first=store.accept(self.completed,self.res)
        with self.assertRaises(CapacityExceeded):store.accept(second(self.completed),self.res)
        self.assertTrue(store.accept(self.completed,self.res).identical)
        self.assertEqual(store.inspect_pending(),(first.entry,))
        store.close()
        store=self.open(max_payload_bytes=first.entry.stored_bytes)
        with self.assertRaises(CapacityExceeded):store.accept(second(self.completed),self.res)
        store.close()
        with self.assertRaises(CapacityExceeded):self.open(max_payload_bytes=first.entry.stored_bytes-1)

    def test_byte_capacity_rejects_first_acceptance(self):
        store=self.open(max_payload_bytes=1)
        with self.assertRaises(CapacityExceeded):store.accept(self.completed,self.res)
        self.assertEqual(store.inspect_pending(),())

    def test_bounded_lock_timeout(self):
        store=self.open(lock_timeout=.05)
        other=sqlite3.connect(self.path,isolation_level=None)
        self.addCleanup(other.close);other.execute('BEGIN IMMEDIATE')
        import time
        start=time.monotonic()
        with self.assertRaises(StorageError):store.accept(self.completed,self.res)
        elapsed=time.monotonic()-start
        self.assertGreaterEqual(elapsed,.04);self.assertLess(elapsed,1)
        other.rollback()
        self.assertEqual(store.inspect_pending(),())

    def test_paginated_pending_entries_are_not_consumed(self):
        store=self.open()
        first=store.accept(self.completed,self.res).entry
        other=store.accept(second(self.completed),self.res).entry
        self.assertEqual(store.inspect_pending(limit=1),(first,))
        self.assertEqual(store.inspect_pending(after_sequence=first.sequence,limit=1),(other,))
        self.assertEqual(store.inspect_pending(after_sequence=other.sequence),())
        self.assertEqual(len(store.inspect_pending()),2)
        for limit in (0,1001,True):
            with self.assertRaises(ValueError):store.inspect_pending(limit=limit)

    def test_incompatible_namespace_schema_policy_and_version(self):
        store=self.open();store.accept(self.completed,self.res);store.close()
        with self.assertRaises(IncompatibleStore):self.open(namespace=UUID(int=1))
        v=validation();v._row.schema['description']='incompatible schema revision'
        with self.assertRaises(IncompatibleStore):self.open(validation=v)
        with patch('vehicle.common.db1_outbox.Path.read_bytes',return_value=b'incompatible policy'):
            with self.assertRaises(IncompatibleStore):self.open()
        with sqlite3.connect(self.path) as c:c.execute('PRAGMA user_version=2')
        with self.assertRaises(IncompatibleStore):self.open()

    def test_corrupted_record_rejected_on_read_and_reopen(self):
        store=self.open();store.accept(self.completed,self.res)
        store.connection.execute("UPDATE accepted SET digest='invalid'")
        with self.assertRaises(CorruptStore):store.inspect_pending()
        store.close()
        with self.assertRaises(CorruptStore):self.open()

    def test_corrupted_inner_hash_or_schema_or_parent_with_rehashed_envelope(self):
        for defect in ('hash','schema','parent','decision','sql_outcome'):
            with self.subTest(defect=defect):
                for path in self.path.parent.glob('outbox.sqlite*'):path.unlink()
                store=self.open();store.accept(self.completed,self.res)
                row=store.connection.execute('SELECT payload FROM accepted').fetchone()
                data=strict_loads(row[0]);fact=data['facts'][2]
                if defect=='hash':fact['delivery']['sha256']='b'*64
                elif defect=='decision':fact['decision']='terminal_omission'
                else:
                    name='sql' if defect=='sql_outcome' else 'delivery'
                    value=strict_loads(fact[name]['canonical'])
                    if defect=='schema':value['unexpected']=1
                    elif defect=='parent':value['track_id']=2
                    else:value['outcome']='unreadable'
                    frozen=FrozenJSON.of(value)
                    fact[name]=dict(canonical=frozen.canonical.decode(),sha256=frozen.digest)
                payload=canonical_json_bytes(data)
                store.connection.execute('UPDATE accepted SET payload=?,digest=?,payload_bytes=?',
                                         (payload,sha256(payload).hexdigest(),len(payload)))
                with self.assertRaises(CorruptStore):store.inspect_pending()
                store.close()

    def coordinator(self, store, *, resolutions=True):
        completed=self.completed
        class Owner:
            pipeline=SimpleNamespace(lifecycle=SimpleNamespace(context=completed.vehicle.lifetime.context))
            def __init__(self):self.pending=[completed.vehicle];self.acks=0
            def claim_closure_delivery(self,owner):pass
            def pending_finalized_observations(self,**kwargs):return tuple(self.pending)
            def acknowledge_finalized_observations(self,ids,**kwargs):self.acks+=1;self.pending=[]
        class Consumer:
            context=completed.vehicle.lifetime.context
            def __init__(self,evidence):self.evidence=evidence;self.finalizations=0;self.releases=0
            def claim_result_delivery(self,owner):pass
            def finalize_vehicle(self,vehicle,**kwargs):self.finalizations+=1;return self.evidence
            def release_finalized(self,id,**kwargs):self.releases+=1
        owner=Owner();consumers={name:Consumer(e) for name,e in completed.consumer_evidence}
        sink=OutboxCompletionSink(store,resolutions=lambda c:self.res if resolutions else None)
        coordinator=ObservationCoordinator(owner,consumers,on_completed=sink)
        return owner,consumers,coordinator,sink

    def test_coordinator_rejection_retains_evidence(self):
        store=self.open();owner,consumers,coord,sink=self.coordinator(store,resolutions=False)
        with self.assertRaises(AdmissionRejected):coord.flush_closures()
        self.assertEqual(owner.acks,0);self.assertEqual(len(coord._completed),1)
        self.assertEqual([c.releases for c in consumers.values()],[0,0])
        with self.assertRaises(PendingCleanup):coord.drain_completed()
        self.assertEqual(store.inspect_pending(),())

    def test_coordinator_cleanup_failure_does_not_reaccept(self):
        store=self.open();owner,consumers,coord,sink=self.coordinator(store)
        consumer=consumers['plate']
        with patch.object(consumer,'release_finalized',side_effect=RuntimeError('injected release failure')), \
             patch.object(store,'accept',wraps=store.accept) as accept:
            with self.assertRaises(RuntimeError):coord.flush_closures()
            self.assertEqual(accept.call_count,1)
            self.assertEqual(len(store.inspect_pending()),1)
        with patch.object(store,'accept',side_effect=AssertionError('must not reaccept')):
            coord.flush_closures()
        self.assertEqual(len(coord._completed),0)
        self.assertEqual([c.finalizations for c in consumers.values()],[1,1])
        self.assertEqual([c.releases for c in consumers.values()],[1,1])

    def test_coordinator_commit_then_callback_error_retries_without_duplicates(self):
        store=self.open();owner,consumers,coord,sink=self.coordinator(store)
        with patch.object(store,'_after_commit',side_effect=RuntimeError('injected commit then error')):
            with self.assertRaises(RuntimeError):coord.flush_closures()
        self.assertEqual(owner.acks,0);self.assertEqual(len(coord._completed),1)
        coord.flush_closures();coord.flush_closures()
        self.assertEqual(len(store.inspect_pending()),1)
        self.assertEqual(owner.acks,1)
        self.assertEqual([c.finalizations for c in consumers.values()],[1,1])

    def test_unsettled_and_clock_reasons_remain_distinct(self):
        store=self.open()
        plate=dict(self.completed.consumer_evidence)['plate']
        attempt=replace(plate.ledger[0],status='settlement_timeout',execution_unknown=True)
        unsettled=replace(plate,ledger=(attempt,),selected=None,outcome='omitted',
                          omission_reason='unsettled_operation',settlement_incomplete=True)
        completed=replace(self.completed,consumer_evidence=(('plate',unsettled),))
        with self.assertRaises(AdmissionRejected) as rejected:store.accept(completed,self.res)
        diag=rejected.exception.proposal.facts[2].diagnostic.value()
        self.assertEqual(diag['reason'],'unsettled_operation')
        self.assertTrue(diag['settlement']['incomplete'])
        life=replace(self.completed.vehicle.lifetime,closure=VehicleClosure.CLOCK_DISCONTINUITY,quarantined=True)
        completed=replace(self.completed,vehicle=replace(self.completed.vehicle,lifetime=life),consumer_evidence=())
        with self.assertRaises(AdmissionRejected) as rejected:store.accept(completed,self.res)
        self.assertEqual(rejected.exception.proposal.facts[0].diagnostic.value()['reason'],'clock_discontinuity')
        self.assertEqual(store.inspect_pending(),())

    def test_unresolved_development_rejected_with_supplied_records(self):
        context=self.completed.vehicle.lifetime.context
        context=replace(context,environment='development',camera=replace(context.camera,camera_key=None),
                        resolution=replace(context.resolution,run_key=None,lane_key=None,source_media_key=None,
                        detector=replace(context.resolution.detector,model_key=None),synthetic=False))
        life=replace(self.completed.vehicle.lifetime,context=context)
        completed=replace(self.completed,vehicle=replace(self.completed.vehicle,lifetime=life),consumer_evidence=())
        store=self.open()
        with self.assertRaises(AdmissionRejected) as rejected:
            store.accept(completed,replace(self.res,run=replace(self.res.run,synthetic=False)))
        self.assertIn('UNRESOLVED_DEVELOPMENT',rejected.exception.proposal.facts[0].issues[0])
        self.assertEqual(store.inspect_pending(),())

    def test_reject_corrupt_prior_without_overwriting(self):
        store=self.open();store.accept(self.completed,self.res)
        store.connection.execute("UPDATE accepted SET digest='corrupted'")
        with self.assertRaises(CorruptStore):store.accept(self.completed,self.res)
        self.assertEqual(store.connection.execute('SELECT digest FROM accepted').fetchone()[0],'corrupted')

    def test_explicit_path_namespace_limits_and_storage_errors(self):
        for path in ('', ':memory:'):
            with self.assertRaises(ValueError):LocalOutbox(path=path,namespace=NAMESPACE,validation=validation())
        with self.assertRaises(ValueError):self.open(namespace='not UUID')
        for timeout in (0,float('inf'),float('nan'),True):
            with self.assertRaises(ValueError):self.open(lock_timeout=timeout)
        with self.assertRaises(StorageError):LocalOutbox(path=self.path/'no-parent',namespace=NAMESPACE,validation=validation())


if __name__ == '__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--worker':worker(sys.argv[2],sys.argv[3])
    else:unittest.main()
