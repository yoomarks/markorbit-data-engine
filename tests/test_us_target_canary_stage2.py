from __future__ import annotations

from copy import deepcopy

import pytest

from app.us import target_canary_stage2 as stage2
from app.us.target_canary import APPLICATION_CANARY_TABLES, TARGET_STORAGE_POLICY


def _accepted_stage1() -> tuple[dict[str, object], dict[str, object]]:
    report: dict[str, object] = {
        "report_version": "PRODUCTION_US_APPLICATION_CANARY_STAGE1_V1",
        "mode": "READ_ONLY",
        "decision": stage2.STAGE1_READY_DECISION,
        "expected_main": stage2.STAGE1_ACCEPTED_MAIN,
        "canary": {
            "registry_basis": "ACCEPTED_PILOT_EVIDENCE_ONLY",
            "live_registry_read": False,
            "accepted_pilot_evidence_reference": stage2.ACCEPTED_PILOT_EVIDENCE_REF,
            "package_sequence": stage2.EXPECTED_SEQUENCE,
            "package_file_name": stage2.EXPECTED_FILE_NAME,
            "package_path": stage2.EXPECTED_SOURCE_PATH,
            "package_size_bytes": stage2.EXPECTED_SIZE_BYTES,
            "package_sha256": stage2.EXPECTED_SHA256,
            "package_id": str(stage2.EXPECTED_PACKAGE_ID),
            "package_kind": stage2.EXPECTED_PACKAGE_KIND,
            "source_rank": stage2.EXPECTED_SOURCE_RANK,
            "schema_manifest_sha256": stage2.EXPECTED_SCHEMA_MANIFEST_SHA256,
            "storage_policy": TARGET_STORAGE_POLICY,
        },
        "target": {
            "required_application_tables_existing": 0,
            "hot_us_active_parts_before": 0,
            "hot_us_active_parts_after": 0,
            "warm_cn_active_parts_before": 0,
            "warm_cn_active_parts_after": 0,
        },
        "safety": {
            "read_only": True,
            "target_write_performed": False,
            "source_data_write_performed": False,
            "registry_write_performed": False,
            "cn_write_performed": False,
            "package_2_executed": False,
            "stage2_go_consumed": False,
        },
    }
    review: dict[str, object] = {
        "review_version": "US_TARGET_CANARY_STAGE1_REVIEW_V1",
        "decision": stage2.STAGE1_REVIEW_DECISION,
        "continuity": {
            "success_prefix_count": stage2.EXPECTED_SUCCESS_PREFIX_COUNT,
            "remaining_count": stage2.EXPECTED_REMAINING_COUNT,
            "next_sequence": stage2.EXPECTED_SEQUENCE,
            "next_action": "REGISTER_AND_INGEST",
            "accepted_pilot_evidence": {"reference": stage2.ACCEPTED_PILOT_EVIDENCE_REF},
        },
        "package": {
            "sequence": stage2.EXPECTED_SEQUENCE,
            "file_name": stage2.EXPECTED_FILE_NAME,
            "path": stage2.EXPECTED_SOURCE_PATH,
            "size_bytes": stage2.EXPECTED_SIZE_BYTES,
            "sha256": stage2.EXPECTED_SHA256,
            "package_id": str(stage2.EXPECTED_PACKAGE_ID),
            "package_kind": stage2.EXPECTED_PACKAGE_KIND,
            "partition_dimension": stage2.EXPECTED_PARTITION_DIMENSION,
            "partition_value": stage2.EXPECTED_PARTITION_VALUE,
            "source_period_end": stage2.EXPECTED_SOURCE_EFFECTIVE_DATE.isoformat(),
            "source_rank": stage2.EXPECTED_SOURCE_RANK,
        },
        "target": {
            "storage_policy": TARGET_STORAGE_POLICY,
            "required_tables": list(APPLICATION_CANARY_TABLES),
        },
        "schema_manifest": {
            "schema_version": "US_M1.4_TARGET_HOT_US_V1",
            "storage_policy": TARGET_STORAGE_POLICY,
            "tables": list(APPLICATION_CANARY_TABLES),
            "statements": [],
            "sha256": stage2.EXPECTED_SCHEMA_MANIFEST_SHA256,
        },
    }
    return report, review


def test_stage2_contract_is_exact_package2_and_never_package3() -> None:
    assert stage2.STAGE1_ACCEPTED_MAIN == "d92f430913ef0684c386c2d7bcb767aa2d3284f8"
    assert stage2.EXPECTED_SEQUENCE == 2
    assert stage2.EXPECTED_FILE_NAME == "apc18840407-20251231-02.zip"
    assert stage2.EXPECTED_SIZE_BYTES == 5_997_232
    assert stage2.EXPECTED_SHA256 == "96555bf13b6e8c2f2ede3433c88e4c600b7115ef3e4d7d22f28c8263cada60c7"
    assert str(stage2.EXPECTED_PACKAGE_ID) == "aec9c8b5-f680-5881-94fb-71a1f8e44152"
    assert stage2.EXPECTED_SOURCE_RANK == 1_020_251_231_002_002
    assert stage2.EXPECTED_SCHEMA_MANIFEST_SHA256 == "ff801dea29e5f4b146e5e7ca24507abf4d7d498f977af64e1bc2e14267f63795"
    assert stage2.STAGE2_AUTHORITY_TOKEN == "GO #526 Stage 2 bounded US Application canary"


def test_stage1_evidence_must_match_frozen_package_and_schema(monkeypatch) -> None:
    report, review = _accepted_stage1()
    monkeypatch.setattr(stage2, "validate_target_schema_manifest", lambda manifest: None)

    package, manifest = stage2.validate_stage1_evidence(report, review)
    assert package["sha256"] == stage2.EXPECTED_SHA256
    assert manifest["sha256"] == stage2.EXPECTED_SCHEMA_MANIFEST_SHA256

    bad_report = deepcopy(report)
    bad_report["canary"]["package_sha256"] = "0" * 64  # type: ignore[index]
    with pytest.raises(RuntimeError, match="package SHA-256 drifted"):
        stage2.validate_stage1_evidence(bad_report, review)

    bad_review = deepcopy(review)
    bad_review["schema_manifest"]["sha256"] = "0" * 64  # type: ignore[index]
    with pytest.raises(RuntimeError, match="schema manifest SHA-256 drifted"):
        stage2.validate_stage1_evidence(report, bad_review)


def test_stage1_evidence_requires_read_only_empty_target(monkeypatch) -> None:
    report, review = _accepted_stage1()
    monkeypatch.setattr(stage2, "validate_target_schema_manifest", lambda manifest: None)

    dirty = deepcopy(report)
    dirty["target"]["hot_us_active_parts_after"] = 1  # type: ignore[index]
    with pytest.raises(RuntimeError, match="hot_us changed"):
        stage2.validate_stage1_evidence(dirty, review)

    already_run = deepcopy(report)
    already_run["safety"]["stage2_go_consumed"] = True  # type: ignore[index]
    with pytest.raises(RuntimeError, match="GO consumed"):
        stage2.validate_stage1_evidence(already_run, review)


def test_execute_stage2_rejects_any_noncanonical_authority_before_io(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="authority token mismatch"):
        stage2.execute_stage2(
            stage1_report_path=tmp_path / "missing-report.json",
            stage1_review_path=tmp_path / "missing-review.json",
            journal_path=tmp_path / "journal.json",
            receipt_path=tmp_path / "receipt.json",
            authority_token="GO Stage 2",
        )
