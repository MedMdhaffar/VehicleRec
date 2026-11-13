"""Real CPU Plate crop smoke; private Redis only; no downloads/provider route."""
import argparse
import os
from pathlib import Path
import socket
import sys
from uuid import UUID
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ocr-dir',required=True);p.add_argument('--marker',required=True)
    p.add_argument('--crops',required=True,nargs='+');p.add_argument('--output',required=True)
    p.add_argument('--org',required=True,type=UUID);p.add_argument('--identity-namespace',required=True,type=UUID)
    p.add_argument('--namespace',required=True);p.add_argument('--run-id',required=True)
    p.add_argument('--admission-seconds',required=True,type=float)
    args=p.parse_args()
    os.environ['OPENROUTER_ENABLED']='false'
    os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK']='True'
    os.environ['YOLO_AUTOINSTALL']='false'
    os.environ['YOLO_CONFIG_DIR']=str(Path(args.output).resolve()/'ultralytics-settings')
    # Only this Engine host is guarded; Redis workers receive explicit private
    # Unix URLs and no model initialization. Native downloads/providers cannot
    # silently use network during initialization, prediction or cleanup.
    original=socket.socket.connect
    def connect(self,address):
        if self.family in (socket.AF_INET,socket.AF_INET6):raise RuntimeError('NETWORK_DISABLED_LOCAL_SMOKE')
        return original(self,address)
    socket.socket.connect=connect
    from vehicle.common.plate_crop_smoke import run
    try:
        result=run(args)
        print('PLATE_CROP_SMOKE '+result['status'])
        return 0 if result['status']=='passed' else 2
    except Exception as error:
        print('PLATE_CROP_SMOKE_FAILED '+type(error).__name__,file=sys.stderr)
        return 2


if __name__=='__main__':raise SystemExit(main())
