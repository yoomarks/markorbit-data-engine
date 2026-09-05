from __future__ import annotations

from datetime import date
import re
from pathlib import Path
from typing import Any, Callable
import uuid

from app.us.target_bulk_journal import (
    initialize_bulk_journal,
    load_bulk_journal,
    mark_bulk_blocked,
    mark_bulk_running,
    mark_package_complete,
    mark_package_final_verified,
)
from app.us.target_bulk_plan import (
    ACCEPTED_SCHEMA_MANIFEST_SHA256,
    validate_bulk_plan,
    validate_stage2_anchor,
)
from app.us.target_canary import (
    APPLICATION_CANARY_TABLES,
    STAGE_DATABASE,
    TARGET_DATABASE,
    FrozenCanaryPackage,
    WslNativeClickHouseClient,
    assert_package_unchanged,
    build_target_schema_manifest,
    freeze_package,
    package_column_for_table,
    stage_ddl_from_manifest,
    stage_package_rows,
    stage_table_map,
    validate_target_schema_manifest,
    write_receipt,
)
from app.us.target_canary_journal import (
    commit_staged_tables,
    initialize_canary_journal,
    load_canary_journal,
    mark_stage_complete,
    mark_stage_started,
)


BULK_EXECUTOR_VERSION = "US_APPLICATION_TARGET_BULK_EXECUTOR_V1"
BULK_ACCEPTED_DECISION = "BOUNDED_US_APPLICATION_BULK_REPLAY_RANGE_COMPLETE"
_STAGE_NAME = re.compile(r"^[A-Za-z0-9_]+__[0-9a-f]{16}$")


def _query_single(client: WslNativeClickHouseClient, sql: str) -> int:
    rows = client.query(sql).result_rows
    if len(rows) != 1 or len(rows[0]) != 1:
        raise RuntimeError("US target bulk query returned unexpected single-value shape")
    return int(rows[0][0])


def _target_table_names_sql() -> str:
    return ",".join("'" + table.split(".", 1)[1] + "'" for table in APPLICATION_CANARY_TABLES)


def _read_target_manifest(client: WslNativeClickHouseClient) -> dict[str, object]:
    rows = client.query(
        f"SELECT name,create_table_query FROM system.tables WHERE database='{TARGET_DATABASE}' "
        f"AND name IN ({_target_table_names_sql()}) ORDER BY name"
    ).result_rows
    if len(rows) != len(APPLICATION_CANARY_TABLES):
        raise RuntimeError(
            "US target bulk requires the accepted Application target schema: "
            f"expected={len(APPLICATION_CANARY_TABLES)} actual={len(rows)}"
        )
    show_create: dict[str, str] = {}
    for row in rows:
        if len(row) != 2:
            raise RuntimeError("US target bulk schema query returned unexpected row shape")
        show_create[f"{TARGET_DATABASE}.{row[0]}"] = str(row[1])
    manifest = build_target_schema_manifest(show_create)
    validate_target_schema_manifest(manifest)
    if str(manifest.get("sha256") or "").lower() != ACCEPTED_SCHEMA_MANIFEST_SHA256:
        raise RuntimeError("US target bulk target schema manifest drifted")
    return manifest


def _final_package_counts(
    client: WslNativeClickHouseClient,
    package_id: uuid.UUID,
) -> dict[str, int]:
    result: dict[str, int] = {}
    for table in APPLICATION_CANARY_TABLES:
        package_column = package_column_for_table(table)
        result[table] = _query_single(
            client,
            f"SELECT count() FROM {table} "
            f"WHERE {package_column}=toUUID('{package_id}')",
        )
    return result


