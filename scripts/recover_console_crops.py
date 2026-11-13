"""Explicit offline display-only recovery from a retained console MP4."""
import argparse
import json
from pathlib import Path
import sys
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vehicle.common.console_crops import recover_manufacturer_mp4

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_id', type=UUID)
    args = parser.parse_args()
    print(json.dumps(recover_manufacturer_mp4(ROOT/'vehicle/media/t1-console'/str(args.run_id))))
