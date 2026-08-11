from __future__ import annotations

import json

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


def main() -> None:
    client = clickhouse_client()
    _cleanup_fixture_rows()
    try:
        if _table_exists(client, ARCHIVE_TABLE) or _table_exists(client, SHADOW_TABLE):
            raise AssertionError("fixture requires no leftover compaction temp tables")

        _stage_fixture()
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
            raise AssertionError("single-process commit left a temp table behind")
        if result.get("status") != "COMMITTED_FINAL":
            raise AssertionError(result)

        print(
            json.dumps(
                {
                    "status": "PASS",
                    "commit_status": result["status"],
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