def _verify_storage(client: WslNativeClickHouseClient) -> dict[str, int]:
    final_non_hot = _query_single(
        client,
        f"SELECT count() FROM system.parts WHERE active AND database='{TARGET_DATABASE}' "
        f"AND table IN ({_target_table_names_sql()}) AND disk_name!='hot_us'",
    )
    stage_non_hot = _query_single(
        client,
        f"SELECT count() FROM system.parts WHERE active AND database='{STAGE_DATABASE}' "
        "AND disk_name!='hot_us'",
    )
    warm_cn = _query_single(
        client,
        "SELECT count() FROM system.parts WHERE active AND disk_name='warm_cn'",
    )
    if final_non_hot:
        raise RuntimeError(f"US target Application parts escaped hot_us: {final_non_hot}")
    if stage_non_hot:
        raise RuntimeError(f"US target bulk stage parts escaped hot_us: {stage_non_hot}")
    if warm_cn:
        raise RuntimeError(f"US target bulk observed unexpected active warm_cn parts: {warm_cn}")
    return {
        "final_non_hot_active_parts": final_non_hot,
        "stage_non_hot_active_parts": stage_non_hot,
        "warm_cn_active_parts": warm_cn,
    }


def _frozen_from_plan(item: dict[str, Any]) -> FrozenCanaryPackage:
    effective_text = str(item.get("source_effective_date") or "")
    if not effective_text:
        raise RuntimeError(f"planned source effective date missing: {item.get('file_name')}")
    return freeze_package(
        Path(str(item["path"])),
        expected_size=int(item["size_bytes"]),
        expected_sha256=str(item["sha256"]),
        package_kind=str(item["package_kind"]),
        source_rank=int(item["source_rank"]),
        source_effective_date=date.fromisoformat(effective_text),
        package_id=uuid.UUID(str(item["package_id"])),
    )


def _stage_table_short_names(package: FrozenCanaryPackage) -> list[str]:
    return [table.split(".", 1)[1] for table in stage_table_map(package).values()]


def _stage_table_count(
    client: WslNativeClickHouseClient,
    package: FrozenCanaryPackage,
) -> int:
    names = ",".join("'" + name + "'" for name in _stage_table_short_names(package))
    return _query_single(
        client,
        f"SELECT count() FROM system.tables WHERE database='{STAGE_DATABASE}' "
        f"AND name IN ({names})",
    )


class BulkTargetClient(WslNativeClickHouseClient):
    """Target client with one extra mutation: exact package-scoped staging DROP."""

    def drop_bulk_stage_table(self, table: str) -> None:
        database, dot, short = table.partition(".")
        if not dot or database != STAGE_DATABASE:
            raise ValueError("bulk stage cleanup is limited to markorbit_canary_stage")
        if not _STAGE_NAME.fullmatch(short):
            raise ValueError("bulk stage cleanup table name is not package-scoped")
        allowed_prefixes = {
            target.split(".", 1)[1] + "__" for target in APPLICATION_CANARY_TABLES
        }
        if not any(short.startswith(prefix) for prefix in allowed_prefixes):
            raise ValueError("bulk stage cleanup table is not an Application staging table")
        self._exec(f"DROP TABLE IF EXISTS {table}")


def _expected_counts_from_canary(journal: dict[str, Any]) -> dict[str, int]:
    commits = journal.get("commits")
    if not isinstance(commits, dict) or set(commits) != set(APPLICATION_CANARY_TABLES):
        raise RuntimeError("US target canary COMPLETE commit set is invalid")
    counts: dict[str, int] = {}
    for table in APPLICATION_CANARY_TABLES:
        item = commits[table]
        if not isinstance(item, dict) or item.get("status") != "COMMITTED":
            raise RuntimeError(f"US target canary table is not COMMITTED: {table}")
        expected = item.get("expected_rows")
        observed = item.get("observed_rows")
        if not isinstance(expected, int) or expected < 0 or observed != expected:
            raise RuntimeError(f"US target canary committed count is invalid: {table}")
        counts[table] = expected
    return counts


def _verify_complete_canary(
    client: WslNativeClickHouseClient,
    *,
    journal_path: Path,
    package: FrozenCanaryPackage,
) -> dict[str, int]:
    journal = load_canary_journal(
        journal_path,
        package=package,
        schema_manifest_sha256=ACCEPTED_SCHEMA_MANIFEST_SHA256,
    )
    if journal.get("state") != "COMPLETE":
        raise RuntimeError("US target package canary journal is not COMPLETE")
    expected = _expected_counts_from_canary(journal)
    observed = _final_package_counts(client, package.package_id)
    if observed != expected:
        raise RuntimeError(
            "US target package final counts differ from durable COMPLETE journal: "
            f"expected={expected} observed={observed}"
        )
    assert_package_unchanged(package)
    _read_target_manifest(client)
    _verify_storage(client)
    return expected


