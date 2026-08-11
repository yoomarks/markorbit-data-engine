from __future__ import annotations

import json
import uuid

from app.cn.ingest import _party_aggregate_sql
from app.cn.party_publish import (
    cleanup_party_publish_stage,
    ensure_party_publish_schema,
    materialize_party_publish_stage,
)
from app.db import clickhouse_client


PACKAGE_ID = uuid.UUID("00000000-0000-0000-0000-00000000d016")
CASE_A = uuid.UUID("00000000-0000-0000-0000-00000000a101")
CASE_B = uuid.UUID("00000000-0000-0000-0000-00000000b101")
REL_OWNER_A = uuid.UUID("00000000-0000-0000-0000-000000001001")
REL_COOWNER_A = uuid.UUID("00000000-0000-0000-0000-000000001002")
REL_AGENT_A = uuid.UUID("00000000-0000-0000-0000-000000001003")
REL_OWNER_B = uuid.UUID("00000000-0000-0000-0000-000000001004")
MENTION_OWNER_A = uuid.UUID("00000000-0000-0000-0000-000000002001")
MENTION_COOWNER_A = uuid.UUID("00000000-0000-0000-0000-000000002002")
MENTION_AGENT_A = uuid.UUID("00000000-0000-0000-0000-000000002003")
MENTION_OWNER_B = uuid.UUID("00000000-0000-0000-0000-000000002004")
ZERO_UUID = uuid.UUID(int=0)
OWNER_A_KEY = "a" * 64
COOWNER_A_KEY = "b" * 64
AGENT_A_KEY = "c" * 64
OWNER_B_KEY = "d" * 64
EMPTY_KEY = "0" * 64


def _cleanup_fixture() -> None:
    client = clickhouse_client()
    package = str(PACKAGE_ID)
    ensure_party_publish_schema(client=client)
    cleanup_party_publish_stage(PACKAGE_ID, client=client)
    for table in (
        "cn_stage_applicant",
        "cn_stage_coowner",
        "cn_stage_basic",
        "cn_stage_agent",
    ):
        client.command(
            f"ALTER TABLE markorbit_facts.{table} DELETE WHERE package_id = "
            f"toUUID('{package}') SETTINGS mutations_sync = 1"
        )


