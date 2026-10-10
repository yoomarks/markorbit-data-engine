from __future__ import annotations

import argparse
import json

from app.tmclass.schema import migrate_tmclass_schema, tmclass_schema_status


def main() -> None:
    parser = argparse.ArgumentParser(description="TMclass fact-store operator")
    parser.add_argument("command", choices=("migrate", "status"))
    args = parser.parse_args()
    status = migrate_tmclass_schema() if args.command == "migrate" else tmclass_schema_status()
    print(
        json.dumps(
            {
                "component": "TMCLASS_FACT_STORE",
                "installed_version": status.installed_version,
                "expected_version": "TMCLASS_FACT_STORE_V1",
                "ready": status.ready,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
