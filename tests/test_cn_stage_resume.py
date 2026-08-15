from __future__ import annotations

from pathlib import Path
import uuid

from app.cn import stage_resume


def _counts(basic: int = 10, goods: int = 20) -> dict[str, int]:
    result = {table: 0 for table in stage_resume.STAGE_TABLES}
    result["markorbit_facts.cn_stage_basic"] = basic
    result["markorbit_facts.cn_stage_goods"] = goods
    return result


def test_stage_checkpoint_requires_exact_stage_counts(monkeypatch):
    package_uuid = uuid.uuid4()
    checkpoint = {"snapshot": {"stage_counts": _counts()}}
    monkeypatch.setattr(stage_resume, "_stage_counts", lambda package, client: _counts())
    assert stage_resume.stage_checkpoint_is_usable(
        package_uuid,
        checkpoint,
        client=object(),
    )

    monkeypatch.setattr(
        stage_resume,
        "_stage_counts",
        lambda package, client: _counts(goods=19),
    )
    assert not stage_resume.stage_checkpoint_is_usable(
        package_uuid,
        checkpoint,
        client=object(),
    )


def test_capture_stage_snapshot_reconstructs_role_counts(monkeypatch):
    package_uuid = uuid.uuid4()
    monkeypatch.setattr(
        stage_resume,
        "_package_file_profiles",
        lambda package_id: [
            {"role": "basic", "logical_rows": 7},
            {"role": "goods", "logical_rows": 11},
            {"role": "goods", "logical_rows": 13},
        ],
    )
    monkeypatch.setattr(stage_resume, "_stage_counts", lambda package, client: _counts())
    snapshot = stage_resume.capture_stage_snapshot(package_uuid, client=object())
    assert snapshot["role_counts"] == {"basic": 7, "goods": 24}
    assert snapshot["stage_counts"]["markorbit_facts.cn_stage_basic"] == 10
    assert snapshot["checkpoint_version"] == "CN_M16_STAGE_V1"


def test_rehydrated_file_quality_preserves_durable_counters():
    issues = stage_resume._rehydrated_file_quality_issues(
        uuid.uuid4(),
        uuid.uuid4(),
        [
            {
                "internal_name": "goods.csv",
                "header_raw": ["申请号", "未知列"],
                "header_canonical": ["application_number", "unknown:未知列"],
                "failed_rows": 3,
                "replacement_chars": 5,
            }
        ],
    )
    by_type = {issue["issue_type"]: issue for issue in issues}
    assert by_type["UNREPAIRABLE_CSV_ROW"]["occurrence_count"] == 3
    assert by_type["INVALID_TEXT_BYTES_REPLACED"]["occurrence_count"] == 5
    assert by_type["UNKNOWN_SOURCE_HEADER"]["source_file"] == "goods.csv"


def test_stage_resume_schema_and_failure_policy_are_durable():
    sql = Path("database/postgres/init/008_cn_stage_checkpoint.sql").read_text(
        encoding="utf-8"
    )
    source = Path("app/cn/stage_resume.py").read_text(encoding="utf-8")
    ingest = Path("app/cn/ingest_m16.py").read_text(encoding="utf-8")
    assert "control.cn_package_stage_checkpoint" in sql
    assert "source_sha256" in sql
    assert "ON DELETE CASCADE" in sql
    assert "legacy_module._cleanup_partial_outputs(package_uuid)" in source
    assert "clear_stage_checkpoint(str(package_uuid))" in source
    assert "cleanup_stage(package_uuid)" in source
    assert "checkpoint_aware_stage_cleanup" in ingest
    assert 'str(package.get("status")) == "SUCCESS"' in ingest
