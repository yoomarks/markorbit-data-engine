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
_CURSOR_1 = "e413e6e8-834d-4c0e-8ad7-fac80b047d91"
_CURSOR_2 = "f413e6e8-834d-4c0e-8ad7-fac80b047d91"


def _reset() -> None:
    ensure_qcc_schema()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM contact.channel_observation
                WHERE raw_record_id IN (
                    SELECT raw_record_id FROM contact.raw_record
                    WHERE entity_id = %s
                )
                """,
                (_ENTITY_ID,),
            )
            cur.execute(
                "DELETE FROM contact.raw_record WHERE entity_id = %s",
                (_ENTITY_ID,),
            )
            cur.execute(
                """
                DELETE FROM contact.import_run
                WHERE source_id IN (
                    SELECT source_id FROM contact.source
                    WHERE source_profile = 'QICHACHA_COMPANY_ENRICHMENT'
                )
                """
            )
            cur.execute(
                """
                DELETE FROM acquisition.cn_qcc_company_observation WHERE entity_id = %s
                """,
                (_ENTITY_ID,),
            )
            cur.execute(
                "DELETE FROM acquisition.cn_qcc_task WHERE entity_id = %s",
                (_ENTITY_ID,),
            )
            cur.execute(
                "DELETE FROM acquisition.cn_qcc_company_coverage WHERE entity_id = %s",
                (_ENTITY_ID,),
            )
            cur.execute(
                "DELETE FROM acquisition.cn_qcc_batch WHERE batch_key LIKE 'CN_QCC_FIXTURE_%'"
            )
            cur.execute(
                "DELETE FROM contact.entity_person_relation WHERE entity_id = %s",
                (_ENTITY_ID,),
            )
            cur.execute(
                "DELETE FROM contact.channel WHERE entity_id = %s",
                (_ENTITY_ID,),
            )
            cur.execute(
                "DELETE FROM entity.entity_identifier WHERE entity_id = %s",
                (_ENTITY_ID,),
            )
            cur.execute(
                "DELETE FROM entity.entity WHERE entity_id = %s",
                (_ENTITY_ID,),
            )
            cur.execute(
                """
                UPDATE acquisition.cn_qcc_planner_state
                SET source_rank_watermark = 0,
                    source_entity_watermark = '',
                    backfill_bucket = 0,
                    backfill_entity_watermark = '',
                    last_completed_batch_id = NULL,
                    updated_at = now()
                WHERE state_key = 'CN_QCC_APPLICANT'
                """
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


def _candidate() -> QccCandidate:
    return QccCandidate(
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


def _mark_fixture_batch(batch_id: str) -> None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE acquisition.cn_qcc_batch SET batch_key = %s WHERE batch_id = %s",
                (f"CN_QCC_FIXTURE_{batch_id}", batch_id),
            )
        conn.commit()


def _export_task(batch_id: str, path: Path) -> dict[str, str]:
    exported = export_batch(batch_id, path)
    assert exported["task_count"] == 1, exported
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        task_rows = list(csv.DictReader(handle))
    assert len(task_rows) == 1
    assert task_rows[0]["applicant_name"] == "北京轨道示例科技有限公司"
    assert task_rows[0]["trademark_count"] == "23"
    return task_rows[0]


def _write_result(
    path: Path,
    *,
    task: dict[str, str],
    company_name: str = "北京轨道示例科技有限公司",
) -> None:
    fieldnames = [
        "task_id",
        "entity_id",
        "result_status",
        "fetched_at",
        "qcc_company_id",
        "company_name",
        "unified_social_credit_code",
        "legal_representative",
        "registration_status",
        "registered_capital",
        "establishment_date",
        "registered_address",
        "business_scope",
        "phones",
        "emails",
        "websites",
        "contact_name",
        "contact_title",
        "contact_phones",
        "contact_emails",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(
            {
                "task_id": task["task_id"],
                "entity_id": str(_ENTITY_ID),
                "result_status": "SUCCESS",
                "fetched_at": datetime(2026, 8, 21, 1, 0, tzinfo=timezone.utc).isoformat(),
                "qcc_company_id": "QCC-FIXTURE-1",
                "company_name": company_name,
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


def _planner_state() -> dict[str, object]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT backfill_bucket, backfill_entity_watermark, last_completed_batch_id
                FROM acquisition.cn_qcc_planner_state
                WHERE state_key = 'CN_QCC_APPLICANT'
                """
            )
            row = cur.fetchone()
            assert row is not None
            return row


