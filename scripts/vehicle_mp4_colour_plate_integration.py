"""Owned native Redis with injected combined-video execution; skips exit 2."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromName('vehicle.common.tests.test_mp4_colour_plate_redis_integration')
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(2 if result.skipped else 0 if result.wasSuccessful() else 1)
