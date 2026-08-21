import argparse
import json

from app.global_trademarks.catalog import COUNTRY_SOURCES
from app.global_trademarks.schema import ensure_country_trademark_schemas


def main() -> int:
    parser = argparse.ArgumentParser(description="Country-native trademark store administration")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("catalog", help="Print the configured jurisdiction/source plan")
    sub.add_parser("migrate", help="Install additive country-native PostgreSQL schemas")
    args = parser.parse_args()

    if args.command == "catalog":
        payload = {
            jurisdiction: {
                "store_schema": plan.store_schema,
                "sources": [
                    {
                        "source_id": source.source_id,
                        "role": source.role.value,
                        "authoritative": source.authoritative,
                        "active_now": source.active_now,
                        "notes": source.notes,
                    }
                    for source in plan.sources
                ],
            }
            for jurisdiction, plan in COUNTRY_SOURCES.items()
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    ensure_country_trademark_schemas()
    print("GLOBAL_TRADEMARK_COUNTRY_SCHEMAS_READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
