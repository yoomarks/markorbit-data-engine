from __future__ import annotations

import json

from app.cn.storage_v2_party_history_compaction import (
    DATABASE,
    EVENT_TABLE,
    SHADOW_TABLE,
    SOURCE_TABLE,
    build_plan,
    build_status,
    commit_compaction,
)
from app.db import clickhouse_client


def _insert_event(
    client,
    *,
    event_id: str,
    role: str,
    kind: str,
    relation_key_char: str,
    source_rank: int,
    hash_char: str,
) -> None:
    if kind == "OBSERVED":
        event_type = f"{role}_RELATION_OBSERVED"
    elif kind == "SUPERSEDED":
        event_type = f"{role}_RELATION_SUPERSEDED_OBSERVED"
    else:
        raise ValueError(f"Unknown fixture PARTY event kind: {kind}")
    relation_key = relation_key_char * 64
    if kind == "OBSERVED":
        old_value = ""
        new_value = json.dumps(
            {
                "name": f"{role}-{relation_key_char}",
                "address": "fixture",
                "relation_key": relation_key,
                "entity_id": "",
            },
            separators=(",", ":"),
        )
    else:
        old_value = json.dumps(
            {
                "name": f"{role}-{relation_key_char}",
                "address": "fixture",
                "relation_key": relation_key,
            },
            separators=(",", ":"),
        )
        new_value = ""
    old_sql = old_value.replace("'", "\\'")
    new_sql = new_value.replace("'", "\\'")
    client.command(
        f"""
        INSERT INTO {DATABASE}.{EVENT_TABLE}
        (event_id, case_id, application_number, event_type, event_date, observed_at,
         affected_scope, class_no, field_name, old_value_compact, new_value_compact,
         evidence_level, legal_effect, confidence_score, source_package_id,
         source_package_kind, source_file, source_first_line, source_last_line,
         source_row_hash, source_rank, event_hash)
        VALUES
        (toUUID('{event_id}'), toUUID('00000000-0000-0000-0000-000000000100'),
         'APP-{relation_key_char}', '{event_type}', NULL, now64(3), 'PARTY', NULL,
         '{role.lower()}', '{old_sql}', '{new_sql}', 'OFFICIAL_FACT_OBSERVATION',
         'NOT_DETERMINED', 1.0,
         toUUID('00000000-0000-0000-0000-000000000200'), 'FIXTURE', 'fixture.xml',
         1, 1, repeat('{hash_char}', 64), {source_rank}, repeat('{hash_char}', 64))
        """
    )


def _insert_history(
    client,
    *,
    history_id: str,
    role: str,
    action: str,
    relation_key_char: str,
    source_rank: int,
    hash_char: str,
) -> None:
    relation_key = relation_key_char * 64
    client.command(
        f"""
        INSERT INTO {DATABASE}.{SOURCE_TABLE}
        (history_id, relation_id, case_id, application_number, role, action,
         effective_date, relation_key, mention_id, entity_id, raw_name, raw_address,
         source_package_id, source_package_kind, source_file, source_first_line,
         source_last_line, source_row_hash, source_rank, history_hash, observed_at)
        VALUES
        (toUUID('{history_id}'), toUUID('00000000-0000-0000-0000-000000000300'),
         toUUID('00000000-0000-0000-0000-000000000100'), 'APP-{relation_key_char}',
         '{role}', '{action}', NULL, '{relation_key}',
         toUUID('00000000-0000-0000-0000-000000000400'), NULL,
         '{role}-{relation_key_char}', 'fixture',
         toUUID('00000000-0000-0000-0000-000000000200'), 'FIXTURE', 'fixture.xml',
         1, 1, repeat('{hash_char}', 64), {source_rank}, repeat('{hash_char}', 64),
         now64(3))
        """
    )


