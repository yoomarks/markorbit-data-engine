from __future__ import annotations

import argparse
import json
from typing import Any

from app.db import clickhouse_client


DATABASE = "markorbit_facts"
SOURCE_TABLE = "cn_goods_item_observation"
CURRENT_TABLE = "cn_goods_item_current"
SHADOW_TABLE = "cn_goods_item_observation_storage_v2_shadow"
ARCHIVE_TABLE = "cn_goods_item_observation_storage_v1_archive"
ZERO_UUID = "00000000-0000-0000-0000-000000000000"

BASELINE_TRANSITIONS = {"FIRST_OBSERVED", "REOBSERVED"}
DELTA_TRANSITIONS = {"STATUS_CHANGED", "ITEM_DETAILS_CHANGED"}
KNOWN_TRANSITIONS = BASELINE_TRANSITIONS | DELTA_TRANSITIONS


def _scalar(client: Any, sql: str) -> int:
    rows = client.query(sql).result_rows
    return int(rows[0][0] or 0) if rows else 0


def _table_exists(client: Any, table: str) -> bool:
    return bool(
        _scalar(
            client,
            "SELECT count() FROM system.tables "
            f"WHERE database = '{DATABASE}' AND name = '{table}'",
        )
    )


def _transition_counts(client: Any, table: str = SOURCE_TABLE) -> dict[str, int]:
    rows = client.query(
        f"""
        SELECT transition_type, count()
        FROM {DATABASE}.{table}
        GROUP BY transition_type
        ORDER BY transition_type
        """
    ).result_rows
    return {str(name): int(count or 0) for name, count in rows}


def _active_bytes(client: Any, table: str) -> int:
    return _scalar(
        client,
        "SELECT coalesce(sum(bytes_on_disk), 0) FROM system.parts "
        f"WHERE database = '{DATABASE}' AND table = '{table}' AND active",
    )


def _current_goods_summary(client: Any) -> tuple[int, int]:
    current_rows, missing_first_source_rows = client.query(
        f"""
        SELECT
            count(),
            countIf(
                first_source_rank = 0
                OR first_source_package_id = toUUID('{ZERO_UUID}')
            )
        FROM {DATABASE}.{CURRENT_TABLE} FINAL
        WHERE is_deleted = 0
        """
    ).result_rows[0]
    return int(current_rows or 0), int(missing_first_source_rows or 0)


def _related_tables(client: Any) -> list[dict[str, Any]]:
    rows = client.query(
        f"""
        SELECT name, toString(uuid), engine
        FROM system.tables
        WHERE database = '{DATABASE}'
          AND startsWith(name, 'cn_goods_item_observation')
        ORDER BY name
        """
    ).result_rows
    result: list[dict[str, Any]] = []
    for name, table_uuid, engine in rows:
        table = str(name)
        result.append(
            {
                "table": table,
                "uuid": str(table_uuid),
                "engine": str(engine),
                "rows": _scalar(client, f"SELECT count() FROM {DATABASE}.{table}"),
                "active_bytes": _active_bytes(client, table),
                "transition_counts": _transition_counts(client, table),
            }
        )
    return result


def _recent_dropped_tables(client: Any) -> list[dict[str, Any]]:
    has_table = _scalar(
        client,
        "SELECT count() FROM system.tables "
        "WHERE database = 'system' AND name = 'dropped_tables'",
    )
    if not has_table:
        return []

    columns = {
        str(row[0])
        for row in client.query(
            "SELECT name FROM system.columns "
            "WHERE database = 'system' AND table = 'dropped_tables'"
        ).result_rows
    }
    required = {"database", "table", "uuid"}
    if not required.issubset(columns):
        return []

    rows = client.query(
        f"""
        SELECT database, table, toString(uuid)
        FROM system.dropped_tables
        WHERE database = '{DATABASE}'
          AND startsWith(table, 'cn_goods_item_observation')
        ORDER BY table
        """
    ).result_rows
    return [
        {"database": str(database), "table": str(table), "uuid": str(table_uuid)}
        for database, table, table_uuid in rows
    ]


