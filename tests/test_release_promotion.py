from __future__ import annotations

import copy

from app.cn.serving_state_checkpoint import (
    CHECKPOINT_VERSION as CN_SERVING_STATE_CHECKPOINT_VERSION,
    CRITICAL_TABLES,
)
from app.release_promotion import (
    EXPECTED_OPERATOR_COMMENT_SHA256,
    EXPECTED_OPERATOR_DECISION,
    EXPECTED_OPERATOR_EVIDENCE_TYPE,
    EXPECTED_QUERY_SCOPE,
    EXPECTED_SOURCE_PACKAGE,
    OPERATOR_EVIDENCE_SCHEMA_VERSION,
    PROMOTION_CONTRACT_VERSION,
    build_m17_release_promotion_contract,
    evaluate_m17_release_promotion,
    load_operator_runtime_evidence,
    validate_operator_runtime_evidence,
    validate_serving_state_evidence,
)


OPERATOR_DECISION_TEXT = (
    "Operator decision: do not re-run the expensive `2023_5.zip` package validation. "
    "The package has already completed and was previously validated on the target host; "
    "repeating validation of this very large package would add cost without new evidence. "
    "Treat `2023_5.zip` completion as accepted operator evidence. Any remaining work should "
    "be separated into lightweight, resource-safe full-corpus checkpoint / runtime-gate "
    "engineering and must not require reprocessing or rescanning the package itself."
)


def _operator_evidence() -> dict:
    return {
        "schema_version": OPERATOR_EVIDENCE_SCHEMA_VERSION,
        "evidence_id": "fixture",
        "jurisdiction": "CN",
        "source_package_file_name": EXPECTED_SOURCE_PACKAGE,
        "evidence_type": EXPECTED_OPERATOR_EVIDENCE_TYPE,
        "operator_decision": EXPECTED_OPERATOR_DECISION,
        "accepted_scope": "fixture",
        "requires_current_serving_state": True,
        "fresh_full_corpus_validation_claimed": False,
        "package_replay_or_rescan_required": False,
        "target_host_runtime_details_reconstructed": False,
        "source_reference": {
            "kind": "GITHUB_ISSUE_COMMENT",
            "repository": "yoomarks/markorbit-data-engine",
            "issue_number": 247,
            "comment_id": 5421693252,
            "comment_url": (
                "https://github.com/yoomarks/markorbit-data-engine/issues/247"
                "#issuecomment-5421693252"
            ),
            "comment_body_sha256": EXPECTED_OPERATOR_COMMENT_SHA256,
        },
        "source_operator_decision_text": OPERATOR_DECISION_TEXT,
        "statement": "fixture",
    }


def _serving_state(*, status: str = "PASS") -> dict:
    return {
        "checkpoint_version": CN_SERVING_STATE_CHECKPOINT_VERSION,
        "status": status,
        "read_only": True,
        "evidence_mode": "LIGHTWEIGHT_SERVING_CHECKPOINT",
        "expected_file_name": EXPECTED_SOURCE_PACKAGE,
        "expected_package_success": True,
        "processing_package_count": 0,
        "quiescent": True,
        "core_tables_ready": True,
        "goods_schema_exact": True,
        "critical_tables": {
            table: {
                "exists": True,
                "active_parts": 1,
                "bytes_on_disk": 1024,
                "rows_from_parts": 10,
            }
            for table in CRITICAL_TABLES
        },
        "disks": [
            {
                "name": "default",
                "path": "/var/lib/clickhouse/",
                "free_ratio": 0.15 if status == "WARN" else 0.30,
                "free_space": 150 if status == "WARN" else 300,
                "total_space": 1000,
                "keep_free_space": 0,
            }
        ],
        "query_scope": EXPECTED_QUERY_SCOPE,
        "full_corpus_scan": False,
        "package_reprocessed": False,
        "full_corpus_semantic_acceptance_claimed": False,
        "reasons": (
            [
                {
                    "code": "CLICKHOUSE_DISK_LOW_FREE",
                    "message": "fixture warning",
                    "severity": "WARN",
                }
            ]
            if status == "WARN"
            else []
        ),
    }


def _codes(reasons):
    return {reason["code"] for reason in reasons}


def test_repository_operator_evidence_is_pinned_and_does_not_overclaim() -> None:
    evidence = load_operator_runtime_evidence()

    assert validate_operator_runtime_evidence(evidence) == []
    assert evidence["evidence_type"] == "PRIOR_RUNTIME_VALIDATION_OPERATOR_ACCEPTED"
    assert evidence["source_reference"]["issue_number"] == 247
    assert evidence["source_reference"]["comment_id"] == 5421693252
    assert evidence["source_reference"]["comment_body_sha256"] == EXPECTED_OPERATOR_COMMENT_SHA256
    assert evidence["source_operator_decision_text"] == OPERATOR_DECISION_TEXT
    assert evidence["fresh_full_corpus_validation_claimed"] is False
    assert evidence["package_replay_or_rescan_required"] is False
    assert evidence["target_host_runtime_details_reconstructed"] is False


def test_operator_evidence_source_reference_and_text_hash_are_immutable() -> None:
    evidence = copy.deepcopy(_operator_evidence())
    evidence["source_reference"]["comment_id"] = 1
    evidence["source_operator_decision_text"] += " tampered"

    codes = _codes(validate_operator_runtime_evidence(evidence))

    assert "OPERATOR_EVIDENCE_SOURCE_REFERENCE_MISMATCH" in codes
    assert "OPERATOR_EVIDENCE_SOURCE_TEXT_HASH_MISMATCH" in codes


