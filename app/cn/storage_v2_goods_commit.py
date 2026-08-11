from __future__ import annotations

import argparse
import json
from typing import Any

from app.cn.storage_v2_goods_compaction import (
    ARCHIVE_TABLE,
    BASELINE_TRANSITIONS,
    DATABASE,
    DELTA_TRANSITIONS,
    SHADOW_TABLE,
    SOURCE_TABLE,
    _active_bytes,
    _scalar,
    _table_exists,
    _transition_counts,
    build_plan,
    build_status,
)
from app.db import clickhouse_client


def commit_compaction(*, client: Any | None = None) -> dict[str, Any]:
    """Compact and finalize CN goods history in one guarded process.

    This path removes the operator-visible Apply/Finalize window. The original
    wide table remains under SHADOW_TABLE after the atomic EXCHANGE until every
    active-table validation has passed; only then is it dropped. If validation
    fails after the exchange, the old table is deliberately left recoverable.
    """
    client = client or clickhouse_client()
    plan = build_plan(client=client)
    if not plan["safe_to_apply"]:
        raise RuntimeError(
            "CN goods commit preflight failed: " + json.dumps(plan, default=str)
        )
    if _table_exists(client, ARCHIVE_TABLE) or _table_exists(client, SHADOW_TABLE):
        raise RuntimeError(
            "CN goods commit requires no archive/shadow table. Status: "
            + json.dumps(build_status(client=client), default=str)
        )

    source_rows_before = int(plan["source_rows"])
    keep_rows_expected = int(plan["keep_delta_rows"])
    source_bytes_before = int(plan["source_active_bytes"])

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
    if shadow_rows != keep_rows_expected or source_rows_now != source_rows_before:
        raise RuntimeError(
            "CN goods commit pre-exchange validation failed; source was not changed. "
            f"expected_shadow={keep_rows_expected}, shadow={shadow_rows}, "
            f"source_before={source_rows_before}, source_now={source_rows_now}."
        )

    client.command(
        f"EXCHANGE TABLES {DATABASE}.{SOURCE_TABLE} AND {DATABASE}.{SHADOW_TABLE}"
    )

    active_counts = _transition_counts(client, SOURCE_TABLE)
    old_counts = _transition_counts(client, SHADOW_TABLE)
    active_unknown = {
        name: count for name, count in active_counts.items() if name not in DELTA_TRANSITIONS
    }
    old_delta_rows = sum(old_counts.get(name, 0) for name in DELTA_TRANSITIONS)
    active_rows = sum(active_counts.values())

    validation_error: str | None = None
    if any(name in active_counts for name in BASELINE_TRANSITIONS):
        validation_error = "active compact table still contains baseline rows"
    elif active_unknown:
        validation_error = f"active compact table contains unknown transitions: {active_unknown}"
    elif active_rows != keep_rows_expected:
        validation_error = (
            f"active compact row count mismatch: expected {keep_rows_expected}, got {active_rows}"
        )
    elif old_delta_rows != active_rows:
        validation_error = (
            "active compact table does not preserve every delta row from the old table: "
            f"old_delta_rows={old_delta_rows}, active_rows={active_rows}"
        )

    if validation_error:
        raise RuntimeError(
            "CN goods commit stopped after atomic exchange but before drop; the original "
            f"wide table is still recoverable as {DATABASE}.{SHADOW_TABLE}. "
            f"Reason: {validation_error}. Status: "
            + json.dumps(build_status(client=client), default=str)
        )

    old_rows = sum(old_counts.values())
    old_bytes = _active_bytes(client, SHADOW_TABLE)
    client.command(f"DROP TABLE {DATABASE}.{SHADOW_TABLE} SYNC")

    if _table_exists(client, SHADOW_TABLE) or _table_exists(client, ARCHIVE_TABLE):
        raise RuntimeError(
            "CN goods commit completed drop but a temporary table still exists. Status: "
            + json.dumps(build_status(client=client), default=str)
        )

    return {
        "status": "COMMITTED_FINAL",
        "plan": plan,
        "active_transition_counts": active_counts,
        "dropped_wide_rows": old_rows,
        "released_clickhouse_bytes_before_filesystem_reuse": old_bytes,
        "source_active_bytes_before": source_bytes_before,
        "state_after": build_status(client=client)["state"],
        "note": (
            "ClickHouse can reuse released filesystem space immediately. The outer "
            "Docker/WSL VHDX may remain physically large until separate VHDX compaction."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Single-process guarded Storage V2 commit for CN goods history."
    )
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    result = commit_compaction()
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
