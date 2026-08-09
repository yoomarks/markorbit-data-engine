from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path

from app.us_ttab.repository import register_ttab_source


def main() -> None:
    parser = argparse.ArgumentParser(description="Register an authoritative TTABVUE XML snapshot")
    parser.add_argument("path", type=Path)
    parser.add_argument("--snapshot-at", required=True, help="Timezone-aware ISO-8601 timestamp")
    parser.add_argument(
        "--source-kind",
        default="TTABVUE_PROCEEDING_RAWXML_SNAPSHOT",
    )
    args = parser.parse_args()
    snapshot_at = datetime.fromisoformat(args.snapshot_at.replace("Z", "+00:00"))
    package_id, inserted = register_ttab_source(
        args.path,
        snapshot_at=snapshot_at,
        source_kind=args.source_kind,
    )
    print(json.dumps({"package_id": package_id, "inserted": inserted}, indent=2))


if __name__ == "__main__":
    main()
