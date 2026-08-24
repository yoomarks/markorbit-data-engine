from __future__ import annotations

from pathlib import Path

import pytest

from app.contact_ingest import country_inference_work as work


ROOT = Path(__file__).resolve().parents[1]


def test_contact_country_operation_hash_captures_execution_contract() -> None:
    baseline = work.operation_hash(
        apply=False,
        min_confidence=0.86,
        min_margin=0.15,
        batch_size=500,
        max_entities=None,
    )
    assert len(baseline) == 64
    assert baseline == work.operation_hash(
        apply=False,
        min_confidence=0.86,
        min_margin=0.15,
        batch_size=500,
        max_entities=None,
    )
    assert baseline != work.operation_hash(
        apply=True,
        min_confidence=0.86,
        min_margin=0.15,
        batch_size=500,
        max_entities=None,
    )
    assert baseline != work.operation_hash(
        apply=False,
        min_confidence=0.90,
        min_margin=0.15,
        batch_size=500,
        max_entities=None,
    )
    assert baseline != work.operation_hash(
        apply=False,
        min_confidence=0.86,
        min_margin=0.15,
        batch_size=250,
        max_entities=1000,
    )


def test_resume_batch_requires_exact_durable_entity_range() -> None:
    pending = {
        "item_count": 2,
        "range_lower": "00000000-0000-0000-0000-000000000010",
        "range_upper": "00000000-0000-0000-0000-000000000020",
    }
    work.validate_resume_batch(
        [
            {"entity_id": pending["range_lower"]},
            {"entity_id": pending["range_upper"]},
        ],
        pending,
    )

    with pytest.raises(RuntimeError, match="resume range drifted"):
        work.validate_resume_batch(
            [{"entity_id": pending["range_lower"]}],
            pending,
        )

    with pytest.raises(RuntimeError, match="resume range drifted"):
        work.validate_resume_batch(
            [
                {"entity_id": "00000000-0000-0000-0000-000000000011"},
                {"entity_id": pending["range_upper"]},
            ],
            pending,
        )


def test_contact_country_runtime_is_a_real_second_work_engine_owner() -> None:
    runtime = (
        ROOT / "app" / "contact_ingest" / "country_inference_runtime.py"
    ).read_text(encoding="utf-8")
    owner = (
        ROOT / "app" / "contact_ingest" / "country_inference_work.py"
    ).read_text(encoding="utf-8")
    guard = (
        ROOT / "app" / "contact_ingest" / "country_inference_work_guard.py"
    ).read_text(encoding="utf-8")
    migration = (
        ROOT
        / "database"
        / "postgres"
        / "init"
        / "018_contact_country_inference_work.sql"
    ).read_text(encoding="utf-8")

    assert "DurableWorkUnitStore" in owner
    assert 'WORK_OWNER_SCOPE = "CONTACT_COUNTRY_INFERENCE"' in owner
    assert 'PARTITION_KIND = "ENTITY_RANGE"' in owner
    assert "run_id" in owner
    assert "last_run_id" in owner
    assert "reconcile_committed_units" in owner
    assert "validate_resume_batch" in owner
    assert "unfinished durable country inference run exists" in owner
    assert "assert_complete()" in owner

    assert "--resume-run" in runtime
    assert "run_country_inference_resumable" in runtime
    assert "ensure_country_inference_work_membership_guard" in runtime
    assert "CONTACT_COUNTRY_RUNTIME_MODEL_V4" in runtime
    assert "INFERRED_CONTACT_GEO_OVERLAY_NOT_OFFICIAL_TRADEMARK_FACT" in owner

    assert "country_inference_work_member_fingerprint" in guard
    assert "membership drift" in guard
    assert "NEW.attempts > OLD.attempts" in guard
    assert "member_fingerprint SET NOT NULL" in guard

    assert "contact.country_inference_work_unit" in migration
    assert "PRIMARY KEY (run_id, checkpoint_version, task_key)" in migration
    assert "CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED'))" in migration
    assert "CHECK (partition_kind = 'ENTITY_RANGE')" in migration
    assert "member_fingerprint char(32) NOT NULL" in migration
    assert "trg_contact_country_work_membership" in migration
    assert "ix_contact_country_inference_last_run" in migration
