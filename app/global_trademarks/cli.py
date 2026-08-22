import argparse
import json
import uuid
from datetime import date
from pathlib import Path

from app.global_trademarks.acceptance import evaluate_manifest_data_trust
from app.global_trademarks.catalog import COUNTRY_SOURCES
from app.global_trademarks.diagnostics import collect_readiness_audit
from app.global_trademarks.execution import (
    ExecutionAlreadyRunning,
    global_trademark_execution_lock,
)
from app.global_trademarks.ingest_runs import IngestRunState, get_ingest_run_state
from app.global_trademarks.manifest import SourceManifest
from app.global_trademarks.migrations import (
    assert_global_trademark_schema,
    global_trademark_migration_status,
    migrate_global_trademark_schema,
)
from app.global_trademarks.operator import IngestPlan, build_ingest_plan, register_plan_source
from app.global_trademarks.preflight import (
    SourcePreflight,
    inspect_au_ipgod,
    inspect_ca_st96,
    inspect_gb_2018,
    inspect_tm_link,
)
from app.global_trademarks.runtime_adapters import runtime_registry
from app.trademark_framework.registry import resolve_pipeline_id
from app.trademark_framework.runtime import RuntimeRequest, SourceRuntimeAdapter


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


def _selector(value: str) -> tuple[str, str]:
    key, separator, raw_value = value.partition("=")
    key = key.strip()
    raw_value = raw_value.strip()
    if not separator or not key or not raw_value:
        raise argparse.ArgumentTypeError("selector must use KEY=VALUE")
    return key, raw_value


def _selector_metadata(values: list[tuple[str, str]] | None) -> dict[str, object]:
    metadata: dict[str, object] = {}
    for key, value in values or []:
        if key in metadata:
            raise ValueError(f"duplicate selector key: {key}")
        metadata[key] = value
    return metadata