def main() -> int:
    client = clickhouse_client()
    client.command(
        f"DROP TABLE IF EXISTS {DATABASE}.{SHADOW_TABLE} SYNC",
        settings={"max_table_size_to_drop": 0},
    )
    client.command(f"TRUNCATE TABLE {DATABASE}.{SOURCE_TABLE}")
    client.command(f"TRUNCATE TABLE {DATABASE}.{EVENT_TABLE}")

    events = [
        ("00000000-0000-0000-0000-000000001001", "OWNER", "OBSERVED", "a", 10, "a"),
        ("00000000-0000-0000-0000-000000001002", "OWNER", "OBSERVED", "b", 20, "b"),
        ("00000000-0000-0000-0000-000000001003", "AGENT", "OBSERVED", "c", 10, "c"),
        ("00000000-0000-0000-0000-000000001004", "CO_OWNER", "OBSERVED", "d", 10, "d"),
        ("00000000-0000-0000-0000-000000001005", "OWNER", "SUPERSEDED", "a", 30, "e"),
    ]
    for args in events:
        _insert_event(
            client,
            event_id=args[0],
            role=args[1],
            kind=args[2],
            relation_key_char=args[3],
            source_rank=args[4],
            hash_char=args[5],
        )

    history = [
        ("00000000-0000-0000-0000-000000002001", "OWNER", "OBSERVED_CURRENT", "a", 10, "f"),
        ("00000000-0000-0000-0000-000000002002", "OWNER", "OBSERVED_CURRENT", "b", 20, "g"),
        ("00000000-0000-0000-0000-000000002003", "AGENT", "OBSERVED_CURRENT", "c", 10, "h"),
        ("00000000-0000-0000-0000-000000002004", "CO_OWNER", "OBSERVED_CURRENT", "d", 10, "i"),
        ("00000000-0000-0000-0000-000000002005", "OWNER", "SUPERSEDED", "a", 30, "j"),
        # Pre-Storage-V2 legacy history wrote unchanged OBSERVED_CURRENT rows even
        # though the canonical event publisher correctly emitted no second event.
        ("00000000-0000-0000-0000-000000002006", "OWNER", "OBSERVED_CURRENT", "a", 15, "k"),
    ]
    for args in history:
        _insert_history(
            client,
            history_id=args[0],
            role=args[1],
            action=args[2],
            relation_key_char=args[3],
            source_rank=args[4],
            hash_char=args[5],
        )

    plan = build_plan(client=client)
    assert plan["safe_to_commit"] is True, plan
    assert plan["source_rows"] == 6, plan
    assert plan["coverage"]["canonical_party_event_rows"] == 5, plan
    assert plan["coverage"]["legacy_extra_noop_history_rows"] == 1, plan

    # Exercise the resumable pre-EXCHANGE state instead of only the happy path.
    client.command(
        f"CREATE TABLE {DATABASE}.{SHADOW_TABLE} AS {DATABASE}.{SOURCE_TABLE}"
    )
    status = build_status(client=client)
    assert status["state"] == "PRE_EXCHANGE_EMPTY_SHADOW", status

    result = commit_compaction(client=client)
    assert result["status"] == "COMMITTED_FINAL", result
    assert result["resumed_pre_exchange"] is True, result
    assert result["dropped_duplicate_history_rows"] == 6, result

    final_status = build_status(client=client)
    assert final_status["state"] == "CANONICAL_EVENT_ONLY", final_status
    assert final_status["source_rows"] == 0, final_status
    remaining_events = client.query(
        f"SELECT count() FROM {DATABASE}.{EVENT_TABLE}"
    ).result_rows[0][0]
    assert int(remaining_events) == 5, remaining_events

    print(
        json.dumps(
            {
                "status": "PASS",
                "plan_history_rows": plan["source_rows"],
                "canonical_party_event_rows": plan["coverage"]["canonical_party_event_rows"],
                "legacy_extra_noop_history_rows": plan["coverage"]["legacy_extra_noop_history_rows"],
                "commit_status": result["status"],
                "resumed_pre_exchange": result["resumed_pre_exchange"],
                "remaining_history_rows": final_status["source_rows"],
                "remaining_party_events": int(remaining_events),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
