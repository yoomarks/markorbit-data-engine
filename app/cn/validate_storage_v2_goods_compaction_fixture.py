from __future__ import annotations

import json

from app.cn.storage_v2_goods_compaction import (
    ARCHIVE_TABLE,
    DATABASE,
    SOURCE_TABLE,
    apply_compaction,
    build_plan,
    build_status,
    finalize_compaction,
    rollback_compaction,
)
from app.db import clickhouse_client


APPLICATION = "STORAGEV2FIXTURE001"
CASE_ID = "00000000-0000-0000-0000-00000000f201"
PACKAGE_ID = "00000000-0000-0000-0000-00000000f202"
ITEM_KEY = "a" * 64
SOURCE_HASH = "b" * 64
RECORD_HASH = "c" * 64
FIRST_OBS_HASH = "d" * 64
CHANGE_OBS_HASH = "e" * 64
SOURCE_RANK = 90_000_000_000_000_001


def _scalar(sql: str) -> int:
    rows = clickhouse_client().query(sql).result_rows
    return int(rows[0][0] or 0) if rows else 0


def _cleanup_fixture_rows() -> None:
    client = clickhouse_client()
    client.command(
        f"ALTER TABLE {DATABASE}.cn_goods_item_current DELETE WHERE "
        f"application_number = '{APPLICATION}' SETTINGS mutations_sync = 1"
    )
    client.command(
        f"ALTER TABLE {DATABASE}.{SOURCE_TABLE} DELETE WHERE "
        f"application_number = '{APPLICATION}' SETTINGS mutations_sync = 1"
    )


def _stage_fixture() -> None:
    client = clickhouse_client()
    client.command(f"""
        INSERT INTO {DATABASE}.cn_goods_item_current
        (
            case_id, application_number, class_no, goods_item_key,
            goods_sequence, goods_name, goods_name_norm, similar_group,
            goods_status_raw, goods_status_bucket, goods_status_reason,
            goods_status_semantic, goods_status_source_finality,
            operational_effect, goods_status_mapping_version, evidence_label,
            first_source_package_id, first_source_package_kind, first_source_rank,
            source_package_kind, source_file, source_first_line, source_last_line,
            source_row_hash, last_source_package_id, record_hash, source_rank,
            is_deleted
        ) VALUES
        (
            toUUID('{CASE_ID}'), '{APPLICATION}', 9, '{ITEM_KEY}',
            '1', 'Fixture goods', 'fixture goods', '0901',
            '2', 'INACTIVE', '', 'FINAL_INACTIVE', 'FINAL',
            'INACTIVE_CONFIRMED', 'CN_GOODS_STATUS_V1', 'OFFICIAL_SOURCE',
            toUUID('{PACKAGE_ID}'), 'BASE_PARTITION', {SOURCE_RANK},
            'BASE_PARTITION', 'fixture.csv', 1, 1,
            '{SOURCE_HASH}', toUUID('{PACKAGE_ID}'), '{RECORD_HASH}', {SOURCE_RANK},
            0
        )
    """)
    client.command(f"""
        INSERT INTO {DATABASE}.{SOURCE_TABLE}
        (
            observation_id, case_id, application_number, class_no,
            goods_item_key, goods_sequence, goods_name, similar_group,
            previous_status_raw, previous_status_semantic,
            previous_operational_effect, new_status_raw, new_status_semantic,
            new_status_source_finality, new_operational_effect, transition_type,
            evidence_label, source_package_id, source_package_kind, source_file,
            source_first_line, source_last_line, source_row_hash, source_rank,
            observation_hash
        ) VALUES
        (
            generateUUIDv4(), toUUID('{CASE_ID}'), '{APPLICATION}', 9,
            '{ITEM_KEY}', '1', 'Fixture goods', '0901',
            '', '', '', '0', 'ACTIVE', 'INTERMEDIATE',
            'EFFECTIVE_UNLESS_CONTRADICTED', 'FIRST_OBSERVED',
            'OFFICIAL_SOURCE', toUUID('{PACKAGE_ID}'), 'BASE_PARTITION',
            'fixture.csv', 1, 1, '{SOURCE_HASH}', {SOURCE_RANK}, '{FIRST_OBS_HASH}'
        ),
        (
            generateUUIDv4(), toUUID('{CASE_ID}'), '{APPLICATION}', 9,
            '{ITEM_KEY}', '1', 'Fixture goods', '0901',
            '0', 'ACTIVE', 'EFFECTIVE_UNLESS_CONTRADICTED',
            '2', 'FINAL_INACTIVE', 'FINAL', 'INACTIVE_CONFIRMED', 'STATUS_CHANGED',
            'OFFICIAL_SOURCE', toUUID('{PACKAGE_ID}'), 'BASE_PARTITION',
            'fixture.csv', 2, 2, '{SOURCE_HASH}', {SOURCE_RANK + 1}, '{CHANGE_OBS_HASH}'
        )
    """)


