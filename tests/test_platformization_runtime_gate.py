from __future__ import annotations

import inspect
import json

import app.platformization_runtime_gate as runtime_gate
from app.platformization_runtime_gate import (
    build_platformization_runtime_gate,
    evaluate_platformization_runtime_gate,
)


def _static(*, ready: bool = True):
    return {
        "version": "MARKORBIT_PLATFORMIZATION_CHECKPOINT_V1",
        "status": "CODE_READY_PENDING_RUNTIME_ACCEPTANCE" if ready else "BLOCKED",
        "code_ready": ready,
        "runtime_acceptance_evaluated": False,
        "release_promotion_allowed": False,
        "reasons": [] if ready else [{"code": "STATIC_BLOCK"}],
    }


def _final_checkpoint(
    status: str = "PASS", *, ready: bool = True, acceptance_executed: bool = True
):
    return {
        "checkpoint_version": "CN_M16_FINAL_CHECKPOINT_V1",
        "read_only": True,
        "status": status,
        "ready_for_next_domain": ready,
        "acceptance_executed": acceptance_executed,
        "reasons": [] if ready else [{"code": "CN_BLOCK"}],
    }


def _receipt(
    status: str = "PASS",
    *,
    next_mode: str = "CN_REPLAY_ACCEPTED",
    file_name: str = "2023_5.zip",
    checkpoint: dict | None = None,
):
    return {
        "post_import_version": "CN_M16_POST_IMPORT_ACCEPTANCE_V1",
        "read_only": True,
        "expected_file_name": file_name,
        "status": status,
        "expected_package_success": True,
        "readiness_status": "COMPLETE",
        "final_checkpoint_executed": True,
        "final_checkpoint": checkpoint or _final_checkpoint(status),
        "next_action": {"mode": next_mode, "command": None},
        "warnings": [],
        "reasons": [],
    }


def _reason_codes(report):
    return {reason["code"] for reason in report["reasons"]}


def test_pass_combines_static_readiness_and_persisted_cn_acceptance() -> None:
    report = evaluate_platformization_runtime_gate(
        static_checkpoint=_static(),
        cn_acceptance_receipt=_receipt(),
    )

    assert report["version"] == "MARKORBIT_PLATFORMIZATION_RUNTIME_GATE_V2"
    assert report["status"] == "PASS"
    assert report["read_only"] is True
    assert report["receipt_driven"] is True
    assert report["static_code_ready"] is True
    assert report["runtime_acceptance_evaluated"] is True
    assert report["runtime_acceptance_passed"] is True
    assert report["release_promotion_eligible"] is True
    assert report["release_promoted"] is False
    assert report["real_cn_runtime_accepted"] is True
    assert (
        report["real_cn_runtime_acceptance_source"]
        == "CN_M16_POST_IMPORT_ACCEPTANCE_V1"
    )
    assert report["cn_acceptance_next_mode"] == "CN_REPLAY_ACCEPTED"
    assert report["reasons"] == []


def test_pass_with_warnings_receipt_is_still_promotion_eligible() -> None:
    report = evaluate_platformization_runtime_gate(
        static_checkpoint=_static(),
        cn_acceptance_receipt=_receipt("PASS_WITH_WARNINGS"),
    )
    assert report["status"] == "PASS_WITH_WARNINGS"
    assert report["release_promotion_eligible"] is True


def test_blocked_static_checkpoint_short_circuits_receipt_loader() -> None:
    calls = {"receipt": 0}

    def receipt_loader(path):
        calls["receipt"] += 1
        raise AssertionError("receipt loader must not run when static code is blocked")

    report = build_platformization_runtime_gate(
        cn_acceptance_receipt_path="does-not-matter.json",
        static_builder=lambda: _static(ready=False),
        receipt_loader=receipt_loader,
    )

    assert calls["receipt"] == 0
    assert report["status"] == "BLOCKED"
    assert report["runtime_acceptance_evaluated"] is False
    assert report["release_promotion_eligible"] is False


def test_missing_receipt_fails_closed_without_runtime_query() -> None:
    report = build_platformization_runtime_gate(static_builder=_static)

    assert report["status"] == "BLOCKED"
    assert report["runtime_acceptance_evaluated"] is False
    assert report["release_promotion_eligible"] is False
    assert "CN_ACCEPTANCE_RECEIPT_MISSING" in _reason_codes(report)


def test_nonexistent_receipt_fails_closed() -> None:
    report = build_platformization_runtime_gate(
        cn_acceptance_receipt_path="missing.json",
        static_builder=_static,
        receipt_loader=lambda path: (_ for _ in ()).throw(FileNotFoundError(path)),
    )

    assert report["status"] == "BLOCKED"
    assert "CN_ACCEPTANCE_RECEIPT_NOT_FOUND" in _reason_codes(report)


def test_malformed_receipt_fails_closed(tmp_path) -> None:
    receipt_path = tmp_path / "bad.json"
    receipt_path.write_text("{not-json", encoding="utf-8")

    report = build_platformization_runtime_gate(
        cn_acceptance_receipt_path=receipt_path,
        static_builder=_static,
    )

    assert report["status"] == "BLOCKED"
    assert report["runtime_acceptance_evaluated"] is False
    assert "CN_ACCEPTANCE_RECEIPT_INVALID" in _reason_codes(report)


def test_pass_without_cn_replay_accepted_marker_fails_closed() -> None:
    report = evaluate_platformization_runtime_gate(
        static_checkpoint=_static(),
        cn_acceptance_receipt=_receipt(next_mode="STOP_AND_REVIEW"),
    )

    assert report["status"] == "BLOCKED"
    assert report["release_promotion_eligible"] is False
    assert "CN_ACCEPTANCE_MARKER_MISSING" in _reason_codes(report)


def test_nonpassing_receipt_cannot_promote() -> None:
    receipt = _receipt("BLOCKED", checkpoint=_final_checkpoint("BLOCKED", ready=False))
    receipt["reasons"] = [{"code": "TARGET_BLOCK"}]
    report = evaluate_platformization_runtime_gate(
        static_checkpoint=_static(),
        cn_acceptance_receipt=receipt,
    )

    assert report["status"] == "BLOCKED"
    assert report["runtime_acceptance_passed"] is False
    assert report["release_promotion_eligible"] is False
    assert "CN_ACCEPTANCE_RECEIPT_STATUS_NOT_PASS" in _reason_codes(report)


def test_wrong_package_or_mutating_checkpoint_fails_closed() -> None:
    checkpoint = _final_checkpoint()
    checkpoint["read_only"] = False
    report = evaluate_platformization_runtime_gate(
        static_checkpoint=_static(),
        cn_acceptance_receipt=_receipt(file_name="older.zip", checkpoint=checkpoint),
    )

    codes = _reason_codes(report)
    assert report["release_promotion_eligible"] is False
    assert "CN_ACCEPTANCE_RECEIPT_FILE_MISMATCH" in codes
    assert "CN_RUNTIME_CHECKPOINT_NOT_READ_ONLY" in codes


def test_builder_reads_real_receipt_file_without_cn_checkpoint_builder(tmp_path) -> None:
    receipt_path = tmp_path / "accepted.json"
    receipt_path.write_text(json.dumps(_receipt()), encoding="utf-8")

    report = build_platformization_runtime_gate(
        cn_acceptance_receipt_path=receipt_path,
        static_builder=_static,
    )

    assert report["release_promotion_eligible"] is True
    source = inspect.getsource(runtime_gate)
    assert "from app.cn.final_checkpoint import" not in source
    assert "build_final_checkpoint(" not in source
