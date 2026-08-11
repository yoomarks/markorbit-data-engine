from __future__ import annotations

import json
import uuid

from app.cn.goods_lifecycle import cleanup_goods_outputs, publish_goods_lifecycle
from app.db import clickhouse_client


PACKAGE_ID = uuid.UUID("00000000-0000-0000-0000-00000000c016")
CASE_A = uuid.UUID("00000000-0000-0000-0000-00000000a001")
CASE_B = uuid.UUID("00000000-0000-0000-0000-00000000b001")
SOURCE_RANK = 10_000_000_000_000_016
ROW_A1 = "a" * 64
ROW_A2 = "b" * 64
ROW_B1 = "c" * 64


def _scalar(sql: str) -> int:
    rows = clickhouse_client().query(sql).result_rows
    return int(rows[0][0] or 0) if rows else 0


def _stage_fixture() -> None:
    package = str(PACKAGE_ID)
    client = clickhouse_client()
    client.command(f"""
        INSERT INTO markorbit_facts.cn_stage_goods
        (
            package_id, case_id, application_number, class_no,
            similar_group, goods_sequence, goods_name, goods_status_raw,
            goods_status_bucket, goods_status_reason,
            goods_status_mapping_version, source_file,
            source_start_line, source_end_line, row_hash
        ) VALUES
        (
            toUUID('{package}'), toUUID('{CASE_A}'), 'A001', 9,
            '0901', '1', 'Alpha fixture goods', '', 'ACTIVE', '',
            'CN_GOODS_STATUS_V1', 'fixture.csv', 1, 1, '{ROW_A1}'
        ),
        (
            toUUID('{package}'), toUUID('{CASE_A}'), 'A001', 9,
            '0901', '1', 'Alpha fixture goods', '0', 'UNKNOWN', '',
            'CN_GOODS_STATUS_V1', 'fixture.csv', 2, 2, '{ROW_A2}'
        ),
        (
            toUUID('{package}'), toUUID('{CASE_B}'), 'B001', 25,
            '2501', '1', 'Beta fixture goods', '2', 'INACTIVE', '',
            'CN_GOODS_STATUS_V1', 'fixture.csv', 3, 3, '{ROW_B1}'
        )
    """)


def _cleanup_fixture() -> None:
    package = str(PACKAGE_ID)
    cleanup_goods_outputs(PACKAGE_ID)
    clickhouse_client().command(
        "ALTER TABLE markorbit_facts.cn_stage_goods "
        f"DELETE WHERE package_id = toUUID('{package}') SETTINGS mutations_sync = 1"
    )


def main() -> None:
    package = str(PACKAGE_ID)
    try:
        _cleanup_fixture()
        _stage_fixture()

        metrics = publish_goods_lifecycle(
            PACKAGE_ID,
            {
                "package_kind": "BASE_PARTITION",
                "source_rank": SOURCE_RANK,
                "source_period_end": None,
            },
        )

        item_count = _scalar(
            "SELECT count() FROM markorbit_facts.cn_goods_item_current FINAL "
            f"WHERE last_source_package_id = toUUID('{package}') AND is_deleted = 0"
        )
        scope_count = _scalar(
            "SELECT count() FROM markorbit_facts.cn_stage_scope_publish "
            f"WHERE package_id = toUUID('{package}')"
        )
        lifecycle_count = _scalar(
            "SELECT count() FROM markorbit_facts.cn_goods_scope_lifecycle_current FINAL "
            f"WHERE last_source_package_id = toUUID('{package}') AND is_deleted = 0"
        )
        a_status = clickhouse_client().query(f"""
            SELECT goods_status_raw
            FROM markorbit_facts.cn_goods_item_current FINAL
            WHERE last_source_package_id = toUUID('{package}')
              AND application_number = 'A001'
              AND is_deleted = 0
        """).result_rows[0][0]

        expected_metrics = {
            "goods_lifecycle_touched_items": 2,
            "goods_lifecycle_touched_scopes": 2,
            "goods_lifecycle_code_0_items_in_touched_scopes": 1,
            "goods_lifecycle_code_1_items_in_touched_scopes": 0,
            "goods_lifecycle_code_2_items_in_touched_scopes": 1,
            "goods_publish_chunk_count": 1,
        }
        if metrics != expected_metrics:
            raise AssertionError({"expected_metrics": expected_metrics, "actual": metrics})
        if item_count != 2 or scope_count != 2 or lifecycle_count != 2:
            raise AssertionError(
                {
                    "item_count": item_count,
                    "scope_count": scope_count,
                    "lifecycle_count": lifecycle_count,
                }
            )
        if str(a_status) != "0":
            raise AssertionError(f"package-local strongest status was {a_status!r}, expected '0'")

        print(
            json.dumps(
                {
                    "status": "PASS",
                    "package_id": package,
                    "metrics": metrics,
                    "item_count": item_count,
                    "scope_count": scope_count,
                    "lifecycle_count": lifecycle_count,
                    "a_status": a_status,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        _cleanup_fixture()


if __name__ == "__main__":
    main()
