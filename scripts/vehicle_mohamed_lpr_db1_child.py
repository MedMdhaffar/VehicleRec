"""Explicit CUDA half of the owned Phase 5 test; DB credentials never enter it."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--control-dir',type=Path,required=True)
    p.add_argument('--identity-namespace',required=True)
    args,remaining=p.parse_known_args()
    require_control=args.control_dir.resolve()
    if not require_control.is_dir() or require_control.stat().st_mode & 0o077:
        p.error('private owned control directory required')
    def hook(*,engine,models,live,plate,owned,report):
        from vehicle.common.mohamed_lpr_delivery import OCRBinding,TunisDeliveryCodec
        from vehicle.common.mohamed_lpr_delivery_stream import TunisDeliveryWorker
        from vehicle.common.observation_evidence import RecognitionIdentity
        from vehicle.common.redis_scalar.worker import load_codec
        provenance=models.expected('registration')
        binding=OCRBinding(provenance.model_ref,RecognitionIdentity(provenance.model_id+':'+provenance.model_ref,1,'ocr'))
        codec=TunisDeliveryCodec(base=load_codec(live.scope.org_id,args.identity_namespace),scope=live.scope,bindings=(binding,))
        contexts={}
        for session,slot in live.slots.items():
            bound=slot.adapters[slot.engine.crop.generation].bound
            sid=bound.resource.binding.candidate.identity.service_track_id
            expected=tuple(k for k,s in live.slots.items() if s.engine.crop.vehicle_lifetime_id==slot.engine.crop.vehicle_lifetime_id)
            contexts[session]=dict(bound=bound,closed=engine.pending[sid],expected_sessions=expected)
        namespace=live.journal.prefix.split(':')[0]
        worker=TunisDeliveryWorker(owned.client,codec=codec,namespace=namespace,contexts=contexts,
            proof_reader=plate.journal.read_state,lease_ms=60000)
        worker.setup()
        for _ in range(len(contexts)+2):worker.step()
        key=worker.journal.key('db1.delivery.v1')
        if owned.client.xlen(key)!=len(contexts):raise RuntimeError('projection_gap_or_delivery_count')
        control=dict(scope=asdict(live.scope),namespace=namespace,bindings=[asdict(binding)],
            redis_socket=str(owned.socket),identity_namespace=args.identity_namespace,
            source_media=dict(sha256=report['clip_sha256'],width=int(report['source_metadata']['width']),
                              height=int(report['source_metadata']['height'])))
        path=require_control/'ready.json'
        path.write_text(json.dumps(control));os.chmod(path,0o600)
        deadline=time.monotonic()+120
        done=require_control/'done.json'
        while not done.exists() and time.monotonic()<deadline:time.sleep(.01)
        if not done.exists():raise RuntimeError('db1_consumer_deadline')
        result=json.loads(done.read_text())
        if result.get('status')!='PASS':raise RuntimeError('owned_db1_consumer_failed')
        worker.journal.release()
        return result
    from scripts.vehicle_mohamed_lpr_redis_mp4 import main as run
    return run(remaining,review_hook=hook)


if __name__=='__main__':raise SystemExit(main())
