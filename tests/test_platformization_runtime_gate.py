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


def _serving_checkpoint(
    status: str = "PASS",
    *,
    file_name: str = "2023_5.zip",
    **overrides,
):
    report = {
        "checkpoint_version": "CN_M16_LIGHTWEIGHT_SERVING_CHECKPOINT_V1",
        "status": status,
        "read_only": True,
        "evidence_mode": "LIGHTWEIGHT_SERVING_CHECKPOINT",
        "expected_file_name": file_name,
        "expected_package_success": True,
        "processing_package_count": 0,
        "quiescent": True,
        "core_tables_ready": True,
        "goods_schema_exact": True,
        "query_scope": "control_and_system_metadata_only",
        "full_corpus_scan": False,
        "package_reprocessed": False,
        "full_corpus_semantic_acceptance_claimed": False,
        "reasons": [],
    }
    report.update(overrides)
    return report


def _reason_codes(report):
    return {reason["code"] for reason in report["reasons"]}


def test_legacy_full_acceptance_receipt_remains_promotion_eligible() -> None:
    report = evaluate_platformization_runtime_gate(
        static_checkpoint=_static(),
        cn_acceptance_receipt=_receipt(),
    )

    assert report["version"] == "MARKORBIT_PLATFORMIZATION_RUNTIME_GATE_V3"
    assert report["status"] == "PASS"
    assert report["read_only"] is True
    assert report["receipt_driven"] is True
    assert report["runtime_evidence_mode"] == "FULL_ACCEPTANCE_RECEIPT"
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
    assert report["promotion_basis"] == "PERSISTED_FULL_ACCEPTANCE_RECEIPT"
    assert report["cn_acceptance_next_mode"] == "CN_REPLAY_ACCEPTED"
    assert report["reasons"] == []


def test_pass_with_warnings_receipt_is_still_promotion_eligible() -> None:
    report = evaluate_platformization_runtime_gate(
        static_checkpoint=_static(),
        cn_acceptance_receipt=_receipt("PASS_WITH_WARNINGS"),
    )
    assert report["status"] == "PASS_WITH_WARNINGS"
    assert report["release_promotion_eligible"] is True


def test_lightweight_serving_checkpoint_is_distinct_persisted_evidence_mode() -> None:
    report = evaluate_platformization_runtime_gate(
        static_checkpoint=_static(),
        cn_serving_checkpoint=_serving_checkpoint(),
    )

    assert report["status"] == "PASS"
    assert report["receipt_driven"] is False
    assert report["runtime_evidence_mode"] == "LIGHTWEIGHT_SERVING_CHECKPOINT"
    assert report["runtime_acceptance_evaluated"] is True
    assert report["runtime_acceptance_passed"] is True
    assert report["release_promotion_eligible"] is True
    assert report["real_cn_runtime_accepted"] is True
    assert (
        report["real_cn_runtime_acceptance_source"]
        == "CN_M16_LIGHTWEIGHT_SERVING_CHECKPOINT_V1"
    )
    assert (
        report["promotion_basis"]
        == "PERSISTED_LIGHTWEIGHT_SERVING_STATE_AFTER_PRIOR_VALIDATION"
    )
    assert report["cn_acceptance_receipt_loaded"] is False
    assert report["cn_serving_checkpoint_loaded"] is True
    assert report["reasons"] == []


def test_lightweight_disk_warning_maps_to_pass_with_warnings() -> None:
    report = evaluate_platformization_runtime_gate(
        static_checkpoint=_static(),
        cn_serving_checkpoint=_serving_checkpoint(
            "WARN",
            reasons=[
                {
                    "code": "CLICKHOUSE_DISK_LOW_FREE",
                    "severity": "WARN",
                }
            ],
        ),
    )
    assert report["status"] == "PASS_WITH_WARNINGS"
    assert report["release_promotion_eligible"] is True


