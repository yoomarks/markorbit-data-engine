from __future__ import annotations

import uuid

from app.contact_ingest.person_dedupe import execute_person_dedupe
from app.db import postgres_conn


ENTITY_ID = uuid.UUID("92000000-0000-0000-0000-000000000001")
PERSON_A = uuid.UUID("92000000-0000-0000-0000-000000000002")
PERSON_B = uuid.UUID("92000000-0000-0000-0000-000000000003")
REL_A1 = uuid.UUID("92000000-0000-0000-0000-000000000004")
REL_A2 = uuid.UUID("92000000-0000-0000-0000-000000000005")
REL_B = uuid.UUID("92000000-0000-0000-0000-000000000006")


def _seed() -> str:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO entity.entity (
                    entity_id, entity_key, entity_type, canonical_name, normalized_name,
                    normalized_address, country_code, status, resolution_method,
                    source_primary, confidence_score
                ) VALUES (%s, %s, 'ORGANIZATION', '深圳联系人去重测试有限公司',
                          '深圳联系人去重测试有限公司', '深圳市南山区测试路1号', 'CN',
                          'CANDIDATE', 'FIXTURE', 'CONTACT_INGEST', 0.9000)
                """,
                (ENTITY_ID, "94" + "0" * 62),
            )
            cur.execute(
                """
                INSERT INTO contact.source (
                    source_sha256, source_name, file_type, source_profile,
                    source_segment, source_scope, default_country_code, ingest_version
                ) VALUES (%s, 'person-dedupe-fixture.xlsx', '.xlsx', 'QCC_COMPANY_EXPORT',
                          'DIRECT', 'CN', 'CN', 'FIXTURE')
                RETURNING source_id::text
                """,
                ("e1" * 32,),
            )
            source_id = str(cur.fetchone()["source_id"])
            cur.execute(
                """
                INSERT INTO contact.person (
                    person_id, person_key, canonical_name, normalized_name,
                    country_code, first_seen_at
                ) VALUES
                (%s, %s, '张三', '张三', 'CN', now() - interval '2 days'),
                (%s, %s, '张三', '张三', 'CN', now() - interval '1 day')
                """,
                (
                    PERSON_A,
                    "95" + "0" * 62,
                    PERSON_B,
                    "96" + "0" * 62,
                ),
            )
            cur.execute(
                """
                INSERT INTO contact.entity_person_relation (
                    relation_id, entity_id, person_id, relation_type, title,
                    department, confidence_score, first_source_id, last_source_id
                ) VALUES
                (%s, %s, %s, 'CONTACT_PERSON', '经理', '业务部', 0.9000, %s, %s),
                (%s, %s, %s, 'DIRECTOR', '董事', '', 0.9000, %s, %s),
                (%s, %s, %s, 'CONTACT_PERSON', '高级经理', '业务部', 0.9500, %s, %s)
                """,
                (
                    REL_A1,
                    ENTITY_ID,
                    PERSON_A,
                    source_id,
                    source_id,
                    REL_A2,
                    ENTITY_ID,
                    PERSON_A,
                    source_id,
                    source_id,
                    REL_B,
                    ENTITY_ID,
                    PERSON_B,
                    source_id,
                    source_id,
                ),
            )
            cur.execute(
                """
                INSERT INTO contact.channel (
                    person_id, channel_type, channel_value, normalized_value
                ) VALUES (%s, 'EMAIL', 'zhangsan@example.cn', 'zhangsan@example.cn')
                RETURNING channel_id::text
                """,
                (PERSON_A,),
            )
            channel_a = str(cur.fetchone()["channel_id"])
            cur.execute(
                """
                INSERT INTO contact.channel (
                    person_id, channel_type, channel_value, normalized_value
                ) VALUES (%s, 'EMAIL', 'zhangsan@example.cn', 'zhangsan@example.cn')
                RETURNING channel_id::text
                """,
                (PERSON_B,),
            )
            channel_b = str(cur.fetchone()["channel_id"])
            cur.execute(
                """
                INSERT INTO contact.channel_observation (
                    observation_key, channel_id, source_id, source_column,
                    raw_value, confidence_score, metadata
                ) VALUES
                (%s, %s, %s, '邮箱', 'zhangsan@example.cn', 0.9000, '{}'::jsonb),
                (%s, %s, %s, '邮箱', 'zhangsan@example.cn', 0.9000, '{}'::jsonb)
                """,
                (
                    "c1" * 32,
                    channel_a,
                    source_id,
                    "c2" * 32,
                    channel_b,
                    source_id,
                ),
            )
        conn.commit()
    return source_id


def _assert_preview() -> str:
    result = execute_person_dedupe(country_code="CN", apply=False)
    assert result["status"] == "SUCCESS"
    assert result["metrics"]["candidate_clusters"] == 1
    assert result["metrics"]["candidate_duplicates"] == 1
    assert result["metrics"]["applied_duplicates"] == 0
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT canonical_person_id::text, duplicate_person_id::text, decision_status
                FROM contact.person_merge_decision
                WHERE run_id = %s
                """,
                (result["run_id"],),
            )
            decision = dict(cur.fetchone())
            assert decision["canonical_person_id"] == str(PERSON_A)
            assert decision["duplicate_person_id"] == str(PERSON_B)
            assert decision["decision_status"] == "CANDIDATE"
            cur.execute("SELECT status FROM contact.person WHERE person_id = %s", (PERSON_B,))
            assert cur.fetchone()["status"] != "MERGED"
    return result["run_id"]


