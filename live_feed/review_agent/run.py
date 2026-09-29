"""CLI entry point for the review agent.

    python -m live_feed.review_agent.run --once          # first camera, one pass
    python -m live_feed.review_agent.run --loop --cam 1  # persistent loop for ch1

Camera is selected via --cam (default: first camera in CHANNELS env).
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_VS_DIR = Path(__file__).parent.parent.parent
if str(_VS_DIR) not in sys.path:
    sys.path.insert(0, str(_VS_DIR))


def _main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    parser = argparse.ArgumentParser(description="Review agent CLI")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--once", action="store_true", help="Run one pass and exit")
    group.add_argument("--loop", action="store_true", help="Run persistent loop")
    parser.add_argument("--cam", default=None, help="Camera id (default: first camera in CHANNELS)")
    args = parser.parse_args()

    from live_feed.config import CAMERAS
    from live_feed.review_agent.worker import loop, run_pass

    cam_id = args.cam if args.cam is not None else CAMERAS[0]["id"]

    if args.once:
        n = run_pass(cam_id)
        print(f"review_agent --once: {n} records reviewed on ch{cam_id}")
        sys.exit(0)
    else:
        loop(cam_id)


if __name__ == "__main__":
    _main()