def _add_apply_controls(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually mutate the database. Without this flag the command is a no-write plan.",
    )
    parser.add_argument(
        "--max-records",
        type=int,
        help=(
            "Bound this invocation to at most N newly committed records. A bounded stop "
            "remains resumable and is not marked COMPLETE until a later invocation reaches EOF."
        ),
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


def _runtime_request_for_args(
    args: argparse.Namespace,
) -> tuple[SourceRuntimeAdapter, RuntimeRequest]:
    registry = runtime_registry()
    if args.command == "ingest-source":
        try:
            metadata = _selector_metadata(args.selector)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from exc
        adapter = registry.for_source(args.jurisdiction, args.source_id)
        request = adapter.request_from_source(
            jurisdiction=args.jurisdiction,
            source_id=args.source_id,
            path=args.path,
            metadata=metadata,
            max_records=args.max_records,
        )
        return adapter, request

    adapter = registry.for_command(args.command)
    return adapter, adapter.request_from_command(args.command, vars(args))


def _plan_for_request(
    args: argparse.Namespace,
    request: RuntimeRequest,
    preflight: SourcePreflight,
) -> IngestPlan:
    return build_ingest_plan(
        command=args.command,
        jurisdiction=request.jurisdiction,
        source_id=request.source_id,
        path=request.path,
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
        parser_version=request.parser_version,
        max_records=request.max_records,
    )


def _pipeline_id_for_request(request: RuntimeRequest) -> str:
    pipeline_id = resolve_pipeline_id(
        request.jurisdiction,
        request.source_id,
        request.metadata,
    )
    if pipeline_id is None:
        raise RuntimeError(
            "jurisdiction framework could not resolve intended pipeline for "
            f"{request.jurisdiction}:{request.source_id} metadata={dict(request.metadata)!r}"
        )
    return pipeline_id


def _print_apply_result(
    *,
    before_run: IngestRunState | None,
    after_run: IngestRunState,
    manifest: SourceManifest,
    max_records: int | None,
) -> None:
    before_rows = before_run.rows_committed if before_run else 0
    processed_rows = after_run.rows_committed - before_rows
    if processed_rows < 0:
        raise RuntimeError("ingest run cumulative row count moved backwards")
    print(
        json.dumps(
            {
                "status": "COMPLETE" if after_run.complete else "PARTIAL",
                "processed_rows": processed_rows,
                "cumulative_committed_rows": after_run.rows_committed,
                "ingest_run_status": after_run.status,
                "max_records": max_records,
                "bounded_apply": max_records is not None,
                "net_inserted_rows": None,
                "manifest": manifest.as_dict(),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


def _configure_ingest_commands(sub: argparse._SubParsersAction) -> None:
    registry = runtime_registry()

    gb_adapter = registry.for_command("ingest-gb-2018")
    gb = sub.add_parser(
        "ingest-gb-2018",
        help="Compatibility command for UKIPO 2018 ingestion; requires --apply for writes",
    )
    gb.add_argument("--path", required=True, type=_path)
    gb.add_argument(
        "--stream",
        required=True,
        choices=gb_adapter.selector_choices["source_stream"],
    )
    _add_apply_controls(gb)

    tm_link_adapter = registry.for_command("ingest-tm-link")
    tm_link = sub.add_parser(
        "ingest-tm-link",
        help="Compatibility command for TM-Link EU/NZ seed ingestion",
    )
    tm_link.add_argument("--path", required=True, type=_path)
    tm_link.add_argument("--jurisdiction", required=True, choices=("EU", "NZ"))
    tm_link.add_argument(
        "--table",
        required=True,
        choices=tm_link_adapter.selector_choices["source_table"],
    )
    _add_apply_controls(tm_link)

    au_adapter = registry.for_command("ingest-au-ipgod")
    au = sub.add_parser(
        "ingest-au-ipgod",
        help="Compatibility command for IPGOD ingestion",
    )
    au.add_argument("--path", required=True, type=_path)
    au.add_argument(
        "--table",
        required=True,
        choices=au_adapter.selector_choices["source_table"],
    )
    _add_apply_controls(au)

    ca_adapter = registry.for_command("ingest-ca-st96")
    ca = sub.add_parser(
        "ingest-ca-st96",
        help="Compatibility command for CIPO ST.96 ingestion",
    )
    ca.add_argument("--path", required=True, type=_path)
    ca.add_argument(
        "--source-id",
        default="CIPO_GLOBAL_2025_06_14",
        choices=tuple(key.source_id for key in ca_adapter.source_keys),
    )
    _add_apply_controls(ca)

    generic = sub.add_parser(
        "ingest-source",
        help=(
            "Generic runtime-adapter ingestion entrypoint. Source-specific selectors use "
            "repeatable --selector KEY=VALUE; no database mutation occurs without --apply."
        ),
    )
    generic.add_argument("--jurisdiction", required=True)
    generic.add_argument("--source-id", required=True)
    generic.add_argument("--path", required=True, type=_path)
    generic.add_argument("--selector", action="append", type=_selector)
    _add_apply_controls(generic)


def main() -> int:
    parser = argparse.ArgumentParser(description="Country-native trademark store administration")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("catalog", help="Print the configured jurisdiction/source plan")
    sub.add_parser("schema-status", help="Read-only audit of country-store schema and ingest state")
    sub.add_parser("migrate", help="Install or safely upgrade country-native PostgreSQL schemas")

    preflight = sub.add_parser(
        "preflight-source",
        help="Legacy no-write source-file preflight; generic ingest-source also preflights by default",
    )
    preflight.add_argument("--path", required=True, type=_path)
    preflight.add_argument(
        "--kind",
        required=True,
        choices=("GB_2018", "CA_ST96", "TM_LINK", "AU_IPGOD"),
    )
    preflight.add_argument("--jurisdiction", choices=("EU", "NZ"))
    preflight.add_argument("--table")
    preflight.add_argument("--sample-limit", type=int, default=100)

    acceptance = sub.add_parser(
        "accept-manifest",
        help="Read-only release acceptance and Data Trust evaluation for one source manifest",
    )
    acceptance.add_argument("--manifest-id", required=True, type=_uuid)
    acceptance.add_argument("--required-coverage-through", type=_iso_date)

    _configure_ingest_commands(sub)
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
    if args.max_records is not None and args.max_records < 1:
        parser.error("--max-records must be at least 1")

    try:
        adapter, runtime_request = _runtime_request_for_args(args)
    except (ValueError, argparse.ArgumentTypeError) as exc:
        parser.error(str(exc))

    source_preflight = adapter.preflight(runtime_request)
    plan = _plan_for_request(args, runtime_request, source_preflight)
    if not args.apply:
        payload = plan.as_dict(apply_requested=False)
        payload["runtime_adapter_id"] = adapter.adapter_id
        payload["runtime_metadata"] = dict(runtime_request.metadata)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if source_preflight.schema_valid else 2

    if not source_preflight.schema_valid:
        payload = plan.as_dict(apply_requested=True)
        payload["runtime_adapter_id"] = adapter.adapter_id
        payload["runtime_metadata"] = dict(runtime_request.metadata)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 2

    assert_global_trademark_schema()
    try:
        with global_trademark_execution_lock(plan.execution_scope):
            ingest_metadata = dict(runtime_request.metadata)
            source_object_id, manifest = register_plan_source(
                plan,
                metadata=ingest_metadata,
            )
            pipeline_id = _pipeline_id_for_request(runtime_request)
            before_run = get_ingest_run_state(
                source_object_id=source_object_id,
                pipeline_id=pipeline_id,
            )
            adapter.execute(runtime_request)
            after_run = get_ingest_run_state(
                source_object_id=source_object_id,
                pipeline_id=pipeline_id,
            )
            if after_run is None:
                raise RuntimeError("ingest command completed without a durable ingest run state")
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

    _print_apply_result(
        before_run=before_run,
        after_run=after_run,
        manifest=manifest,
        max_records=args.max_records,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