def _reconcile_prepared(
    client: WslNativeClickHouseClient,
    *,
    journal_path: Path,
    package: FrozenCanaryPackage,
) -> None:
    journal = load_canary_journal(
        journal_path,
        package=package,
        schema_manifest_sha256=ACCEPTED_SCHEMA_MANIFEST_SHA256,
    )
    stage = journal.get("stage")
    commits = journal.get("commits")
    if (
        journal.get("state") != "PREPARED"
        or not isinstance(stage, dict)
        or stage.get("status") != "NOT_STARTED"
        or stage.get("row_counts") is not None
    ):
        raise RuntimeError("US target bulk PREPARED reconciliation state mismatch")
    if not isinstance(commits, dict):
        raise RuntimeError("US target bulk PREPARED commits are missing")
    for table in APPLICATION_CANARY_TABLES:
        item = commits.get(table)
        if (
            not isinstance(item, dict)
            or item.get("status") != "PENDING"
            or item.get("expected_rows") is not None
            or item.get("observed_rows") is not None
        ):
            raise RuntimeError(f"US target bulk PREPARED commit is not pristine: {table}")

    _read_target_manifest(client)
    if _stage_table_count(client, package) != 0:
        raise RuntimeError("US target bulk PREPARED package already has staging tables")
    final_counts = _final_package_counts(client, package.package_id)
    if any(final_counts.values()):
        raise RuntimeError(
            f"US target bulk PREPARED package already has final rows: {final_counts}"
        )
    _verify_storage(client)
    assert_package_unchanged(package)


def _verify_package2_target(
    client: WslNativeClickHouseClient,
    *,
    plan: dict[str, Any],
    stage2_receipt: dict[str, Any],
) -> None:
    validated = validate_stage2_anchor(stage2_receipt)
    plan_anchor = plan.get("accepted_package2_anchor")
    if validated != plan_anchor:
        raise RuntimeError("accepted Package 2 receipt no longer matches frozen bulk plan")
    expected = validated["expected_row_counts"]
    if set(expected) != set(APPLICATION_CANARY_TABLES):
        raise RuntimeError("accepted Package 2 row-count table set drifted")
    observed = _final_package_counts(client, uuid.UUID(validated["package_id"]))
    if observed != expected:
        raise RuntimeError(
            "accepted Package 2 target anchor count drifted: "
            f"expected={expected} observed={observed}"
        )
    source = plan.get("accepted_package2_source")
    if not isinstance(source, dict):
        raise RuntimeError("bulk plan is missing accepted Package 2 source identity")
    package = _frozen_from_plan(source)
    assert_package_unchanged(package)
    _read_target_manifest(client)
    _verify_storage(client)


def commit_one_package(
    item: dict[str, Any],
    package_state: dict[str, Any],
    *,
    client: WslNativeClickHouseClient,
) -> dict[str, int]:
    package = _frozen_from_plan(item)
    manifest = _read_target_manifest(client)
    journal_path = Path(str(package_state["canary_journal_path"]))

    if not journal_path.exists():
        existing = _final_package_counts(client, package.package_id)
        if any(existing.values()):
            raise RuntimeError(
                "US target bulk found package rows without a durable target-canary journal: "
                f"sequence={item['sequence']} counts={existing}"
            )
        initialize_canary_journal(
            journal_path,
            package=package,
            schema_manifest_sha256=ACCEPTED_SCHEMA_MANIFEST_SHA256,
        )

    journal = load_canary_journal(
        journal_path,
        package=package,
        schema_manifest_sha256=ACCEPTED_SCHEMA_MANIFEST_SHA256,
    )
    state = str(journal.get("state") or "")
    if state == "COMPLETE":
        return _verify_complete_canary(
            client,
            journal_path=journal_path,
            package=package,
        )
    if state == "STAGING":
        raise RuntimeError(
            "US target bulk package stopped in STAGING; explicit read-only staging "
            "reconciliation is required before any retry"
        )
    if state == "PREPARED":
        _reconcile_prepared(
            client,
            journal_path=journal_path,
            package=package,
        )
        mark_stage_started(
            journal_path,
            package=package,
            schema_manifest_sha256=ACCEPTED_SCHEMA_MANIFEST_SHA256,
        )
        for statement in stage_ddl_from_manifest(manifest, package):
            client.command(statement)
        staged_counts = stage_package_rows(client, package)
        mark_stage_complete(
            client,
            journal_path,
            package=package,
            schema_manifest_sha256=ACCEPTED_SCHEMA_MANIFEST_SHA256,
            expected_row_counts=staged_counts,
        )
    elif state not in {"STAGED", "COMMITTING"}:
        raise RuntimeError(f"US target bulk package journal state is not resumable: {state}")

    commit_staged_tables(
        client,
        journal_path,
        package=package,
        schema_manifest_sha256=ACCEPTED_SCHEMA_MANIFEST_SHA256,
    )
    return _verify_complete_canary(
        client,
        journal_path=journal_path,
        package=package,
    )


