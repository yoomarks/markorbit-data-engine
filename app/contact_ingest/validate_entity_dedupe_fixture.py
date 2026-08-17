from __future__ import annotations

import uuid

from app.contact_ingest.entity_dedupe import execute_entity_dedupe
from app.db import postgres_conn


CANONICAL_ID = uuid.UUID("91000000-0000-0000-0000-000000000001")
DUPLICATE_ID = uuid.UUID("91000000-0000-0000-0000-000000000002")
PERSON_ID = uuid.UUID("91000000-0000-0000-0000-000000000003")
RELATION_ID = uuid.UUID("91000000-0000-0000-0000-000000000004")
MENTION_ID = uuid.UUID("91000000-0000-0000-0000-000000000005")
NORMALIZED_NAME = "深圳示例去重科技有限公司"
NORMALIZED_ADDRESS = "深圳市南山区科技园测试大道100号"


def _seed() -> tuple[str, str]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO entity.entity (
                    entity_id, entity_key, entity_type, canonical_name, normalized_name,
                    normalized_address, country_code, status, resolution_method,
                    source_primary, confidence_score
                ) VALUES
                (%s, %s, 'TRADEMARK_PARTY', %s, %s, %s, 'CN', 'CANDIDATE',
                 'FIXTURE_OFFICIAL', 'CN_OFFICIAL', 0.9900),
                (%s, %s, 'ORGANIZATION', %s, %s, %s, 'CN', 'CANDIDATE',
                 'CONTACT_SOURCE_NEW_AMBIGUOUS', 'CONTACT_INGEST', 0.7500)
                """,
                (
                    CANONICAL_ID,
                    "91" + "0" * 62,
                    "深圳示例去重科技有限公司",
                    NORMALIZED_NAME,
                    NORMALIZED_ADDRESS,
                    DUPLICATE_ID,
                    "92" + "0" * 62,
                    "深圳示例去重科技有限公司",
                    NORMALIZED_NAME,
                    NORMALIZED_ADDRESS,
                ),
            )
            cur.execute(
                """
                INSERT INTO entity.entity_mention (
                    mention_id, jurisdiction, source_case_key, role, raw_name,
                    normalized_name, raw_address, normalized_address, country_code,
                    entity_id, match_status, resolution_method
                ) VALUES (%s, 'CN', 'DEDUPE-FIXTURE-1', 'APPLICANT', %s, %s, %s, %s,
                          'CN', %s, 'MATCHED', 'FIXTURE')
                """,
                (
                    MENTION_ID,
                    "深圳示例去重科技有限公司",
                    NORMALIZED_NAME,
                    "深圳市南山区科技园测试大道100号",
                    NORMALIZED_ADDRESS,
                    CANONICAL_ID,
                ),
            )
            cur.execute(
                """
                INSERT INTO contact.source (
                    source_sha256, source_name, file_type, source_profile,
                    source_segment, source_scope, default_country_code, ingest_version
                ) VALUES (%s, 'dedupe-fixture.xlsx', '.xlsx', 'QCC_COMPANY_EXPORT',
                          'DIRECT', 'CN', 'CN', 'FIXTURE')
                RETURNING source_id::text
                """,
                ("d1" * 32,),
            )
            source_id = str(cur.fetchone()["source_id"])
            cur.execute(
                """
                INSERT INTO contact.raw_record (
                    source_id, source_member, sheet_name, source_row, source_profile,
                    entity_id, entity_match_method, entity_match_confidence, raw_data
                ) VALUES (%s, '', 'Sheet1', 2, 'QCC_COMPANY_EXPORT', %s,
                          'CONTACT_SOURCE_NEW_AMBIGUOUS', 0.7500, %s::jsonb)
                RETURNING raw_record_id::text
                """,
                (
                    source_id,
                    DUPLICATE_ID,
                    '{"企业名称":"深圳示例去重科技有限公司","邮箱":"same@example.cn"}',
                ),
            )
            raw_record_id = str(cur.fetchone()["raw_record_id"])
            cur.execute(
                """
                INSERT INTO contact.person (
                    person_id, person_key, canonical_name, normalized_name, country_code
                ) VALUES (%s, %s, '张三', '张三', 'CN')
                """,
                (PERSON_ID, "93" + "0" * 62),
            )
            cur.execute(
                """
                INSERT INTO contact.entity_person_relation (
                    relation_id, entity_id, person_id, relation_type, title,
                    confidence_score, first_source_id, last_source_id
                ) VALUES (%s, %s, %s, 'CONTACT_PERSON', '经理', 0.9000, %s, %s)
                """,
                (RELATION_ID, DUPLICATE_ID, PERSON_ID, source_id, source_id),
            )
            cur.execute(
                """
                INSERT INTO contact.channel (
                    entity_id, channel_type, channel_value, normalized_value
                ) VALUES (%s, 'EMAIL', 'same@example.cn', 'same@example.cn')
                RETURNING channel_id::text
                """,
                (CANONICAL_ID,),
            )
            canonical_channel_id = str(cur.fetchone()["channel_id"])
            cur.execute(
                """
                INSERT INTO contact.channel (
                    entity_id, channel_type, channel_value, normalized_value
                ) VALUES (%s, 'EMAIL', 'same@example.cn', 'same@example.cn')
                RETURNING channel_id::text
                """,
                (DUPLICATE_ID,),
            )
            duplicate_channel_id = str(cur.fetchone()["channel_id"])
            cur.execute(
                """
                INSERT INTO contact.channel_observation (
                    observation_key, channel_id, source_id, raw_record_id,
                    source_column, raw_value, confidence_score, metadata
                ) VALUES
                (%s, %s, %s, %s, '邮箱', 'same@example.cn', 0.9000, '{}'::jsonb),
                (%s, %s, %s, %s, '邮箱', 'same@example.cn', 0.9000, '{}'::jsonb)
                """,
                (
                    "a1" * 32,
                    canonical_channel_id,
                    source_id,
                    raw_record_id,
                    "a2" * 32,
                    duplicate_channel_id,
                    source_id,
                    raw_record_id,
                ),
            )
            cur.execute(
                """
                INSERT INTO entity.entity_alias (
                    entity_id, alias_name, normalized_name, language_code,
                    source, confidence_score
                ) VALUES (%s, '深圳示例去重科技', '深圳示例去重科技', 'zh',
                          'CONTACT_INGEST', 0.9000)
                """,
                (DUPLICATE_ID,),
            )
            cur.execute(
                """
                INSERT INTO entity.entity_identifier (
                    entity_id, identifier_type, identifier_value, normalized_value,
                    country_code, source, confidence_score
                ) VALUES (%s, 'CN_USCC', '91440300DEDUPE001', '91440300DEDUPE001',
                          'CN', 'CONTACT_INGEST', 0.9900)
                """,
                (DUPLICATE_ID,),
            )
        conn.commit()
    return source_id, raw_record_id


def _assert_preview() -> None:
    result = execute_entity_dedupe(country_code="CN", apply=False)
    assert result["status"] == "SUCCESS"
    assert result["metrics"]["candidate_clusters"] == 1
    assert result["metrics"]["candidate_duplicates"] == 1
    assert result["metrics"]["applied_duplicates"] == 0
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM entity.entity WHERE entity_id = %s", (DUPLICATE_ID,))
            assert cur.fetchone()["status"] != "MERGED"
            cur.execute(
                """
                SELECT decision_status
                FROM contact.entity_merge_decision
                WHERE run_id = %s AND duplicate_entity_id = %s
                """,
                (result["run_id"], DUPLICATE_ID),
            )
            assert cur.fetchone()["decision_status"] == "CANDIDATE"


def _assert_busy_guard() -> None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO control.source_package (
                    jurisdiction, file_name, file_path, file_size, sha256, status
                ) VALUES ('CN', 'dedupe-busy.zip', '/tmp/dedupe-busy.zip', 1, %s, 'PROCESSING')
                RETURNING package_id
                """,
                ("b1" * 32,),
            )
            package_id = cur.fetchone()["package_id"]
        conn.commit()
    try:
        try:
            execute_entity_dedupe(country_code="CN", apply=True)
        except RuntimeError as exc:
            assert str(exc) == "CONTACT_ENTITY_DEDUPE_APPLY_BLOCKED_ACTIVE_SOURCE_PACKAGE"
        else:
            raise AssertionError("apply should be blocked while a source package is PROCESSING")
    finally:
        with postgres_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM control.source_package WHERE package_id = %s", (package_id,))
            conn.commit()


