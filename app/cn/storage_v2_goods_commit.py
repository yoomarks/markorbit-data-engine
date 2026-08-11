from __future__ import annotations

import argparse
import json
from typing import Any

from app.cn.storage_v2_goods_compaction import (
    ARCHIVE_TABLE,
    BASELINE_TRANSITIONS,
    DATABASE,
    DELTA_TRANSITIONS,
    KNOWN_TRANSITIONS,
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


DROP_QUERY_SETTINGS = {"max_table_size_to_drop": 0}


def _validate_post_exchange(client: Any) -> tuple[dict[str, int], dict[str, int]]:
    active_counts = _transition_counts(client, SOURCE_TABLE)
    old_counts = _transition_counts(client, SHADOW_TABLE)

    active_unknown = {
        name: count for name, count in active_counts.items() if name not in DELTA_TRANSITIONS
    }
    old_unknown = {
        name: count for name, count in old_counts.items() if name not in KNOWN_TRANSITIONS
    }
    old_delta_rows = sum(old_counts.get(name, 0) for name in DELTA_TRANSITIONS)
    active_rows = sum(active_counts.values())
    old_baseline_rows = sum(old_counts.get(name, 0) for name in BASELINE_TRANSITIONS)

    validation_error: str | None = None
    if any(name in active_counts for name in BASELINE_TRANSITIONS):
        validation_error = "active compact table still contains baseline rows"
    elif active_unknown:
        validation_error = f"active compact table contains unknown transitions: {active_unknown}"
    elif old_unknown:
        validation_error = f"old wide table contains unknown transitions: {old_unknown}"
    elif old_baseline_rows == 0:
        validation_error = "shadow table is not the expected legacy wide baseline table"
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

    return active_counts, old_counts


def _drop_validated_shadow(client: Any) -> tuple[int, int]:
    old_counts = _transition_counts(client, SHADOW_TABLE)
    old_rows = sum(old_counts.values())
    old_bytes = _active_bytes(client, SHADOW_TABLE)

    # ClickHouse protects DROP of large MergeTree tables with a 50 GiB default
    # max_table_size_to_drop. We override that guard only for this one already-
    # validated shadow DROP; server/global configuration is left unchanged.
    client.command(
        f"DROP TABLE {DATABASE}.{SHADOW_TABLE} SYNC",
        settings=DROP_QUERY_SETTINGS,
    )

    if _table_exists(client, SHADOW_TABLE) or _table_exists(client, ARCHIVE_TABLE):
        raise RuntimeError(
            "CN goods commit completed drop but a temporary table still exists. Status: "
            + json.dumps(build_status(client=client), default=str)
        )
    return old_rows, old_bytes


def _resume_pending_drop(client: Any) -> dict[str, Any]:
    """Finish a prior commit that exchanged successfully but could not DROP the shadow."""
    if _table_exists(client, ARCHIVE_TABLE):
        raise RuntimeError(
            "CN goods commit recovery refuses to proceed while an archive table exists. Status: "
            + json.dumps(build_status(client=client), default=str)
        )
    if not _table_exists(client, SHADOW_TABLE):
        raise RuntimeError("No pending CN goods shadow exists to resume.")

    status = build_status(client=client)
    if int(status.get("current_rows_missing_first_source", 0)) != 0:
        raise RuntimeError(
            "CN goods commit recovery found current rows without first-source provenance. Status: "
            + json.dumps(status, default=str)
        )

    active_counts, old_counts = _validate_post_exchange(client)
    old_rows = sum(old_counts.values())
    old_bytes = _active_bytes(client, SHADOW_TABLE)
    dropped_rows, dropped_bytes = _drop_validated_shadow(client)
    if dropped_rows != old_rows or dropped_bytes != old_bytes:
        raise RuntimeError("CN goods commit recovery accounting changed during guarded drop.")

    return {
        "status": "COMMITTED_FINAL",
        "resumed_pending_drop": True,
        "active_transition_counts": active_counts,
        "dropped_wide_rows": dropped_rows,
        "released_clickhouse_bytes_before_filesystem_reuse": dropped_bytes,
        "state_after": build_status(client=client)["state"],
        "drop_query_settings": DROP_QUERY_SETTINGS,
        "note": (
            "Recovered a prior post-EXCHANGE state and dropped only the validated legacy "
            "shadow. ClickHouse can reuse the released filesystem space immediately."
        ),
    }


def commit_compaction(*, client: Any | None = None) -> dict[str, Any]:
    """Compact and finalize CN goods history in one guarded process.

    If a prior run already completed the atomic EXCHANGE but was stopped by
    ClickHouse's large-table DROP safety threshold, the same command safely
    resumes from the validated shadow instead of repeating the compaction.
    """
    client = client or clickhouse_client()

    if _table_exists(client, SHADOW_TABLE):
        return _resume_pending_drop(client)
    if _table_exists(client, ARCHIVE_TABLE):
        raise RuntimeError(
            "CN goods commit requires no archive table. Status: "
            + json.dumps(build_status(client=client), default=str)
        )

    plan = build_plan(client=client)
    if not plan["safe_to_apply"]:
        raise RuntimeError(
            "CN goods commit preflight failed: " + json.dumps(plan, default=str)
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

    active_counts, old_counts = _validate_post_exchange(client)
    active_rows = sum(active_counts.values())
    if active_rows != keep_rows_expected:
        raise RuntimeError(
            "CN goods compact row count changed after exchange: "
            f"expected={keep_rows_expected}, active={active_rows}."
        )

    old_rows = sum(old_counts.values())
    old_bytes = _active_bytes(client, SHADOW_TABLE)
    dropped_rows, dropped_bytes = _drop_validated_shadow(client)
    if dropped_rows != old_rows or dropped_bytes != old_bytes:
        raise RuntimeError("CN goods commit accounting changed during guarded drop.")

    return {
        "status": "COMMITTED_FINAL",
        "resumed_pending_drop": False,
        "plan": plan,
        "active_transition_counts": active_counts,
        "dropped_wide_rows": dropped_rows,
        "released_clickhouse_bytes_before_filesystem_reuse": dropped_bytes,
        "source_active_bytes_before": source_bytes_before,
        "state_after": build_status(client=client)["state"],
        "drop_query_settings": DROP_QUERY_SETTINGS,
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