def _recent_ddl(client: Any) -> list[dict[str, Any]]:
    has_query_log = _scalar(
        client,
        "SELECT count() FROM system.tables "
        "WHERE database = 'system' AND name = 'query_log'",
    )
    if not has_query_log:
        return []
    try:
        rows = client.query(
            """
            SELECT toString(event_time), query
            FROM system.query_log
            WHERE type = 'QueryFinish'
              AND event_time >= now() - INTERVAL 2 HOUR
              AND positionCaseInsensitive(query, 'cn_goods_item_observation') > 0
              AND (
                  positionCaseInsensitive(query, 'RENAME TABLE') > 0
                  OR positionCaseInsensitive(query, 'EXCHANGE TABLES') > 0
                  OR positionCaseInsensitive(query, 'DROP TABLE') > 0
                  OR positionCaseInsensitive(query, 'UNDROP TABLE') > 0
                  OR positionCaseInsensitive(query, 'CREATE TABLE') > 0
              )
            ORDER BY event_time DESC
            LIMIT 50
            """
        ).result_rows
    except Exception:
        return []
    return [{"event_time": str(event_time), "query": str(query)} for event_time, query in rows]


def build_status(*, client: Any | None = None) -> dict[str, Any]:
    """Return read-only diagnostics for the CN goods compaction state."""
    client = client or clickhouse_client()
    current_rows, missing_first_source_rows = _current_goods_summary(client)
    related_tables = _related_tables(client)
    by_name = {row["table"]: row for row in related_tables}
    source = by_name.get(SOURCE_TABLE)
    archive = by_name.get(ARCHIVE_TABLE)
    shadow = by_name.get(SHADOW_TABLE)

    if archive and not shadow:
        state = "APPLIED_REVERSIBLE"
    elif shadow:
        state = "INCOMPLETE_SHADOW_PRESENT"
    elif source:
        source_counts = source["transition_counts"]
        baseline_rows = sum(source_counts.get(name, 0) for name in BASELINE_TRANSITIONS)
        if baseline_rows:
            state = "LEGACY_OR_UNCOMPACTED_ACTIVE"
        elif current_rows:
            state = "COMPACT_WITHOUT_ARCHIVE_OR_ALREADY_FINALIZED"
        else:
            state = "EMPTY"
    else:
        state = "SOURCE_TABLE_MISSING"

    return {
        "status_version": "CN_STORAGE_V2_GOODS_STATUS_V2",
        "read_only": True,
        "state": state,
        "current_goods_rows": current_rows,
        "current_rows_missing_first_source": missing_first_source_rows,
        "related_tables": related_tables,
        "recent_dropped_tables": _recent_dropped_tables(client),
        "recent_ddl": _recent_ddl(client),
        "safety_note": (
            "Do not re-apply compaction when the active observation table is already "
            "baseline-free and the archive is missing. Inspect status/recovery evidence first."
        ),
    }


