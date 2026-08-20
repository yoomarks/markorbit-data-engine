from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import uuid

from app.cn_qcc.exporter import export_batch
from app.cn_qcc.incoming import ingest_result
from app.cn_qcc.migrations import ensure_qcc_schema
from app.cn_qcc.planner import create_batch_from_candidates
from app.cn_qcc.policy import QccCandidate
from app.db import postgres_conn


_ENTITY_ID = uuid.UUID("e413e6e8-834d-4c0e-8ad7-fac80b047d91")
_ENTITY_KEY = hashlib.sha256(b"CN_QCC_FIXTURE_COMPANY").hexdigest()


def _reset() -> None:
    ensure_qcc_schema()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM contact.channel_observation
                WHERE source_id IN (
                    SELECT source_id FROM contact.source
                    WHERE source_profile = 'QICHACHA_COMPANY_ENRICHMENT'
                      AND source_scope LIKE 'fixture-%'
                )
                """
            )
            cur.execute(
                """
                DELETE FROM contact.raw_record
                WHERE source_id IN (
                    SELECT source_id FROM contact.source
                    WHERE source_profile = 'QICHACHA_COMPANY_ENRICHMENT'
                      AND source_scope LIKE 'fixture-%'
                )
                """
            )
            cur.execute(
                """
                DELETE FROM contact.import_run
                WHERE source_id IN (
                    SELECT source_id FROM contact.source
                    WHERE source_profile = 'QICHACHA_COMPANY_ENRICHMENT'
                      AND source_scope LIKE 'fixture-%'
                )
                """
            )
            cur.execute(
                """
                DELETE FROM contact.source
                WHERE source_profile = 'QICHACHA_COMPANY_ENRICHMENT'
                  AND source_scope LIKE 'fixture-%'
                """
            )
            cur.execute("DELETE FROM acquisition.cn_qcc_company_observation WHERE entity_id = %s", (_ENTITY_ID,))
            cur.execute("DELETE FROM acquisition.cn_qcc_task WHERE entity_id = %s", (_ENTITY_ID,))
            cur.execute("DELETE FROM acquisition.cn_qcc_company_coverage WHERE entity_id = %s", (_ENTITY_ID,))
            cur.execute("DELETE FROM acquisition.cn_qcc_batch WHERE batch_key LIKE 'CN_QCC_FIXTURE_%'")
            cur.execute(
                """
                DELETE FROM contact.entity_person_relation WHERE entity_id = %s;
                DELETE FROM contact.channel WHERE entity_id = %s;
                DELETE FROM entity.entity_identifier WHERE entity_id = %s;
                DELETE FROM entity.entity WHERE entity_id = %s;
                """,
                (_ENTITY_ID, _ENTITY_ID, _ENTITY_ID, _ENTITY_ID),
            )
            cur.execute(
                """
                INSERT INTO entity.entity (
                    entity_id, entity_key, entity_type, canonical_name,
                    normalized_name, normalized_address, country_code,
                    region_code, city, resolution_method, source_primary, confidence_score
                ) VALUES (
                    %s, %s, 'TRADEMARK_PARTY', '北京轨道示例科技有限公司',
                    '北京轨道示例科技有限公司', '北京市朝阳区示例路1号', 'CN',
                    'BJ', '北京', 'EXACT_NAME_ADDRESS', 'CN', 0.9500
                )
                """,
                (_ENTITY_ID, _ENTITY_KEY),
            )
        conn.commit()


def main() -> None:
    _reset()
    candidate = QccCandidate(
        entity_id=str(_ENTITY_ID),
        applicant_name="北京轨道示例科技有限公司",
        normalized_name="北京轨道示例科技有限公司",
        applicant_address="北京市朝阳区示例路1号",
        country_code="CN",
        region_code="BJ",
        city="北京",
        trademark_count=23,
        latest_application_number="79990001",
        source_rank=987654,
        source_fingerprint="a" * 64,
        lane_reason="HISTORICAL_BACKFILL",
    )
    plan = create_batch_from_candidates(
        [candidate],
        capacity=10,
        refresh_days=180,
        source_watermark_to=(987654, str(_ENTITY_ID)),
    )

    # Rename the generated key to a fixture prefix so cleanup is deterministic.
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE acquisition.cn_qcc_batch SET batch_key = %s WHERE batch_id = %s",
                (f"CN_QCC_FIXTURE_{plan.batch_id}", plan.batch_id),
            )
        conn.commit()

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        task_csv = root / "qcc_tasks.csv"
        exported = export_batch(plan.batch_id, task_csv)
        assert exported["task_count"] == 1, exported

        with task_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            task_rows = list(csv.DictReader(handle))
        assert len(task_rows) == 1
        task_id = task_rows[0]["task_id"]
        assert task_rows[0]["applicant_name"] == "北京轨道示例科技有限公司"
        assert task_rows[0]["trademark_count"] == "23"

        result_csv = root / "qcc_result.csv"
        with result_csv.open("w", encoding="utf-8-sig", newline="") as handle:
            fieldnames = [
                "task_id", "entity_id", "result_status", "fetched_at",
                "qcc_company_id", "company_name", "unified_social_credit_code",
                "legal_representative", "registration_status", "registered_capital",
                "establishment_date", "registered_address", "business_scope",
                "phones", "emails", "websites", "contact_name", "contact_title",
                "contact_phones", "contact_emails",
            ]
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow(
                {
                    "task_id": task_id,
                    "entity_id": str(_ENTITY_ID),
                    "result_status": "SUCCESS",
                    "fetched_at": datetime(2026, 8, 21, 1, 0, tzinfo=timezone.utc).isoformat(),
                    "qcc_company_id": "QCC-FIXTURE-1",
                    "company_name": "北京轨道示例科技有限公司",
                    "unified_social_credit_code": "91110105MA01ABCDE1",
                    "legal_representative": "李示例",
                    "registration_status": "存续",
                    "registered_capital": "1000万元人民币",
                    "establishment_date": "2020-01-02",
                    "registered_address": "北京市朝阳区示例路1号",
                    "business_scope": "技术开发；技术服务",
                    "phones": "010-12345678;13800138000",
                    "emails": "hello@example.cn;brand@example.cn",
                    "websites": "https://www.example.cn",
                    "contact_name": "王联系人",
                    "contact_title": "品牌负责人",
                    "contact_phones": "13900139000",
                    "contact_emails": "wang@example.cn",
                }
            )

        result = ingest_result(plan.batch_id, result_csv)
        assert result["status"] == "COMPLETED", result
        assert result["result_status_counts"]["SUCCESS"] == 1, result

        # Exact re-import of the same completed result is idempotent.
        again = ingest_result(plan.batch_id, result_csv)
        assert again["idempotent"] is True, again

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT last_result_status, successful_fetch_count, refresh_due_at
                FROM acquisition.cn_qcc_company_coverage WHERE entity_id = %s
                """,
                (_ENTITY_ID,),
            )
            coverage = cur.fetchone()
            assert coverage["last_result_status"] == "SUCCESS"
            assert int(coverage["successful_fetch_count"]) == 1
            assert coverage["refresh_due_at"] is not None

            cur.execute(
                """
                SELECT company_name, unified_social_credit_code, legal_representative,
                       phones, emails, websites
                FROM acquisition.cn_qcc_company_observation WHERE entity_id = %s
                """,
                (_ENTITY_ID,),
            )
            observation = cur.fetchone()
            assert observation["company_name"] == "北京轨道示例科技有限公司"
            assert observation["unified_social_credit_code"] == "91110105MA01ABCDE1"
            assert observation["legal_representative"] == "李示例"
            assert "+8613800138000" in observation["phones"]
            assert "hello@example.cn" in observation["emails"]
            assert "example.cn" in observation["websites"]

            cur.execute(
                """
                SELECT count(*) AS n FROM entity.entity_identifier
                WHERE entity_id = %s AND identifier_type = 'CN_UNIFIED_SOCIAL_CREDIT_CODE'
                """,
                (_ENTITY_ID,),
            )
            assert int(cur.fetchone()["n"]) == 1
            cur.execute(
                "SELECT count(*) AS n FROM contact.channel WHERE entity_id = %s",
                (_ENTITY_ID,),
            )
            assert int(cur.fetchone()["n"]) == 5
            cur.execute(
                """
                SELECT relation_type, count(*) AS n
                FROM contact.entity_person_relation
                WHERE entity_id = %s
                GROUP BY relation_type
                """,
                (_ENTITY_ID,),
            )
            relations = {row["relation_type"]: int(row["n"]) for row in cur.fetchall()}
            assert relations == {"CONTACT_PERSON": 1, "LEGAL_REPRESENTATIVE": 1}, relations

    print(
        json.dumps(
            {
                "status": "PASS",
                "source": "QICHACHA",
                "one_company_task": True,
                "company_observation": True,
                "entity_contacts_enriched": True,
                "idempotent_result": True,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
