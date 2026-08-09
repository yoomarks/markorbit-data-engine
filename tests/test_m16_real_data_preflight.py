import hashlib
from datetime import date
from pathlib import Path

from app.cn.preflight_m16_real_data import (
    evaluate_preflight,
    inventory_raw_packages,
    verify_registered_sources,
)


def _snapshot(**overrides):
    snapshot = {
        "engine_version": "M1.6",
        "postgres_ok": True,
        "clickhouse_ok": True,
        "missing_m16_columns": [],
        "ingestion_lock_available": True,
        "processing_packages": 0,
        "running_ingest_jobs": 0,
        "registered_source_verification": {
            "checked": 0,
            "resolved": 0,
            "missing": [],
            "sha256_mismatch": [],
        },
        "current_scope_count": 0,
        "current_goods_item_count": 0,
        "current_goods_lifecycle_count": 0,
        "incoming_zip_count": 3,
        "archive_zip_count": 3,
        "registered_package_count": 0,
        "successful_package_count": 0,
        "failed_or_interrupted_packages": 0,
        "unknown_raw_package_count": 0,
        "duplicate_raw_filename_count": 3,
        "latest_monthly_coverage_date": None,
    }
    snapshot.update(overrides)
    return snapshot


def test_clean_reset_is_replay_ready_but_not_inference_ready() -> None:
    result = evaluate_preflight(_snapshot())
    assert result["status"] == "PASS_WITH_WARNINGS"
    assert result["mode"] == "CLEAN_RESET_READY_FOR_REPLAY"
    assert result["safe_to_run_replay_command"] is True
    assert result["safe_to_run_inference_audit"] is False
    assert "clean_registry_waiting_for_replay" in result["warning_reasons"]
    assert "raw_package_present_in_multiple_locations" in result["warning_reasons"]


def test_stable_m16_snapshot_is_inference_ready() -> None:
    result = evaluate_preflight(
        _snapshot(
            incoming_zip_count=0,
            archive_zip_count=3,
            duplicate_raw_filename_count=0,
            registered_package_count=3,
            successful_package_count=3,
            current_scope_count=100,
            current_goods_item_count=500,
            current_goods_lifecycle_count=100,
            latest_monthly_coverage_date=date(2023, 1, 31),
            registered_source_verification={
                "checked": 3,
                "resolved": 3,
                "missing": [],
                "sha256_mismatch": [],
            },
        )
    )
    assert result["status"] == "PASS"
    assert result["mode"] == "M16_DATA_PRESENT_STABLE_SNAPSHOT"
    assert result["safe_to_run_inference_audit"] is True


def test_old_scope_without_durable_items_is_hard_failure() -> None:
    result = evaluate_preflight(
        _snapshot(
            registered_package_count=2,
            successful_package_count=2,
            current_scope_count=20,
            current_goods_item_count=0,
        )
    )
    assert result["status"] == "FAIL"
    assert "m15_scope_without_m16_durable_items" in result["hard_fail_reasons"]
    assert result["safe_to_run_replay_command"] is False


def test_busy_or_running_ingestion_is_hard_failure() -> None:
    result = evaluate_preflight(
        _snapshot(
            ingestion_lock_available=False,
            processing_packages=1,
            running_ingest_jobs=1,
        )
    )
    assert result["status"] == "FAIL"
    assert "cn_ingestion_lock_busy" in result["hard_fail_reasons"]
    assert "processing_packages_present" in result["hard_fail_reasons"]
    assert "running_cn_ingest_jobs_present" in result["hard_fail_reasons"]


def test_missing_or_mismatched_registered_sources_are_hard_failures() -> None:
    result = evaluate_preflight(
        _snapshot(
            registered_package_count=2,
            successful_package_count=2,
            registered_source_verification={
                "checked": 2,
                "resolved": 0,
                "missing": [{"file_name": "1999.zip"}],
                "sha256_mismatch": [{"file_name": "2000.zip"}],
            },
        )
    )
    assert result["status"] == "FAIL"
    assert "registered_source_file_missing" in result["hard_fail_reasons"]
    assert "registered_source_sha256_mismatch" in result["hard_fail_reasons"]


def test_inventory_classifies_base_monthly_and_unknown(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming" / "cn"
    archive = tmp_path / "archive" / "cn"
    incoming.mkdir(parents=True)
    archive.mkdir(parents=True)
    (incoming / "1999.zip").write_bytes(b"base")
    (incoming / "2023_1.zip").write_bytes(b"monthly")
    (archive / "mystery.zip").write_bytes(b"unknown")

    rows = inventory_raw_packages(tmp_path)
    by_name = {row.file_name: row for row in rows}
    assert by_name["1999.zip"].package_kind == "BASE_PARTITION"
    assert by_name["2023_1.zip"].package_kind == "MONTHLY_PATCH"
    assert by_name["2023_1.zip"].source_period_end == date(2023, 1, 31)
    assert by_name["mystery.zip"].package_kind == "UNKNOWN"


def test_registered_source_verification_uses_sha_not_filename(tmp_path: Path) -> None:
    archive = tmp_path / "archive" / "cn"
    archive.mkdir(parents=True)
    good = archive / "1999.zip"
    good.write_bytes(b"authoritative")
    expected = hashlib.sha256(b"authoritative").hexdigest()

    result = verify_registered_sources(
        [
            {
                "package_id": "pkg-1",
                "file_name": "1999.zip",
                "sha256": expected,
                "archived_path": "",
            }
        ],
        raw_root=tmp_path,
    )
    assert result["checked"] == 1
    assert result["resolved"] == 1
    assert result["missing"] == []
    assert result["sha256_mismatch"] == []


def test_registered_source_verification_rejects_same_name_wrong_sha(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming" / "cn"
    incoming.mkdir(parents=True)
    (incoming / "1999.zip").write_bytes(b"wrong")

    result = verify_registered_sources(
        [
            {
                "package_id": "pkg-1",
                "file_name": "1999.zip",
                "sha256": hashlib.sha256(b"expected").hexdigest(),
                "archived_path": "",
            }
        ],
        raw_root=tmp_path,
    )
    assert result["resolved"] == 0
    assert result["missing"] == []
    assert len(result["sha256_mismatch"]) == 1