def build_plan(*, client: Any | None = None) -> dict[str, Any]:
    """Return a read-only compaction plan for CN goods observation history."""
    client = client or clickhouse_client()
    counts = _transition_counts(client)
    total_rows = sum(counts.values())
    removable_rows = sum(counts.get(name, 0) for name in BASELINE_TRANSITIONS)
    keep_rows = sum(counts.get(name, 0) for name in DELTA_TRANSITIONS)
    unknown = {
        name: count for name, count in counts.items() if name not in KNOWN_TRANSITIONS
    }
    unknown_rows = sum(unknown.values())

    current_rows, missing_first_source_rows = _current_goods_summary(client)
    source_bytes = _active_bytes(client, SOURCE_TABLE)
    estimated_reclaim_bytes = (
        int(source_bytes * removable_rows / total_rows) if total_rows else 0
    )
    shadow_exists = _table_exists(client, SHADOW_TABLE)
    archive_exists = _table_exists(client, ARCHIVE_TABLE)

    # Never allow a second Apply against an already baseline-free active table.
    # That state is either a completed finalization or an abnormal loss/rename of
    # the reversible archive and must be diagnosed instead of overwritten.
    baseline_rows_present = removable_rows > 0
    ambiguous_compact_without_archive = (
        current_rows > 0
        and not baseline_rows_present
        and not archive_exists
        and not shadow_exists
    )
    safe_to_apply = (
        baseline_rows_present
        and unknown_rows == 0
        and missing_first_source_rows == 0
        and not shadow_exists
        and not archive_exists
        and not ambiguous_compact_without_archive
    )
    return {
        "plan_version": "CN_STORAGE_V2_GOODS_COMPACTION_V2",
        "read_only": True,
        "policy": "CURRENT_FIRST_SOURCE_PLUS_TRUE_DELTA_HISTORY",
        "transition_counts": counts,
        "source_rows": total_rows,
        "removable_baseline_rows": removable_rows,
        "keep_delta_rows": keep_rows,
        "unknown_transition_rows": unknown_rows,
        "unknown_transition_counts": unknown,
        "current_goods_rows": current_rows,
        "current_rows_missing_first_source": missing_first_source_rows,
        "source_active_bytes": source_bytes,
        "estimated_reclaim_bytes": estimated_reclaim_bytes,
        "shadow_exists": shadow_exists,
        "archive_exists": archive_exists,
        "ambiguous_compact_without_archive": ambiguous_compact_without_archive,
        "safe_to_apply": safe_to_apply,
        "evidence_note": (
            "FIRST_OBSERVED is reconstructible from retained raw authority plus "
            "cn_goods_item_current.first_source_package_id/first_source_rank. "
            "REOBSERVED is a no-op. True status/detail changes remain in the "
            "observation table."
        ),
    }


def apply_compaction(*, client: Any | None = None) -> dict[str, Any]:
    """Build and atomically activate a compact goods-observation table."""
    client = client or clickhouse_client()
    plan = build_plan(client=client)
    if not plan["safe_to_apply"]:
        raise RuntimeError(f"CN goods compaction preflight failed: {json.dumps(plan)}")

    source_rows_before = int(plan["source_rows"])
    keep_rows_expected = int(plan["keep_delta_rows"])

    client.command(
        f"CREATE TABLE {DATABASE}.{SHADOW_TABLE} AS {DATABASE}.{SOURCE_TABLE}"
    )
    if keep_rows_expected:
        keep_values = ", ".join(f"'{value}'" for value in sorted(DELTA_TRANSITIONS))
        client.command(
            f"""
            INSERT INTO {DATABASE}.{SHADOW_TABLE}
            SELECT *
            FROM {DATABASE}.{SOURCE_TABLE}
            WHERE transition_type IN ({keep_values})
            """
        )

    shadow_rows = _scalar(client, f"SELECT count() FROM {DATABASE}.{SHADOW_TABLE}")
    source_rows_now = _scalar(client, f"SELECT count() FROM {DATABASE}.{SOURCE_TABLE}")
    if shadow_rows != keep_rows_expected:
        raise RuntimeError(
            "CN goods compaction shadow validation failed: "
            f"expected {keep_rows_expected} rows, got {shadow_rows}."
        )
    if source_rows_now != source_rows_before:
        raise RuntimeError(
            "CN goods observation source changed during compaction: "
            f"before={source_rows_before}, now={source_rows_now}."
        )

    client.command(
        f"EXCHANGE TABLES {DATABASE}.{SOURCE_TABLE} AND {DATABASE}.{SHADOW_TABLE}"
    )
    client.command(
        f"RENAME TABLE {DATABASE}.{SHADOW_TABLE} TO {DATABASE}.{ARCHIVE_TABLE}"
    )

    active_counts = _transition_counts(client, SOURCE_TABLE)
    if any(name in active_counts for name in BASELINE_TRANSITIONS):
        raise RuntimeError(
            "CN goods compaction activated a table that still contains baseline rows."
        )
    if sum(active_counts.values()) != keep_rows_expected:
        raise RuntimeError("CN goods compact table row count changed after activation.")

    return {
        "status": "APPLIED_REVERSIBLE",
        "plan": plan,
        "active_transition_counts": active_counts,
        "archive_rows": _scalar(client, f"SELECT count() FROM {DATABASE}.{ARCHIVE_TABLE}"),
        "archive_active_bytes": _active_bytes(client, ARCHIVE_TABLE),
        "next_step": (
            "Run status/plan/audits against the compact table. Use rollback before "
            "finalize if anything is unexpected; finalize is irreversible."
        ),
    }