def test_lightweight_evidence_boundary_is_fail_closed() -> None:
    for field, value, expected_code in (
        (
            "full_corpus_scan",
            True,
            "CN_SERVING_FULL_CORPUS_SCAN_BOUNDARY_INVALID",
        ),
        (
            "package_reprocessed",
            True,
            "CN_SERVING_PACKAGE_REPROCESS_BOUNDARY_INVALID",
        ),
        (
            "full_corpus_semantic_acceptance_claimed",
            True,
            "CN_SERVING_SEMANTIC_ACCEPTANCE_BOUNDARY_INVALID",
        ),
    ):
        report = evaluate_platformization_runtime_gate(
            static_checkpoint=_static(),
            cn_serving_checkpoint=_serving_checkpoint(**{field: value}),
        )
        assert report["status"] == "BLOCKED"
        assert report["release_promotion_eligible"] is False
        assert expected_code in _reason_codes(report)


def test_lightweight_requires_quiescent_ready_target_state() -> None:
    report = evaluate_platformization_runtime_gate(
        static_checkpoint=_static(),
        cn_serving_checkpoint=_serving_checkpoint(
            processing_package_count=1,
            quiescent=False,
            core_tables_ready=False,
        ),
    )
    codes = _reason_codes(report)
    assert report["status"] == "BLOCKED"
    assert "CN_SERVING_STATE_NOT_QUIESCENT" in codes
    assert "CN_SERVING_CORE_TABLES_NOT_READY" in codes


def test_lightweight_wrong_package_or_version_fails_closed() -> None:
    checkpoint = _serving_checkpoint(file_name="older.zip")
    checkpoint["checkpoint_version"] = "OLD"
    report = evaluate_platformization_runtime_gate(
        static_checkpoint=_static(),
        cn_serving_checkpoint=checkpoint,
    )
    codes = _reason_codes(report)
    assert "CN_SERVING_CHECKPOINT_FILE_MISMATCH" in codes
    assert "CN_SERVING_CHECKPOINT_VERSION_MISMATCH" in codes
    assert report["release_promotion_eligible"] is False


def test_blocked_static_checkpoint_short_circuits_all_evidence_loaders() -> None:
    calls = {"receipt": 0, "serving": 0}

    def receipt_loader(path):
        calls["receipt"] += 1
        raise AssertionError("receipt loader must not run when static code is blocked")

    def serving_loader(path):
        calls["serving"] += 1
        raise AssertionError("serving loader must not run when static code is blocked")

    report = build_platformization_runtime_gate(
        cn_acceptance_receipt_path="does-not-matter.json",
        cn_serving_checkpoint_path="also-does-not-matter.json",
        static_builder=lambda: _static(ready=False),
        receipt_loader=receipt_loader,
        serving_checkpoint_loader=serving_loader,
    )

    assert calls == {"receipt": 0, "serving": 0}
    assert report["status"] == "BLOCKED"
    assert report["runtime_acceptance_evaluated"] is False
    assert report["release_promotion_eligible"] is False


def test_missing_evidence_fails_closed_without_runtime_query() -> None:
    report = build_platformization_runtime_gate(static_builder=_static)
    assert report["status"] == "BLOCKED"
    assert report["runtime_acceptance_evaluated"] is False
    assert report["release_promotion_eligible"] is False
    assert "CN_ACCEPTANCE_RECEIPT_MISSING" in _reason_codes(report)


def test_nonexistent_receipt_and_serving_checkpoint_fail_closed() -> None:
    receipt_report = build_platformization_runtime_gate(
        cn_acceptance_receipt_path="missing.json",
        static_builder=_static,
        receipt_loader=lambda path: (_ for _ in ()).throw(FileNotFoundError(path)),
    )
    assert "CN_ACCEPTANCE_RECEIPT_NOT_FOUND" in _reason_codes(receipt_report)

    serving_report = build_platformization_runtime_gate(
        cn_serving_checkpoint_path="missing.json",
        static_builder=_static,
        serving_checkpoint_loader=lambda path: (_ for _ in ()).throw(
            FileNotFoundError(path)
        ),
    )
    assert "CN_SERVING_CHECKPOINT_NOT_FOUND" in _reason_codes(serving_report)


