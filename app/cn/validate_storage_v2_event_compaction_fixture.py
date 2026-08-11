from __future__ import annotations

import json
import uuid

from app.cn.storage_v2_event_compaction import (
    DATABASE,
    SHADOW_TABLE,
    SOURCE_TABLE,
    _baseline_rows,
    _table_exists,
    build_plan,
    commit_compaction,
)
from app.db import clickhouse_client


PACKAGE = uuid.UUID("22222222-2222-4222-8222-222222222222")
CASE = uuid.UUID("33333333-3333-4333-8333-333333333333")


def _insert_event(index: int, event_type: str, old_value: str) -> None:
    client = clickhouse_client()
    escaped_old = old_value.replace("\\", "\\\\").replace("'", "\\'")
    app = f"V2EVENT{index:04d}"
    row_hash = f"{index:064x}"[-64:]
    event_hash = f"{(index + 1000):064x}"[-64:]
    client.command(
        f"""
        INSERT INTO {DATABASE}.{SOURCE_TABLE}
        (
            event_id, case_id, application_number, event_type, event_date,
            affected_scope, class_no, field_name, old_value_compact,
            new_value_compact, evidence_level, legal_effect, confidence_score,
            source_package_id, source_package_kind, source_file,
            source_first_line, source_last_line, source_row_hash, source_rank,
            event_hash
        )
        VALUES
        (
            generateUUIDv4(), toUUID('{CASE}'), '{app}', '{event_type}', NULL,
            'CASE', NULL, 'fixture', '{escaped_old}', '{{"new":"value"}}',
            'OFFICIAL_FACT_OBSERVATION', 'NOT_DETERMINED', 1.0,
            toUUID('{PACKAGE}'), 'BASE', 'fixture.xml', {index}, {index},
            '{row_hash}', {index}, '{event_hash}'
        )
        """
    )


def main() -> None:
    client = clickhouse_client()
    if _table_exists(client, SHADOW_TABLE):
        client.command(
            f"DROP TABLE {DATABASE}.{SHADOW_TABLE} SYNC",
            settings={"max_table_size_to_drop": 0},
        )

    # Fresh dedicated CI database: six reconstructible baselines plus twelve
    # keep rows covering prior-value deltas, party events, and other change types.
    fixture_rows = [
        ("APPLICATION_OBSERVED", ""),
        ("GOODS_SCOPE_OBSERVED", ""),
        ("DERIVED_CASE_OBSERVED", ""),
        ("EXCLUSIVE_TERM_OBSERVED", ""),
        ("PRELIMINARY_PUBLICATION_OBSERVED", ""),
        ("REGISTRATION_PUBLICATION_OBSERVED", ""),
        ("EXCLUSIVE_TERM_OBSERVED", '{"from":"2020-01-01"}'),
        ("PRELIMINARY_PUBLICATION_OBSERVED", '{"date":"2020-01-01"}'),
        ("REGISTRATION_PUBLICATION_OBSERVED", '{"date":"2021-01-01"}'),
        ("CASE_FACTS_CHANGED_OBSERVED", "old-case-hash"),
        ("MARK_NAME_CHANGED_OBSERVED", "OLD MARK"),
        ("GOODS_SCOPE_CHANGED_OBSERVED", '{"scope_hash":"old"}'),
        ("TERM_EXTENDED_OBSERVED", '{"until":"2030-01-01"}'),
        ("AGENT_CODE_CHANGED_OBSERVED", "OLD_AGENT"),
        ("OWNER_RELATION_OBSERVED", ""),
        ("CO_OWNER_RELATION_OBSERVED", ""),
        ("AGENT_RELATION_OBSERVED", ""),
        ("OWNER_RELATION_SUPERSEDED_OBSERVED", '{"name":"old"}'),
    ]
    for index, (event_type, old_value) in enumerate(fixture_rows, start=1):
        _insert_event(index, event_type, old_value)

    plan = build_plan(client=client)
    if plan["source_rows"] != 18:
        raise AssertionError(plan)
    if plan["reconstructible_baseline_candidate_rows"] != 6:
        raise AssertionError(plan)
    if plan["keep_rows"] != 12 or not plan["safe_to_commit"]:
        raise AssertionError(plan)

    # Build the exact pre-exchange state and prove ClickHouse's DROP guard can
    # block it. commit_compaction must then resume that state and use only the
    # query-scoped validated large-table override.
    client.command(f"CREATE TABLE {DATABASE}.{SHADOW_TABLE} AS {DATABASE}.{SOURCE_TABLE}")
    client.command(
        f"""
        INSERT INTO {DATABASE}.{SHADOW_TABLE}
        SELECT * FROM {DATABASE}.{SOURCE_TABLE}
        WHERE NOT (
            event_type IN ('APPLICATION_OBSERVED', 'DERIVED_CASE_OBSERVED', 'GOODS_SCOPE_OBSERVED')
            OR (
                event_type IN ('EXCLUSIVE_TERM_OBSERVED', 'PRELIMINARY_PUBLICATION_OBSERVED', 'REGISTRATION_PUBLICATION_OBSERVED')
                AND old_value_compact = ''
            )
        )
        """
    )
    client.command(
        f"EXCHANGE TABLES {DATABASE}.{SOURCE_TABLE} AND {DATABASE}.{SHADOW_TABLE}"
    )

    drop_guard_blocked = False
    try:
        client.command(
            f"DROP TABLE {DATABASE}.{SHADOW_TABLE} SYNC",
            settings={"max_table_size_to_drop": 1},
        )
    except Exception as exc:
        drop_guard_blocked = "TABLE_SIZE_EXCEEDS_MAX_DROP_SIZE_LIMIT" in str(exc) or "code 359" in str(exc).lower()
    if not drop_guard_blocked:
        raise AssertionError("fixture did not reproduce the ClickHouse large-table DROP guard")

    result = commit_compaction(client=client)
    if result.get("status") != "COMMITTED_FINAL":
        raise AssertionError(result)
    if not result.get("resumed_pending_drop"):
        raise AssertionError(result)
    if _table_exists(client, SHADOW_TABLE):
        raise AssertionError("event compaction left a shadow table")
    if _baseline_rows(client, SOURCE_TABLE) != 0:
        raise AssertionError("event compaction left baseline candidates active")
    remaining = client.query(
        f"SELECT count() FROM {DATABASE}.{SOURCE_TABLE}"
    ).result_rows[0][0]
    if int(remaining) != 12:
        raise AssertionError({"remaining": remaining, "result": result})

    print(
        json.dumps(
            {
                "status": "PASS",
                "plan_baseline_rows": 6,
                "plan_keep_rows": 12,
                "drop_guard_blocked_at_one_byte": drop_guard_blocked,
                "commit_status": result["status"],
                "resumed_pending_drop": result["resumed_pending_drop"],
                "remaining_rows": int(remaining),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
