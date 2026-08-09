from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

from app.us_assignment.repository import register_assignment_source


def main() -> None:
    parser = argparse.ArgumentParser(description="Register a local USPTO assignment XML/ZIP source")
    parser.add_argument("path")
    parser.add_argument("--effective-date", required=True)
    parser.add_argument(
        "--source-kind",
        choices=["DAILY_ASSIGNMENT_XML", "ASSIGNMENT_SNAPSHOT_XML"],
        required=True,
    )
    args = parser.parse_args()
    package_id, inserted = register_assignment_source(
        Path(args.path),
        effective_date=date.fromisoformat(args.effective_date),
        source_kind=args.source_kind,
    )
    print(
        json.dumps(
            {
                "status": "REGISTERED",
                "package_id": package_id,
                "inserted": inserted,
                "effective_date": args.effective_date,
                "source_kind": args.source_kind,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