def _assert_busy_guard() -> None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO control.source_package (
                    jurisdiction, file_name, file_path, file_size, sha256, status
                ) VALUES ('CN', 'person-dedupe-busy.zip', '/tmp/person-dedupe-busy.zip',
                          1, %s, 'PROCESSING')
                RETURNING package_id
                """,
                ("e2" * 32,),
            )
            package_id = cur.fetchone()["package_id"]
        conn.commit()
    try:
        try:
            execute_person_dedupe(country_code="CN", apply=True)
        except RuntimeError as exc:
            assert str(exc) == "CONTACT_PERSON_DEDUPE_APPLY_BLOCKED_ACTIVE_SOURCE_PACKAGE"
        else:
            raise AssertionError("person dedupe apply should block active source packages")
    finally:
        with postgres_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM control.source_package WHERE package_id = %s", (package_id,))
            conn.commit()


def _assert_apply(source_id: str) -> None:
    result = execute_person_dedupe(country_code="CN", apply=True)
    assert result["status"] == "SUCCESS"
    assert result["metrics"]["candidate_duplicates"] == 1
    assert result["metrics"]["applied_duplicates"] == 1

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM contact.person WHERE person_id = %s", (PERSON_B,))
            assert cur.fetchone()["status"] == "MERGED"
            cur.execute("SELECT status FROM contact.person WHERE person_id = %s", (PERSON_A,))
            assert cur.fetchone()["status"] != "MERGED"

            cur.execute(
                """
                SELECT relation_type, title, department
                FROM contact.entity_person_relation
                WHERE entity_id = %s AND person_id = %s
                ORDER BY relation_type
                """,
                (ENTITY_ID, PERSON_A),
            )
            relations = [dict(row) for row in cur.fetchall()]
            assert [row["relation_type"] for row in relations] == ["CONTACT_PERSON", "DIRECTOR"]
            contact_relation = next(row for row in relations if row["relation_type"] == "CONTACT_PERSON")
            assert contact_relation["title"] == "高级经理"
            assert contact_relation["department"] == "业务部"
            cur.execute(
                "SELECT count(*) AS n FROM contact.entity_person_relation WHERE person_id = %s",
                (PERSON_B,),
            )
            assert int(cur.fetchone()["n"]) == 0

            cur.execute(
                """
                SELECT channel_id::text
                FROM contact.channel
                WHERE person_id = %s AND channel_type = 'EMAIL'
                  AND normalized_value = 'zhangsan@example.cn'
                """,
                (PERSON_A,),
            )
            channel_id = str(cur.fetchone()["channel_id"])
            cur.execute(
                "SELECT count(*) AS n FROM contact.channel WHERE person_id = %s",
                (PERSON_B,),
            )
            assert int(cur.fetchone()["n"]) == 0
            cur.execute(
                """
                SELECT count(*) AS n, count(DISTINCT source_id) AS sources
                FROM contact.channel_observation
                WHERE channel_id = %s
                """,
                (channel_id,),
            )
            observations = dict(cur.fetchone())
            assert int(observations["n"]) == 2
            assert int(observations["sources"]) == 1
            assert source_id

            cur.execute(
                """
                SELECT decision_status, applied_at IS NOT NULL AS applied
                FROM contact.person_merge_decision
                WHERE run_id = %s AND duplicate_person_id = %s
                """,
                (result["run_id"], PERSON_B),
            )
            decision = dict(cur.fetchone())
            assert decision["decision_status"] == "APPLIED"
            assert decision["applied"] is True

    repeated = execute_person_dedupe(country_code="CN", apply=True)
    assert repeated["status"] == "SUCCESS"
    assert repeated["metrics"]["candidate_duplicates"] == 0
    assert repeated["metrics"]["applied_duplicates"] == 0


def main() -> None:
    source_id = _seed()
    _assert_preview()
    _assert_busy_guard()
    _assert_apply(source_id)
    print("CONTACT_PERSON_DEDUPE_FIXTURE_PASS")


if __name__ == "__main__":
    main()