def rollback_compaction(*, client: Any | None = None) -> dict[str, Any]:
    client = client or clickhouse_client()
    if not _table_exists(client, ARCHIVE_TABLE):
        raise RuntimeError(
            "No CN goods Storage V1 archive exists to roll back. Status: "
            + json.dumps(build_status(client=client), default=str)
        )
    if _table_exists(client, SHADOW_TABLE):
        raise RuntimeError("Unexpected compaction shadow table exists; refusing rollback.")

    client.command(
        f"RENAME TABLE {DATABASE}.{ARCHIVE_TABLE} TO {DATABASE}.{SHADOW_TABLE}"
    )
    client.command(
        f"EXCHANGE TABLES {DATABASE}.{SOURCE_TABLE} AND {DATABASE}.{SHADOW_TABLE}"
    )
    client.command(f"DROP TABLE {DATABASE}.{SHADOW_TABLE} SYNC")
    return {"status": "ROLLED_BACK", "transition_counts": _transition_counts(client)}


def finalize_compaction(*, client: Any | None = None) -> dict[str, Any]:
    client = client or clickhouse_client()
    if not _table_exists(client, ARCHIVE_TABLE):
        raise RuntimeError(
            "No CN goods Storage V1 archive exists to finalize. Status: "
            + json.dumps(build_status(client=client), default=str)
        )
    if _table_exists(client, SHADOW_TABLE):
        raise RuntimeError("Unexpected compaction shadow table exists; refusing finalize.")

    active_counts = _transition_counts(client, SOURCE_TABLE)
    if any(name in active_counts for name in BASELINE_TRANSITIONS):
        raise RuntimeError(
            "Active CN goods observation table still contains baseline rows; refusing finalize."
        )
    unknown = {name: count for name, count in active_counts.items() if name not in DELTA_TRANSITIONS}
    if unknown:
        raise RuntimeError(
            f"Active CN goods observation table contains unknown transitions: {unknown}"
        )

    archive_counts = _transition_counts(client, ARCHIVE_TABLE)
    archive_delta_rows = sum(archive_counts.get(name, 0) for name in DELTA_TRANSITIONS)
    if archive_delta_rows != sum(active_counts.values()):
        raise RuntimeError(
            "Active compact history does not preserve every delta row from the archive; "
            "refusing irreversible finalization."
        )

    archive_bytes = _active_bytes(client, ARCHIVE_TABLE)
    archive_rows = sum(archive_counts.values())
    client.command(f"DROP TABLE {DATABASE}.{ARCHIVE_TABLE} SYNC")
    return {
        "status": "FINALIZED",
        "dropped_archive_rows": archive_rows,
        "released_clickhouse_bytes_before_filesystem_reuse": archive_bytes,
        "active_transition_counts": active_counts,
        "note": (
            "The ClickHouse filesystem can reuse the released space immediately. "
            "The outer Docker/WSL VHDX file may remain physically large until a "
            "separate filesystem/VHDX compaction is performed."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Guarded Storage V2 compaction for CN goods observation history."
    )
    parser.add_argument(
        "--mode",
        choices=("status", "plan", "apply", "rollback", "finalize"),
        default="status",
    )
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    if args.mode == "status":
        result = build_status()
    elif args.mode == "plan":
        result = build_plan()
    elif args.mode == "apply":
        result = apply_compaction()
    elif args.mode == "rollback":
        result = rollback_compaction()
    else:
        result = finalize_compaction()

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=None if args.compact else 2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