def cleanup_one_package(
    item: dict[str, Any],
    package_state: dict[str, Any],
    *,
    client: BulkTargetClient,
    expected_counts: dict[str, int],
) -> None:
    package = _frozen_from_plan(item)
    journal_path = Path(str(package_state["canary_journal_path"]))
    observed = _verify_complete_canary(
        client,
        journal_path=journal_path,
        package=package,
    )
    if observed != expected_counts:
        raise RuntimeError("US target bulk cleanup count binding drifted")
    for stage_table in stage_table_map(package).values():
        client.drop_bulk_stage_table(stage_table)
    if _stage_table_count(client, package) != 0:
        raise RuntimeError("US target bulk package staging cleanup is incomplete")
    _verify_storage(client)
    assert_package_unchanged(package)


CommitPackage = Callable[[dict[str, Any], dict[str, Any]], dict[str, int]]
CleanupPackage = Callable[[dict[str, Any], dict[str, Any], dict[str, int]], None]


def execute_bulk_plan(
    *,
    plan: dict[str, Any],
    stage2_receipt: dict[str, Any],
    journal_path: Path,
    state_dir: Path,
    authority_token: str,
    client: BulkTargetClient | None = None,
    commit_package: CommitPackage | None = None,
    cleanup_package: CleanupPackage | None = None,
) -> dict[str, Any]:
    """Execute exactly the frozen Package1 bridge + bounded sequence-3+ range."""
    validate_bulk_plan(plan)
    required = str(plan["required_authority_token"])
    if authority_token != required:
        raise RuntimeError(
            "US target bulk production authority token does not match the exact frozen plan"
        )
    target = client or BulkTargetClient()
    _verify_package2_target(target, plan=plan, stage2_receipt=stage2_receipt)

    if journal_path.exists():
        load_bulk_journal(journal_path, plan=plan)
    else:
        initialize_bulk_journal(journal_path, plan=plan, state_dir=state_dir)
    mark_bulk_running(journal_path, plan=plan)

    if commit_package is None:
        def commit_package(item: dict[str, Any], state: dict[str, Any]) -> dict[str, int]:
            return commit_one_package(item, state, client=target)
    if cleanup_package is None:
        def cleanup_package(
            item: dict[str, Any],
            state: dict[str, Any],
            counts: dict[str, int],
        ) -> None:
            cleanup_one_package(item, state, client=target, expected_counts=counts)

    for item in plan["packages"]:
        sequence = int(item["sequence"])
        bulk = load_bulk_journal(journal_path, plan=plan)
        state = bulk["packages"][str(sequence)]
        if state["status"] == "COMPLETE":
            continue
        try:
            if state["status"] == "PENDING":
                counts = commit_package(item, state)
                bulk = mark_package_final_verified(
                    journal_path,
                    plan=plan,
                    sequence=sequence,
                    final_row_counts=counts,
                )
                state = bulk["packages"][str(sequence)]
            else:
                counts = {
                    str(table): int(count)
                    for table, count in dict(state["final_row_counts"]).items()
                }

            cleanup_package(item, state, counts)
            receipt = {
                "receipt_version": "US_APPLICATION_TARGET_BULK_PACKAGE_RECEIPT_V1",
                "executor_version": BULK_EXECUTOR_VERSION,
                "plan_sha256": plan["plan_sha256"],
                "sequence": sequence,
                "role": item["role"],
                "file_name": item["file_name"],
                "sha256": item["sha256"],
                "package_id": item["package_id"],
                "final_row_counts": counts,
                "stage_cleanup_complete": True,
                "source_file_preserved": True,
                "automatic_next_package": False,
            }
            write_receipt(Path(str(state["receipt_path"])), receipt)
            mark_package_complete(
                journal_path,
                plan=plan,
                sequence=sequence,
            )
        except Exception as exc:
            mark_bulk_blocked(
                journal_path,
                plan=plan,
                sequence=sequence,
                error=exc,
            )
            raise

    audit = audit_bulk_plan(
        plan=plan,
        stage2_receipt=stage2_receipt,
        journal_path=journal_path,
        client=target,
    )
    return {
        "executor_version": BULK_EXECUTOR_VERSION,
        "decision": BULK_ACCEPTED_DECISION,
        "plan_sha256": plan["plan_sha256"],
        "inventory_sha256": plan["inventory_sha256"],
        "execution_main": plan["execution_main"],
        "bridge_sequence": 1,
        "accepted_existing_target_sequence": 2,
        "start_sequence": plan["start_sequence"],
        "end_sequence": plan["end_sequence"],
        "package_count": plan["package_count"],
        "journal_state": audit["journal_state"],
        "target_audit": audit,
        "automatic_next_package": False,
        "next_sequence": (
            int(plan["end_sequence"]) + 1
            if int(plan["end_sequence"]) < 310
            else None
        ),
        "full_accepted_source_corpus_on_target": (
            int(plan["start_sequence"]) == 3 and int(plan["end_sequence"]) == 310
        ),
    }