def test_operator_evidence_cannot_claim_fresh_validation_or_package_rerun() -> None:
    evidence = _operator_evidence()
    evidence["fresh_full_corpus_validation_claimed"] = True
    evidence["package_replay_or_rescan_required"] = True
    evidence["target_host_runtime_details_reconstructed"] = True

    codes = _codes(validate_operator_runtime_evidence(evidence))

    assert "FRESH_FULL_CORPUS_VALIDATION_OVERCLAIMED" in codes
    assert "PACKAGE_RERUN_POLICY_DRIFT" in codes
    assert "TARGET_RUNTIME_DETAILS_RECONSTRUCTED" in codes


def test_prior_operator_evidence_alone_is_pending_not_promoted() -> None:
    report = evaluate_m17_release_promotion(
        operator_evidence=_operator_evidence(),
        serving_state=None,
    )

    assert report["contract_version"] == PROMOTION_CONTRACT_VERSION
    assert report["status"] == "PENDING_CURRENT_SERVING_STATE"
    assert report["operator_evidence_valid"] is True
    assert report["current_serving_state_present"] is False
    assert report["release_promotion_allowed"] is False
    assert report["fresh_full_corpus_validation_claimed"] is False
    assert report["full_corpus_semantic_acceptance_claimed"] is False
    assert report["package_replay_or_rescan_required"] is False
    assert _codes(report["reasons"]) == {"CURRENT_SERVING_STATE_EVIDENCE_REQUIRED"}


def test_prior_operator_evidence_plus_current_pass_allows_m17() -> None:
    report = evaluate_m17_release_promotion(
        operator_evidence=_operator_evidence(),
        serving_state=_serving_state(),
    )

    assert report["status"] == "READY_FOR_M1_7"
    assert report["operator_evidence_valid"] is True
    assert report["current_serving_state_valid"] is True
    assert report["release_promotion_allowed"] is True
    assert report["operator_evidence_sha256"]
    assert report["current_serving_state_sha256"]
    assert report["reasons"] == []


def test_low_disk_warning_is_preserved_but_can_authorize_m17() -> None:
    report = evaluate_m17_release_promotion(
        operator_evidence=_operator_evidence(),
        serving_state=_serving_state(status="WARN"),
    )

    assert report["status"] == "READY_FOR_M1_7_WITH_WARNINGS"
    assert report["current_serving_state_status"] == "WARN"
    assert report["current_serving_state_valid"] is True
    assert report["release_promotion_allowed"] is True
    assert report["reasons"] == []


def test_processing_schema_or_core_table_drift_blocks_release() -> None:
    serving = _serving_state()
    serving["processing_package_count"] = 1
    serving["quiescent"] = False
    serving["goods_schema_exact"] = False
    serving["core_tables_ready"] = False
    serving["critical_tables"]["cn_case_party_current"]["active_parts"] = 0

    report = evaluate_m17_release_promotion(
        operator_evidence=_operator_evidence(),
        serving_state=serving,
    )
    codes = _codes(report["reasons"])

    assert report["release_promotion_allowed"] is False
    assert "SERVING_STATE_NOT_QUIESCENT" in codes
    assert "SERVING_STATE_GOODS_SCHEMA_NOT_EXACT" in codes
    assert "SERVING_STATE_CORE_TABLES_NOT_READY" in codes
    assert "SERVING_STATE_CRITICAL_TABLE_BLOCKED" in codes


def test_lightweight_evidence_boundaries_cannot_be_overclaimed() -> None:
    serving = _serving_state()
    serving["full_corpus_scan"] = True
    serving["package_reprocessed"] = True
    serving["full_corpus_semantic_acceptance_claimed"] = True

    codes = _codes(validate_serving_state_evidence(serving))

    assert "SERVING_STATE_FULL_CORPUS_SCAN_BOUNDARY_INVALID" in codes
    assert "SERVING_STATE_PACKAGE_REPROCESS_BOUNDARY_INVALID" in codes
    assert "SERVING_STATE_SEMANTIC_ACCEPTANCE_BOUNDARY_INVALID" in codes


def test_wrong_package_query_scope_or_mode_blocks_release() -> None:
    serving = _serving_state()
    serving["expected_file_name"] = "older.zip"
    serving["expected_package_success"] = False
    serving["query_scope"] = "full_corpus"
    serving["evidence_mode"] = "FULL_ACCEPTANCE_RECEIPT"

    codes = _codes(validate_serving_state_evidence(serving))

    assert "SERVING_STATE_PACKAGE_MISMATCH" in codes
    assert "SERVING_STATE_PACKAGE_NOT_SUCCESS" in codes
    assert "SERVING_STATE_QUERY_SCOPE_MISMATCH" in codes
    assert "SERVING_STATE_MODE_MISMATCH" in codes


def test_blocked_serving_checkpoint_cannot_promote() -> None:
    serving = _serving_state(status="BLOCKED")

    report = evaluate_m17_release_promotion(
        operator_evidence=_operator_evidence(),
        serving_state=serving,
    )

    assert report["status"] == "BLOCKED"
    assert report["release_promotion_allowed"] is False
    assert "SERVING_STATE_NOT_PASS" in _codes(report["reasons"])


def test_repository_contract_accepts_committed_target_report() -> None:
    report = build_m17_release_promotion_contract()

    assert report["status"] == "READY_FOR_M1_7"
    assert report["operator_evidence_valid"] is True
    assert report["current_serving_state_present"] is True
    assert report["current_serving_state_valid"] is True
    assert report["release_promotion_allowed"] is True
    assert report["fresh_full_corpus_validation_claimed"] is False
    assert report["package_replay_or_rescan_required"] is False
    assert report["reasons"] == []