def main() -> None:
    client = clickhouse_client()
    _cleanup_fixture_rows()
    try:
        _stage_fixture()
        plan = build_plan(client=client)
        if not plan["safe_to_apply"]:
            raise AssertionError(plan)

        applied = apply_compaction(client=client)
        status_after_apply = build_status(client=client)
        if status_after_apply["state"] != "APPLIED_REVERSIBLE":
            raise AssertionError(status_after_apply)

        after_apply = _scalar(
            f"SELECT count() FROM {DATABASE}.{SOURCE_TABLE} "
            f"WHERE application_number = '{APPLICATION}' AND transition_type = 'STATUS_CHANGED'"
        )
        baseline_after_apply = _scalar(
            f"SELECT count() FROM {DATABASE}.{SOURCE_TABLE} "
            f"WHERE application_number = '{APPLICATION}' AND transition_type = 'FIRST_OBSERVED'"
        )
        archive_exists = _scalar(
            f"SELECT count() FROM system.tables WHERE database = '{DATABASE}' "
            f"AND name = '{ARCHIVE_TABLE}'"
        )
        if after_apply != 1 or baseline_after_apply != 0 or archive_exists != 1:
            raise AssertionError(
                {
                    "after_apply": after_apply,
                    "baseline_after_apply": baseline_after_apply,
                    "archive_exists": archive_exists,
                    "applied": applied,
                }
            )

        rolled_back = rollback_compaction(client=client)
        baseline_after_rollback = _scalar(
            f"SELECT count() FROM {DATABASE}.{SOURCE_TABLE} "
            f"WHERE application_number = '{APPLICATION}' AND transition_type = 'FIRST_OBSERVED'"
        )
        if baseline_after_rollback != 1:
            raise AssertionError(rolled_back)

        apply_compaction(client=client)
        finalized = finalize_compaction(client=client)
        archive_exists_after_finalize = _scalar(
            f"SELECT count() FROM system.tables WHERE database = '{DATABASE}' "
            f"AND name = '{ARCHIVE_TABLE}'"
        )
        if archive_exists_after_finalize != 0:
            raise AssertionError(finalized)

        status_after_finalize = build_status(client=client)
        if status_after_finalize["state"] != "COMPACT_WITHOUT_ARCHIVE_OR_ALREADY_FINALIZED":
            raise AssertionError(status_after_finalize)
        post_finalize_plan = build_plan(client=client)
        if post_finalize_plan["safe_to_apply"]:
            raise AssertionError(post_finalize_plan)

        print(
            json.dumps(
                {
                    "status": "PASS",
                    "plan": plan,
                    "applied": applied["status"],
                    "status_after_apply": status_after_apply["state"],
                    "rolled_back": rolled_back["status"],
                    "finalized": finalized["status"],
                    "status_after_finalize": status_after_finalize["state"],
                    "post_finalize_safe_to_apply": post_finalize_plan["safe_to_apply"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        _cleanup_fixture_rows()


if __name__ == "__main__":
    main()