def test_malformed_evidence_fails_closed(tmp_path) -> None:
    bad_path = tmp_path / "bad.json"
    bad_path.write_text("{not-json", encoding="utf-8")

    receipt_report = build_platformization_runtime_gate(
        cn_acceptance_receipt_path=bad_path,
        static_builder=_static,
    )
    assert "CN_ACCEPTANCE_RECEIPT_INVALID" in _reason_codes(receipt_report)

    serving_report = build_platformization_runtime_gate(
        cn_serving_checkpoint_path=bad_path,
        static_builder=_static,
    )
    assert "CN_SERVING_CHECKPOINT_INVALID" in _reason_codes(serving_report)


def test_both_evidence_paths_are_rejected_before_load() -> None:
    calls = {"receipt": 0, "serving": 0}

    def receipt_loader(path):
        calls["receipt"] += 1
        return _receipt()

    def serving_loader(path):
        calls["serving"] += 1
        return _serving_checkpoint()

    report = build_platformization_runtime_gate(
        cn_acceptance_receipt_path="one.json",
        cn_serving_checkpoint_path="two.json",
        static_builder=_static,
        receipt_loader=receipt_loader,
        serving_checkpoint_loader=serving_loader,
    )
    assert calls == {"receipt": 0, "serving": 0}
    assert report["status"] == "BLOCKED"
    assert "CN_RUNTIME_EVIDENCE_AMBIGUOUS" in _reason_codes(report)


def test_pass_without_cn_replay_accepted_marker_fails_closed() -> None:
    report = evaluate_platformization_runtime_gate(
        static_checkpoint=_static(),
        cn_acceptance_receipt=_receipt(next_mode="STOP_AND_REVIEW"),
    )
    assert report["status"] == "BLOCKED"
    assert "CN_ACCEPTANCE_MARKER_MISSING" in _reason_codes(report)


def test_nonpassing_receipt_cannot_promote() -> None:
    receipt = _receipt("BLOCKED", checkpoint=_final_checkpoint("BLOCKED", ready=False))
    receipt["reasons"] = [{"code": "TARGET_BLOCK"}]
    report = evaluate_platformization_runtime_gate(
        static_checkpoint=_static(),
        cn_acceptance_receipt=receipt,
    )
    assert report["runtime_acceptance_passed"] is False
    assert "CN_ACCEPTANCE_RECEIPT_STATUS_NOT_PASS" in _reason_codes(report)


def test_wrong_package_or_mutating_full_checkpoint_fails_closed() -> None:
    checkpoint = _final_checkpoint()
    checkpoint["read_only"] = False
    report = evaluate_platformization_runtime_gate(
        static_checkpoint=_static(),
        cn_acceptance_receipt=_receipt(file_name="older.zip", checkpoint=checkpoint),
    )
    codes = _reason_codes(report)
    assert "CN_ACCEPTANCE_RECEIPT_FILE_MISMATCH" in codes
    assert "CN_RUNTIME_CHECKPOINT_NOT_READ_ONLY" in codes


def test_builder_reads_persisted_evidence_without_cn_runtime_builder(tmp_path) -> None:
    receipt_path = tmp_path / "accepted.json"
    receipt_path.write_text(json.dumps(_receipt()), encoding="utf-8")
    receipt_report = build_platformization_runtime_gate(
        cn_acceptance_receipt_path=receipt_path,
        static_builder=_static,
    )
    assert receipt_report["release_promotion_eligible"] is True

    serving_path = tmp_path / "serving.json"
    serving_path.write_text(json.dumps(_serving_checkpoint()), encoding="utf-8")
    serving_report = build_platformization_runtime_gate(
        cn_serving_checkpoint_path=serving_path,
        static_builder=_static,
    )
    assert serving_report["release_promotion_eligible"] is True
    assert serving_report["runtime_evidence_mode"] == "LIGHTWEIGHT_SERVING_CHECKPOINT"

    source = inspect.getsource(runtime_gate)
    assert "from app.cn.final_checkpoint import" not in source
    assert "build_final_checkpoint(" not in source
    assert "build_serving_state_checkpoint(" not in source