def _assert_apply(source_id: str, raw_record_id: str) -> None:
    result = execute_entity_dedupe(country_code="CN", apply=True)
    assert result["status"] == "SUCCESS"
    assert result["metrics"]["candidate_duplicates"] == 1
    assert result["metrics"]["applied_duplicates"] == 1

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, resolution_method FROM entity.entity WHERE entity_id = %s",
                (DUPLICATE_ID,),
            )
            duplicate = dict(cur.fetchone())
            assert duplicate["status"] == "MERGED"
            assert duplicate["resolution_method"] == "CONTACT_ENTITY_DEDUPE_V1"

            cur.execute(
                "SELECT entity_id::text, source_id::text FROM contact.raw_record WHERE raw_record_id = %s",
                (raw_record_id,),
            )
            raw = dict(cur.fetchone())
            assert raw["entity_id"] == str(CANONICAL_ID)
            assert raw["source_id"] == source_id

            cur.execute(
                """
                SELECT count(*) AS n
                FROM contact.entity_person_relation
                WHERE entity_id = %s AND person_id = %s
                """,
                (CANONICAL_ID, PERSON_ID),
            )
            assert int(cur.fetchone()["n"]) == 1
            cur.execute(
                "SELECT count(*) AS n FROM contact.entity_person_relation WHERE entity_id = %s",
                (DUPLICATE_ID,),
            )
            assert int(cur.fetchone()["n"]) == 0

            cur.execute(
                """
                SELECT channel_id::text
                FROM contact.channel
                WHERE entity_id = %s AND channel_type = 'EMAIL'
                  AND normalized_value = 'same@example.cn'
                """,
                (CANONICAL_ID,),
            )
            canonical_channel_id = str(cur.fetchone()["channel_id"])
            cur.execute(
                "SELECT count(*) AS n FROM contact.channel WHERE entity_id = %s",
                (DUPLICATE_ID,),
            )
            assert int(cur.fetchone()["n"]) == 0
            cur.execute(
                """
                SELECT count(*) AS n, count(DISTINCT source_id) AS sources
                FROM contact.channel_observation
                WHERE channel_id = %s
                """,
                (canonical_channel_id,),
            )
            observations = dict(cur.fetchone())
            assert int(observations["n"]) == 2
            assert int(observations["sources"]) == 1

            cur.execute(
                """
                SELECT entity_id::text
                FROM entity.entity_identifier
                WHERE identifier_type = 'CN_USCC'
                  AND normalized_value = '91440300DEDUPE001'
                """
            )
            assert cur.fetchone()["entity_id"] == str(CANONICAL_ID)
            cur.execute(
                """
                SELECT count(*) AS n
                FROM entity.entity_alias
                WHERE entity_id = %s AND normalized_name = '深圳示例去重科技'
                  AND source = 'CONTACT_INGEST'
                """,
                (CANONICAL_ID,),
            )
            assert int(cur.fetchone()["n"]) == 1
            cur.execute(
                "SELECT entity_id::text FROM entity.entity_mention WHERE mention_id = %s",
                (MENTION_ID,),
            )
            assert cur.fetchone()["entity_id"] == str(CANONICAL_ID)

            cur.execute(
                """
                SELECT decision_status, applied_at IS NOT NULL AS applied
                FROM contact.entity_merge_decision
                WHERE run_id = %s AND duplicate_entity_id = %s
                """,
                (result["run_id"], DUPLICATE_ID),
            )
            decision = dict(cur.fetchone())
            assert decision["decision_status"] == "APPLIED"
            assert decision["applied"] is True

    repeated = execute_entity_dedupe(country_code="CN", apply=True)
    assert repeated["status"] == "SUCCESS"
    assert repeated["metrics"]["candidate_duplicates"] == 0
    assert repeated["metrics"]["applied_duplicates"] == 0


def main() -> None:
    source_id, raw_record_id = _seed()
    _assert_preview()
    _assert_busy_guard()
    _assert_apply(source_id, raw_record_id)
    print("CONTACT_ENTITY_DEDUPE_FIXTURE_PASS")


if __name__ == "__main__":
    main()
