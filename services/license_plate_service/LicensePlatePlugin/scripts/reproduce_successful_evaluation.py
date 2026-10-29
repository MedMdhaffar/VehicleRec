#!/usr/bin/env python3
"""Run the frozen leave-one-out reproduction through the installed package."""

from __future__ import annotations

import argparse
from pathlib import Path

from license_plate_plugin.config import load_config
from license_plate_plugin.evaluation import run_leave_one_out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--authoritative-csv", type=Path, required=True)
    args = parser.parse_args()
    summary = run_leave_one_out(
        load_config(args.config),
        args.output_dir.resolve(),
        args.authoritative_csv.resolve(),
    )
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

