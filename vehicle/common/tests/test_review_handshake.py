"""Deterministic reproductions of the two reviewed host defects."""
from types import SimpleNamespace
from unittest.mock import Mock,patch
import unittest
from uuid import uuid4
from contracts.db1.reference import canonical_json_bytes
from vehicle.common.db1_rows.core import Blocked
from vehicle.common.redis_scalar.worker import load_request_codec
from vehicle.common.redis_scalar.synthetic import ORG,IDENTITY_NAMESPACE
from vehicle.common.redis_scalar.journal import Journal
from vehicle.common.plate_requests.host import V2Publisher,DeliveryPlan
from vehicle.common.tests.test_plate_requests import session_fixture,complete_loop


class CompletionReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.codec=load_request_codec(ORG,IDENTITY_NAMESPACE)
    def make(self,after_append):
        s,crop,*_=session_fixture(self.codec);state,_,terminal=complete_loop(self.codec,s)
        self.addCleanup(lambda:(s.adapter.stop(),s.adapter.cleanup(discard=True)))
        sealed=self.codec.decode(s.sealed)
        class Interleaving:
            prepared=False
            def hget(client,key,field):
                if field=='pending':
                    client.prepared=True
                    return None # PREPARE occurs immediately after this old read
                if field.startswith('seen:'):return __import__('hashlib').sha256(sealed.canonical).hexdigest().encode()
                return canonical_json_bytes(state)
            def xrange(client,*args,**kwargs):return [(b'7-0',{b'json':terminal})] if after_append else []
            def eval(client,*args):
                client.prepared=True
                return [0] # atomic observer sees unfinished PREPARE/append
        closed=SimpleNamespace(owner=SimpleNamespace(service_track_id=1,lifetime=s.adapter.lifetime))
        p=object.__new__(V2Publisher);p.attempts=0;p.confirmed=set();p.codec=self.codec
        p.engine=SimpleNamespace(audit=SimpleNamespace(calls={}))
        p.journal=Journal(None,namespace='review',trusted_org=ORG,role='plate_requests',consumer='engine')
        p.client=Interleaving();p.writer=SimpleNamespace(confirmed={'sealed-1'},entries={'sealed-1':('9-0','engine.sealed.v2',s.sealed)})
        plan=DeliveryPlan(closed,s,b'',b'');p.prepare=lambda c:plan;p.pump=lambda:None
        return p,closed,s,crop
    def test_prepare_between_old_reads_before_append_is_waiting(self):
        p,c,s,crop=self.make(False)
        with patch('vehicle.common.plate_requests.host.reference'):
            self.assertFalse(p.publish(c))
        self.assertFalse(crop.released);self.assertFalse(s.adapter.confirmed)
    def test_prepare_between_old_reads_after_append_cannot_release(self):
        p,c,s,crop=self.make(True)
        with patch('vehicle.common.plate_requests.host.reference'):
            self.assertFalse(p.publish(c))
        self.assertFalse(crop.released);self.assertFalse(s.adapter.confirmed)
    def test_foreign_partition_constructor_has_zero_side_effects(self):
        client=Mock();engine=Mock();engine.limits.max_scalar_bytes=8*1024*1024
        journal=Journal(None,namespace='review',trusted_org=uuid4(),role='plate_requests',consumer='engine')
        with self.assertRaises(Blocked):V2Publisher(client,journal,self.codec,engine)
        self.assertEqual(client.mock_calls,[]);engine.plate.prepare.assert_not_called()