def _stage_fixture() -> None:
    client = clickhouse_client()
    package = str(PACKAGE_ID)

    client.command(f"""
        INSERT INTO markorbit_facts.cn_stage_basic
        (
            package_id, case_id, family_root_case_id, application_number,
            case_family_root, filing_route, number_family, relation_id, class_no,
            agent_code, agent_relation_id, agent_relation_key, agent_mention_id,
            source_file, source_start_line, source_end_line, row_hash
        ) VALUES
        (
            toUUID('{package}'), toUUID('{CASE_A}'), toUUID('{CASE_A}'), 'A101',
            'A101', 'DIRECT_CN', 'CN_DIRECT', toUUID('{ZERO_UUID}'), 9,
            'AG001', toUUID('{REL_AGENT_A}'), '{AGENT_A_KEY}',
            toUUID('{MENTION_AGENT_A}'), 'basic.csv', 1, 1, '{'1' * 64}'
        ),
        (
            toUUID('{package}'), toUUID('{CASE_B}'), toUUID('{CASE_B}'), 'B101',
            'B101', 'DIRECT_CN', 'CN_DIRECT', toUUID('{ZERO_UUID}'), 25,
            '', toUUID('{ZERO_UUID}'), '{EMPTY_KEY}', toUUID('{ZERO_UUID}'),
            'basic.csv', 2, 2, '{'2' * 64}'
        )
    """)

    client.command(f"""
        INSERT INTO markorbit_facts.cn_stage_applicant
        (
            package_id, case_id, application_number, class_no, relation_id,
            relation_key, mention_id, entity_id, raw_name, normalized_name,
            raw_address, normalized_address, country_code, region_code, city,
            geo_confidence, source_file, source_start_line, source_end_line, row_hash
        ) VALUES
        (
            toUUID('{package}'), toUUID('{CASE_A}'), 'A101', 9,
            toUUID('{REL_OWNER_A}'), '{OWNER_A_KEY}', toUUID('{MENTION_OWNER_A}'),
            NULL, 'Alpha Owner', 'alpha owner', 'Shanghai', 'shanghai',
            'CN', 'SH', 'Shanghai', 0.95, 'owner.csv', 1, 1, '{'3' * 64}'
        ),
        (
            toUUID('{package}'), toUUID('{CASE_B}'), 'B101', 25,
            toUUID('{REL_OWNER_B}'), '{OWNER_B_KEY}', toUUID('{MENTION_OWNER_B}'),
            NULL, 'Beta Owner', 'beta owner', 'Beijing', 'beijing',
            'CN', 'BJ', 'Beijing', 0.95, 'owner.csv', 2, 2, '{'4' * 64}'
        )
    """)

    client.command(f"""
        INSERT INTO markorbit_facts.cn_stage_coowner
        (
            package_id, case_id, application_number, relation_id, relation_key,
            mention_id, entity_id, raw_name, normalized_name, raw_address,
            normalized_address, country_code, region_code, city, geo_confidence,
            source_file, source_start_line, source_end_line, row_hash
        ) VALUES
        (
            toUUID('{package}'), toUUID('{CASE_A}'), 'A101',
            toUUID('{REL_COOWNER_A}'), '{COOWNER_A_KEY}',
            toUUID('{MENTION_COOWNER_A}'), NULL, 'Alpha Coowner', 'alpha coowner',
            'Shenzhen', 'shenzhen', 'CN', 'GD', 'Shenzhen', 0.90,
            'coowner.csv', 1, 1, '{'5' * 64}'
        )
    """)

    client.command(f"""
        INSERT INTO markorbit_facts.cn_stage_agent
        (
            package_id, relation_id, mention_id, entity_id, agent_code,
            agent_name, agent_name_norm, source_file, source_start_line,
            source_end_line, row_hash
        ) VALUES
        (
            toUUID('{package}'), toUUID('{REL_AGENT_A}'), toUUID('{MENTION_AGENT_A}'),
            NULL, 'AG001', 'Fixture Agent', 'fixture agent', 'agent.csv', 1, 1,
            '{'6' * 64}'
        )
    """)


def _party_rows(sql: str) -> list[tuple]:
    return clickhouse_client().query(f"""
        SELECT
            application_number, role, toString(relation_key), raw_name,
            class_nos, toString(record_hash)
        FROM ({sql})
        ORDER BY application_number, role, relation_key
    """).result_rows


def main() -> None:
    package = str(PACKAGE_ID)
    try:
        _cleanup_fixture()
        _stage_fixture()

        expected = _party_rows(_party_aggregate_sql(package))
        metrics = materialize_party_publish_stage(
            PACKAGE_ID,
            _party_aggregate_sql,
            target_rows=1,
        )
        actual = _party_rows(f"""
            SELECT
                case_id, application_number, role, relation_id, relation_key,
                mention_id, entity_id, agent_code, raw_name, normalized_name,
                raw_address, normalized_address, country_code, region_code, city,
                class_nos, confidence_score, source_file, source_first_line,
                source_last_line, source_row_hash, record_hash
            FROM markorbit_facts.cn_stage_party_publish
            WHERE package_id = toUUID('{package}')
        """)

        if expected != actual:
            raise AssertionError({"expected": expected, "actual": actual})
        if metrics != {"party_publish_rows": 4, "party_publish_chunk_count": 2}:
            raise AssertionError(metrics)

        print(
            json.dumps(
                {
                    "status": "PASS",
                    "package_id": package,
                    "metrics": metrics,
                    "roles": [row[1] for row in actual],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        _cleanup_fixture()


if __name__ == "__main__":
    main()
