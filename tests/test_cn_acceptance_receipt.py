from __future__ import annotations

import inspect

import app.cn.acceptance_receipt as receipt_module
from app.cn.acceptance_receipt import validate_receipt


def _checkpoint(status: str = "PASS") -> dict:
    return {
        "checkpoint_version": "CN_M16_FINAL_CHECKPOINT_V1",
        "status": status,
        "read_only": True,
        "acceptance_executed": True,
        "ready_for_next_domain": True,
    }


def _receipt(status: str = "PASS") -> dict:
    return {
        "post_import_version": "CN_M16_POST_IMPORT_ACCEPTANCE_V1",
        "read_only": True,
        "status": status,
        "expected_file_name": "2023_5.zip",
        "expected_package_success": True,
        "readiness_status": "COMPLETE",
        "final_checkpoint_executed": True,
        "final_checkpoint": _checkpoint(status),
        "next_action": {"mode": "CN_REPLAY_ACCEPTED"},
    }


def _codes(report: dict) -> set[str]:
    return {str(item["code"]) for item in report["reasons"]}


def test_valid_receipt_passes_without_runtime_dependencies() -> None:
    report = validate_receipt(_receipt())
    assert report["status"] == "PASS"
    assert report["accepted"] is True
    assert report["read_only"] is True
    assert report["docker_required"] is False
    assert report["database_connection_required"] is False

    source = inspect.getsource(receipt_module).lower()
    assert "docker compose" not in source
    assert "import subprocess" not in source
    assert "from app.db import" not in source
    assert "postgres_conn(" not in source
    assert "clickhouse_client(" not in source


def test_ready_to_continue_receipt_is_not_final_acceptance() -> None:
    receipt = _receipt()
    receipt.update(
        status="READY_TO_CONTINUE",
        readiness_status="READY",
        final_checkpoint_executed=False,
        final_checkpoint=None,
        next_action={"mode": "NORMAL"},
    )
    report = validate_receipt(receipt)

    assert report["accepted"] is False
    assert report["status"] == "BLOCKED"
    assert {
        "REPLAY_NOT_COMPLETE",
        "FINAL_CHECKPOINT_NOT_EXECUTED",
        "RECEIPT_STATUS_NOT_PASS",
        "ACCEPTANCE_MARKER_MISSING",
        "FINAL_CHECKPOINT_MISSING",
    }.issubset(_codes(report))


def test_wrong_package_fails_closed() -> None:
    receipt = _receipt()
    receipt["expected_file_name"] = "2023_4.zip"
    report = validate_receipt(receipt)
    assert report["accepted"] is False
    assert "RECEIPT_FILE_MISMATCH" in _codes(report)


def test_mutating_or_incomplete_checkpoint_fails_closed() -> None:
    receipt = _receipt()
    receipt["final_checkpoint"]["read_only"] = False
    receipt["final_checkpoint"]["acceptance_executed"] = False
    report = validate_receipt(receipt)
    assert report["accepted"] is False
    assert "CHECKPOINT_NOT_READ_ONLY" in _codes(report)
    assert "CHECKPOINT_ACCEPTANCE_NOT_EXECUTED" in _codes(report)
