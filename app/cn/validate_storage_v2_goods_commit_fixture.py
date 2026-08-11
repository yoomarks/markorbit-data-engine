from __future__ import annotations

import json

from clickhouse_connect.driver.exceptions import DatabaseError

from app.cn.storage_v2_goods_commit import commit_compaction
from app.cn.storage_v2_goods_compaction import (
    ARCHIVE_TABLE,
    DATABASE,
    SHADOW_TABLE,
    SOURCE_TABLE,
    _table_exists,
)
from app.cn.validate_storage_v2_goods_compaction_fixture import (
    APPLICATION,
    _cleanup_fixture_rows,
    _stage_fixture,
)
from app.db import clickhouse_client


def _scalar(sql: str) -> int:
    rows = clickhouse_client().query(sql).result_rows
    return int(rows[0][0] or 0) if rows else 0


def _stage_post_exchange_pending_drop() -> None:
    client = clickhouse_client()
    client.command(f"CREATE TABLE {DATABASE}.{SHADOW_TABLE} AS {DATABASE}.{SOURCE_TABLE}")
    client.command(
        f"""
        INSERT INTO {DATABASE}.{SHADOW_TABLE}
        SELECT *
        FROM {DATABASE}.{SOURCE_TABLE}
        WHERE transition_type IN ('STATUS_CHANGED', 'ITEM_DETAILS_CHANGED')
        """
    )
    client.command(
        f"EXCHANGE TABLES {DATABASE}.{SOURCE_TABLE} AND {DATABASE}.{SHADOW_TABLE}"
    )


def main() -> None:
    client = clickhouse_client()
    _cleanup_fixture_rows()
    try:
        if _table_exists(client, ARCHIVE_TABLE) or _table_exists(client, SHADOW_TABLE):
            raise AssertionError("fixture requires no leftover compaction temp tables")

        _stage_fixture()
        _stage_post_exchange_pending_drop()

        # Prove query-level max_table_size_to_drop is effective: force the same
        # TABLE_SIZE_EXCEEDS_MAX_DROP_SIZE_LIMIT shape with a one-byte threshold.
        blocked = False
        try:
            client.command(
                f"DROP TABLE {DATABASE}.{SHADOW_TABLE} SYNC",
                settings={"max_table_size_to_drop": 1},
            )
        except DatabaseError as exc:
            blocked = "TABLE_SIZE_EXCEEDS_MAX_DROP_SIZE_LIMIT" in str(exc)
        if not blocked:
            raise AssertionError("expected query-level one-byte DROP guard to block shadow")
        if not _table_exists(client, SHADOW_TABLE):
            raise AssertionError("blocked DROP unexpectedly removed the legacy shadow")

        result = commit_compaction(client=client)

        baseline_rows = _scalar(
            f"SELECT count() FROM {DATABASE}.{SOURCE_TABLE} "
            f"WHERE application_number = '{APPLICATION}' "
            "AND transition_type IN ('FIRST_OBSERVED', 'REOBSERVED')"
        )
        delta_rows = _scalar(
            f"SELECT count() FROM {DATABASE}.{SOURCE_TABLE} "
            f"WHERE application_number = '{APPLICATION}' "
            "AND transition_type = 'STATUS_CHANGED'"
        )
        if baseline_rows != 0 or delta_rows != 1:
            raise AssertionError(
                {"baseline_rows": baseline_rows, "delta_rows": delta_rows, "result": result}
            )
        if _table_exists(client, ARCHIVE_TABLE) or _table_exists(client, SHADOW_TABLE):
            raise AssertionError("single-process commit recovery left a temp table behind")
        if result.get("status") != "COMMITTED_FINAL":
            raise AssertionError(result)
        if result.get("resumed_pending_drop") is not True:
            raise AssertionError("commit did not report pending-drop recovery")
        if result.get("drop_query_settings") != {"max_table_size_to_drop": 0}:
            raise AssertionError("commit did not use the scoped large-table DROP override")

        print(
            json.dumps(
                {
                    "status": "PASS",
                    "drop_guard_blocked_at_one_byte": blocked,
                    "commit_status": result["status"],
                    "resumed_pending_drop": result["resumed_pending_drop"],
                    "baseline_rows": baseline_rows,
                    "delta_rows": delta_rows,
                    "dropped_wide_rows": result["dropped_wide_rows"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        _cleanup_fixture_rows()


if __name__ == "__main__":
    main()