def audit_bulk_plan(
    *,
    plan: dict[str, Any],
    stage2_receipt: dict[str, Any],
    journal_path: Path,
    client: WslNativeClickHouseClient | None = None,
) -> dict[str, Any]:
    validate_bulk_plan(plan)
    target = client or WslNativeClickHouseClient()
    _verify_package2_target(target, plan=plan, stage2_receipt=stage2_receipt)
    bulk = load_bulk_journal(journal_path, plan=plan)
    if bulk.get("state") != "COMPLETE":
        raise RuntimeError("US target bulk audit requires a COMPLETE bulk journal")

    total_rows = 0
    package_rows: dict[str, int] = {}
    for item in plan["packages"]:
        sequence = int(item["sequence"])
        state = bulk["packages"][str(sequence)]
        if state.get("status") != "COMPLETE" or not state.get("stage_cleanup_complete"):
            raise RuntimeError(f"US target bulk audit package is incomplete: {sequence}")
        package = _frozen_from_plan(item)
        counts = _verify_complete_canary(
            target,
            journal_path=Path(str(state["canary_journal_path"])),
            package=package,
        )
        expected = {
            str(table): int(count)
            for table, count in dict(state["final_row_counts"]).items()
        }
        if counts != expected:
            raise RuntimeError(f"US target bulk audit count drift: {sequence}")
        if _stage_table_count(target, package) != 0:
            raise RuntimeError(f"US target bulk audit found stale staging tables: {sequence}")
        package_total = sum(counts.values())
        package_rows[str(sequence)] = package_total
        total_rows += package_total

    storage = _verify_storage(target)
    _read_target_manifest(target)
    return {
        "audit_version": "US_APPLICATION_TARGET_BULK_AUDIT_V1",
        "journal_state": "COMPLETE",
        "plan_sha256": plan["plan_sha256"],
        "inventory_sha256": plan["inventory_sha256"],
        "verified_execution_sequences": [int(item["sequence"]) for item in plan["packages"]],
        "accepted_existing_sequence": 2,
        "package_total_rows": package_rows,
        "selected_total_rows": total_rows,
        "storage": storage,
        "source_files_preserved": True,
        "automatic_next_package": False,
    }
