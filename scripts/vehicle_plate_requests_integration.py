"""Owned native Redis v2 request/recovery tests; unavailable native runtime exits 2."""
from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromName('vehicle.common.tests.test_plate_requests_redis')
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(2 if result.skipped else 0 if result.wasSuccessful() else 1)
