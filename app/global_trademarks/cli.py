import argparse
import json
import uuid
from datetime import date
from pathlib import Path

from app.global_trademarks.acceptance import evaluate_manifest_data_trust
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
from app.global_trademarks.diagnostics import collect_readiness_audit
from app.global_trademarks.execution import (
    ExecutionAlreadyRunning,
    global_trademark_execution_lock,
)
from app.global_trademarks.gb_open_data import ingest_ukipo_2018
from app.global_trademarks.manifest import SourceManifest
from app.global_trademarks.migrations import (
    assert_global_trademark_schema,
    global_trademark_migration_status,
    migrate_global_trademark_schema,
)
from app.global_trademarks.operator import build_ingest_plan, register_plan_source
from app.global_trademarks.preflight import (
    SourcePreflight,
    inspect_au_ipgod,
    inspect_ca_st96,
    inspect_gb_2018,
    inspect_tm_link,
)
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


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected ISO date YYYY-MM-DD") from exc


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected UUID") from exc


def _add_apply_controls(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually mutate the database. Without this flag the command is a no-write plan.",
    )
    parser.add_argument("--manifest-key")
    parser.add_argument("--source-period-start", type=_iso_date)
    parser.add_argument("--source-period-end", type=_iso_date)
    parser.add_argument("--source-sequence", type=int, default=0)
    parser.add_argument("--source-precedence", type=int, default=0)
    parser.add_argument("--expected-objects", type=int, default=1)
    parser.add_argument("--part-sequence", type=int, default=1)
    parser.add_argument("--predecessor-manifest-key")
    parser.add_argument("--baseline-manifest-key")


def _preflight_for_ingest(args: argparse.Namespace) -> SourcePreflight:
    if args.command == "ingest-gb-2018":
        return inspect_gb_2018(args.path)
    if args.command == "ingest-tm-link":
        return inspect_tm_link(
            args.path,
            jurisdiction=args.jurisdiction,
            table=args.table,
        )
    if args.command == "ingest-au-ipgod":
        return inspect_au_ipgod(args.path, table=args.table)
    return inspect_ca_st96(args.path)


def _plan_for_ingest(args: argparse.Namespace, preflight: SourcePreflight):
    if args.command == "ingest-gb-2018":
        jurisdiction = "GB"
        source_id = "UKIPO_OPEN_DATA_2018"
        parser_version = "UKIPO_2018_V1"
    elif args.command == "ingest-tm-link":
        jurisdiction = args.jurisdiction
        source_id = f"TM_LINK_{args.jurisdiction}"
        parser_version = "TM_LINK_SEED_V1"
    elif args.command == "ingest-au-ipgod":
        jurisdiction = "AU"
        source_id = "IPGOD_2022"
        parser_version = "IPGOD_2022_V1"
    else:
        jurisdiction = "CA"
        source_id = args.source_id
        parser_version = "CIPO_ST96_CORE_V1"

    return build_ingest_plan(
        command=args.command,
        jurisdiction=jurisdiction,
        source_id=source_id,
        path=args.path,
        preflight=preflight,
        manifest_key=args.manifest_key,
        source_period_start=args.source_period_start,
        source_period_end=args.source_period_end,
        source_sequence=args.source_sequence,
        source_precedence=args.source_precedence,
        expected_objects=args.expected_objects,
        part_sequence=args.part_sequence,
        predecessor_manifest_key=args.predecessor_manifest_key,
        baseline_manifest_key=args.baseline_manifest_key,
        parser_version=parser_version,
    )


def _ingest_metadata(args: argparse.Namespace) -> dict[str, object]:
    if args.command == "ingest-gb-2018":
        return {"source_stream": args.stream}
    if args.command in {"ingest-tm-link", "ingest-au-ipgod"}:
        return {"source_table": args.table}
    return {"source_kind": "CIPO_ST96_CORE"}


def _execute_ingest(args: argparse.Namespace) -> int:
    if args.command == "ingest-gb-2018":
        return ingest_ukipo_2018(args.path, source_stream=args.stream)
    if args.command == "ingest-tm-link":
        return _TM_LINK_LOADERS[args.table](args.path, jurisdiction=args.jurisdiction)
    if args.command == "ingest-au-ipgod":
        return _AU_LOADERS[args.table](args.path)
    return ingest_cipo_st96_core(args.path, source_id=args.source_id)