def main() -> None:
    _reset()

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)

        # First page: the bucket is not exhausted, so completion must retain the
        # bucket and persist its within-bucket cursor.
        plan1 = create_batch_from_candidates(
            [_candidate()],
            capacity=10,
            refresh_days=180,
            source_watermark_to=(987654, str(_ENTITY_ID)),
            backfill_bucket=7,
            backfill_entity_from="",
            backfill_entity_to=_CURSOR_1,
            backfill_bucket_exhausted=False,
        )
        _mark_fixture_batch(plan1.batch_id)
        task1 = _export_task(plan1.batch_id, root / "qcc_tasks_1.csv")
        result1 = root / "qcc_result_1.csv"
        _write_result(result1, task=task1)
        first = ingest_result(plan1.batch_id, result1)
        assert first["status"] == "COMPLETED", first
        assert first["result_status_counts"]["SUCCESS"] == 1, first
        assert ingest_result(plan1.batch_id, result1)["idempotent"] is True

        state = _planner_state()
        assert int(state["backfill_bucket"]) == 7, state
        assert state["backfill_entity_watermark"] == _CURSOR_1, state

        # Second refresh returns byte-for-byte equivalent company content. It is
        # a new source observation event and must be allowed to reuse the same
        # content/snapshot hash. This page exhausts the bucket, so completion
        # advances to the next bucket and clears the cursor.
        plan2 = create_batch_from_candidates(
            [_candidate()],
            capacity=10,
            refresh_days=180,
            source_watermark_from=(987654, str(_ENTITY_ID)),
            source_watermark_to=(987654, str(_ENTITY_ID)),
            backfill_bucket=7,
            backfill_entity_from=_CURSOR_1,
            backfill_entity_to=_CURSOR_2,
            backfill_bucket_exhausted=True,
        )
        _mark_fixture_batch(plan2.batch_id)
        task2 = _export_task(plan2.batch_id, root / "qcc_tasks_2.csv")
        result2 = root / "qcc_result_2.csv"
        _write_result(result2, task=task2)
        second = ingest_result(plan2.batch_id, result2)
        assert second["status"] == "COMPLETED", second

        state = _planner_state()
        assert int(state["backfill_bucket"]) == 8, state
        assert state["backfill_entity_watermark"] == "", state

        with postgres_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT count(*) AS n, count(DISTINCT observation_hash) AS hashes
                    FROM acquisition.cn_qcc_company_observation
                    WHERE entity_id = %s
                    """,
                    (_ENTITY_ID,),
                )
                repeated = cur.fetchone()
                assert int(repeated["n"]) == 2, repeated
                assert int(repeated["hashes"]) == 1, repeated

                cur.execute(
                    "SELECT count(*) AS n FROM contact.channel WHERE entity_id = %s",
                    (_ENTITY_ID,),
                )
                channel_count_before_bad = int(cur.fetchone()["n"])
                cur.execute(
                    """
                    SELECT count(*) AS n FROM entity.entity_identifier
                    WHERE entity_id = %s AND identifier_type = 'CN_UNIFIED_SOCIAL_CREDIT_CODE'
                    """,
                    (_ENTITY_ID,),
                )
                identifier_count_before_bad = int(cur.fetchone()["n"])

        # A collector-selected wrong company must fail closed. Although the
        # importer touches contact/entity helpers before the immutable company
        # observation insert, the database identity trigger raises inside the
        # same transaction, so none of those writes may commit.
        plan3 = create_batch_from_candidates(
            [_candidate()],
            capacity=10,
            refresh_days=180,
            backfill_bucket=8,
            backfill_entity_from="",
            backfill_entity_to="",
            backfill_bucket_exhausted=True,
        )
        _mark_fixture_batch(plan3.batch_id)
        task3 = _export_task(plan3.batch_id, root / "qcc_tasks_3.csv")
        bad_result = root / "qcc_result_bad.csv"
        _write_result(bad_result, task=task3, company_name="北京另一家科技有限公司")
        try:
            ingest_result(plan3.batch_id, bad_result)
        except Exception as exc:  # noqa: BLE001 - fixture asserts DB fail-closed contract.
            assert "does not exactly match" in str(exc), str(exc)
        else:
            raise AssertionError("mismatched QCC SUCCESS unexpectedly committed")

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
            assert int(coverage["successful_fetch_count"]) == 2
            assert coverage["refresh_due_at"] is not None

            cur.execute(
                """
                SELECT company_name, unified_social_credit_code, legal_representative,
                       phones, emails, websites
                FROM acquisition.cn_qcc_company_observation
                WHERE entity_id = %s
                ORDER BY created_at
                LIMIT 1
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
            assert int(cur.fetchone()["n"]) == identifier_count_before_bad == 1
            cur.execute(
                "SELECT count(*) AS n FROM contact.channel WHERE entity_id = %s",
                (_ENTITY_ID,),
            )
            assert int(cur.fetchone()["n"]) == channel_count_before_bad == 5
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

            cur.execute(
                """
                SELECT state FROM acquisition.cn_qcc_task
                WHERE batch_id = %s
                """,
                (plan3.batch_id,),
            )
            assert cur.fetchone()["state"] == "EXPORTED"
            cur.execute(
                "SELECT status FROM acquisition.cn_qcc_batch WHERE batch_id = %s",
                (plan3.batch_id,),
            )
            assert cur.fetchone()["status"] == "EXPORTED"

    print(
        json.dumps(
            {
                "status": "PASS",
                "source": "QICHACHA",
                "repeated_snapshot_observations": True,
                "company_identity_fail_closed": True,
                "backfill_cursor_progress": True,
                "refresh_trigger_semantics": True,
                "entity_contacts_enriched": True,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
