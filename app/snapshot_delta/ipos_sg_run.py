"""Operator entry point for one Singapore IPOS snapshot acquisition cycle."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .acquisition import DataGovSgSnapshotDownloader
from .lifecycle import SnapshotCycleResult, run_ipos_snapshot_cycle


def cycle_result_payload(result: SnapshotCycleResult) -> dict[str, Any]:
    """Serialize one lifecycle result for operator-facing JSON output."""
    return {
        "status": result.status,
        "content_hash": result.manifest.content_hash,
        "row_count": result.manifest.row_count,
        "retrieved_at": result.manifest.retrieved_at.isoformat(),
        "event_count": result.event_count,
        "events_path": str(result.events_path) if result.events_path else None,
        "native_change_count": result.native_change_count,
        "native_changes_path": (
            str(result.native_changes_path) if result.native_changes_path else None
        ),
        "cleanup_pending_paths": [str(path) for path in result.cleanup_pending_paths],
        "storage_reference": result.manifest.storage_reference,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one Singapore IPOS snapshot cycle")
    parser.add_argument(
        "--state-dir",
        type=Path,
        required=True,
        help="Directory that stores the current snapshot pointer, snapshot, manifests, and deltas",
    )
    args = parser.parse_args()

    downloader = DataGovSgSnapshotDownloader(
        api_key=os.getenv("DATA_GOV_SG_API_KEY") or None
    )
    result = run_ipos_snapshot_cycle(args.state_dir, downloader=downloader)
    print(json.dumps(cycle_result_payload(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