def _print_apply_result(*, count: int, manifest: SourceManifest) -> None:
    print(
        json.dumps(
            {
                "status": "COMPLETE",
                "processed_rows": count,
                "net_inserted_rows": None,
                "manifest": manifest.as_dict(),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Country-native trademark store administration")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("catalog", help="Print the configured jurisdiction/source plan")
    sub.add_parser("schema-status", help="Read-only audit of country-store schema and ingest state")
    sub.add_parser("migrate", help="Install or safely upgrade country-native PostgreSQL schemas")

    preflight = sub.add_parser("preflight-source", help="Validate one source file without database writes")
    preflight.add_argument("--path", required=True, type=_path)
    preflight.add_argument("--kind", required=True, choices=("GB_2018", "CA_ST96", "TM_LINK", "AU_IPGOD"))
    preflight.add_argument("--jurisdiction", choices=("EU", "NZ"))
    preflight.add_argument("--table")
    preflight.add_argument("--sample-limit", type=int, default=100)

    acceptance = sub.add_parser(
        "accept-manifest",
        help="Read-only release acceptance and Data Trust evaluation for one source manifest",
    )
    acceptance.add_argument("--manifest-id", required=True, type=_uuid)
    acceptance.add_argument("--required-coverage-through", type=_iso_date)

    gb = sub.add_parser(
        "ingest-gb-2018",
        help="Plan UKIPO 2018 ingestion; requires --apply for database mutation",
    )
    gb.add_argument("--path", required=True, type=_path)
    gb.add_argument("--stream", required=True, choices=("DOMESTIC", "MADRID_IR"))
    _add_apply_controls(gb)

    tm_link = sub.add_parser(
        "ingest-tm-link",
        help="Plan TM-Link EU/NZ seed ingestion; requires --apply for database mutation",
    )
    tm_link.add_argument("--path", required=True, type=_path)
    tm_link.add_argument("--jurisdiction", required=True, choices=("EU", "NZ"))
    tm_link.add_argument("--table", required=True, choices=tuple(_TM_LINK_LOADERS))
    _add_apply_controls(tm_link)

    au = sub.add_parser(
        "ingest-au-ipgod",
        help="Plan IPGOD ingestion; requires --apply for database mutation",
    )
    au.add_argument("--path", required=True, type=_path)
    au.add_argument("--table", required=True, choices=tuple(_AU_LOADERS))
    _add_apply_controls(au)

    ca = sub.add_parser(
        "ingest-ca-st96",
        help="Plan CIPO ST.96 ingestion; requires --apply for database mutation",
    )
    ca.add_argument("--path", required=True, type=_path)
    ca.add_argument(
        "--source-id",
        default="CIPO_GLOBAL_2025_06_14",
        choices=("CIPO_GLOBAL_2025_06_14", "CIPO_WEEKLY"),
    )
    _add_apply_controls(ca)

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

    if args.command == "schema-status":
        payload = collect_readiness_audit().as_dict()
        payload["migration"] = global_trademark_migration_status().as_dict()
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if args.command == "preflight-source":
        if args.sample_limit <= 0:
            parser.error("--sample-limit must be positive")
        if args.kind == "GB_2018":
            result = inspect_gb_2018(args.path, sample_limit=args.sample_limit)
        elif args.kind == "CA_ST96":
            result = inspect_ca_st96(args.path, sample_limit=args.sample_limit)
        elif args.kind == "TM_LINK":
            if not args.jurisdiction or not args.table:
                parser.error("TM_LINK preflight requires --jurisdiction and --table")
            result = inspect_tm_link(
                args.path,
                jurisdiction=args.jurisdiction,
                table=args.table,
                sample_limit=args.sample_limit,
            )
        else:
            if not args.table:
                parser.error("AU_IPGOD preflight requires --table")
            result = inspect_au_ipgod(args.path, table=args.table, sample_limit=args.sample_limit)
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result.schema_valid else 2

    if args.command == "migrate":
        status = migrate_global_trademark_schema()
        print(json.dumps(status.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if status.ready else 2

    if args.command == "accept-manifest":
        manifest_acceptance, trust = evaluate_manifest_data_trust(
            args.manifest_id,
            required_coverage_through=args.required_coverage_through,
        )
        print(
            json.dumps(
                {
                    "acceptance": manifest_acceptance.as_dict(),
                    "data_trust": trust.as_dict(),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if manifest_acceptance.release_accepted else 2

    if args.source_sequence < 0 or args.source_precedence < 0:
        parser.error("--source-sequence and --source-precedence must be non-negative")
    if args.expected_objects is not None and args.expected_objects < 1:
        parser.error("--expected-objects must be at least 1")
    if args.part_sequence is not None and args.part_sequence < 1:
        parser.error("--part-sequence must be at least 1")

    source_preflight = _preflight_for_ingest(args)
    plan = _plan_for_ingest(args, source_preflight)
    if not args.apply:
        print(
            json.dumps(
                plan.as_dict(apply_requested=False),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if source_preflight.schema_valid else 2

    if not source_preflight.schema_valid:
        print(
            json.dumps(
                plan.as_dict(apply_requested=True),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 2

    assert_global_trademark_schema()
    try:
        with global_trademark_execution_lock(plan.execution_scope):
            _source_object_id, manifest = register_plan_source(
                plan,
                metadata=_ingest_metadata(args),
            )
            count = _execute_ingest(args)
    except ExecutionAlreadyRunning as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "reason": "ALREADY_RUNNING",
                    "message": str(exc),
                    "execution_scope": plan.execution_scope,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 3

    _print_apply_result(count=count, manifest=manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
