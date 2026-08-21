import argparse
import json
from pathlib import Path

from app.global_trademarks.au_ipgod import (
    ingest_application,
    ingest_application_classification,
    ingest_application_description,
    ingest_application_events,
    ingest_application_links,
    ingest_party_activity,
)
from app.global_trademarks.ca_st96 import ingest_cipo_st96_core
from app.global_trademarks.catalog import COUNTRY_SOURCES
from app.global_trademarks.gb_open_data import ingest_ukipo_2018
from app.global_trademarks.ingest_schema import ensure_seed_ingest_schema
from app.global_trademarks.tm_link_seed import (
    ingest_tm_link_applicants,
    ingest_tm_link_applications,
    ingest_tm_link_classes,
    ingest_tm_link_details,
)


_TM_LINK_LOADERS = {
    "applications": ingest_tm_link_applications,
    "applicants": ingest_tm_link_applicants,
    "details": ingest_tm_link_details,
    "classes": ingest_tm_link_classes,
}

_AU_LOADERS = {
    "application": ingest_application,
    "party-activity": ingest_party_activity,
    "application-links": ingest_application_links,
    "application-events": ingest_application_events,
    "application-classification": ingest_application_classification,
    "application-description": ingest_application_description,
}


def _path(value: str) -> Path:
    path = Path(value)
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"file does not exist: {value}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Country-native trademark store administration")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("catalog", help="Print the configured jurisdiction/source plan")
    sub.add_parser("migrate", help="Install or safely upgrade country-native PostgreSQL schemas")

    gb = sub.add_parser("ingest-gb-2018", help="Ingest one UKIPO 2018 TXT source")
    gb.add_argument("--path", required=True, type=_path)
    gb.add_argument("--stream", required=True, choices=("DOMESTIC", "MADRID_IR"))

    tm_link = sub.add_parser("ingest-tm-link", help="Ingest one TM-Link EU/NZ seed table")
    tm_link.add_argument("--path", required=True, type=_path)
    tm_link.add_argument("--jurisdiction", required=True, choices=("EU", "NZ"))
    tm_link.add_argument("--table", required=True, choices=tuple(_TM_LINK_LOADERS))

    au = sub.add_parser("ingest-au-ipgod", help="Ingest one IPGOD 2022 trade mark table")
    au.add_argument("--path", required=True, type=_path)
    au.add_argument("--table", required=True, choices=tuple(_AU_LOADERS))

    ca = sub.add_parser("ingest-ca-st96", help="Ingest CIPO ST.96 core records from XML/ZIP")
    ca.add_argument("--path", required=True, type=_path)
    ca.add_argument(
        "--source-id",
        default="CIPO_GLOBAL_2025_06_14",
        choices=("CIPO_GLOBAL_2025_06_14", "CIPO_WEEKLY"),
    )

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
                        "pipeline_ready": source.pipeline_ready,
                        "notes": source.notes,
                    }
                    for source in plan.sources
                ],
            }
            for jurisdiction, plan in COUNTRY_SOURCES.items()
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if args.command == "migrate":
        ensure_seed_ingest_schema()
        print("GLOBAL_TRADEMARK_COUNTRY_SCHEMAS_READY")
        return 0

    if args.command == "ingest-gb-2018":
        count = ingest_ukipo_2018(args.path, source_stream=args.stream)
    elif args.command == "ingest-tm-link":
        count = _TM_LINK_LOADERS[args.table](args.path, jurisdiction=args.jurisdiction)
    elif args.command == "ingest-au-ipgod":
        count = _AU_LOADERS[args.table](args.path)
    else:
        count = ingest_cipo_st96_core(args.path, source_id=args.source_id)

    print(json.dumps({"status": "COMPLETE", "rows": count}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
