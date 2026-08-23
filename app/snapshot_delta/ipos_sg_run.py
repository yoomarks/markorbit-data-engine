"""Operator entry point for one Singapore IPOS snapshot acquisition cycle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .lifecycle import run_ipos_snapshot_cycle


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one Singapore IPOS snapshot cycle")
    parser.add_argument(
        "--state-dir",
        type=Path,
        required=True,
        help="Directory that stores the current snapshot pointer, snapshot, manifests, and deltas",
    )
    args = parser.parse_args()

    result = run_ipos_snapshot_cycle(args.state_dir)
    print(
        json.dumps(
            {
                "status": result.status,
                "content_hash": result.manifest.content_hash,
                "row_count": result.manifest.row_count,
                "retrieved_at": result.manifest.retrieved_at.isoformat(),
                "event_count": result.event_count,
                "events_path": str(result.events_path) if result.events_path else None,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
