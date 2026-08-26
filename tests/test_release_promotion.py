from __future__ import annotations

import copy

from app.cn.serving_state_checkpoint import (
    CHECKPOINT_VERSION as CN_SERVING_STATE_CHECKPOINT_VERSION,
    CRITICAL_TABLES,
)
from app.release_promotion import (
    EXPECTED_OPERATOR_DECISION,
    EXPECTED_OPERATOR_EVIDENCE_TYPE,
    EXPECTED_QUERY_SCOPE,
    EXPECTED_SOURCE_PACKAGE,
    OPERATOR_EVIDENCE_SCHEMA_VERSION,
    build_m17_release_promotion_contract,
    evaluate_m17_release_promotion,
    load_operator_runtime_evidence,
    validate_operator_runtime_evidence,
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
            "repository": "yoomarks/markorbit-data-engine",
            "issue_number": 247,
            "comment_id": 5421693252,
            "comment_url": "fixture",
        },
        "statement": "fixture",
    }


def _serving_state() -> dict:
    return {
        "checkpoint_version": CN_SERVING_STATE_CHECKPOINT_VERSION,
        "status": "PASS",
        "read_only": True,
        "query_scope": EXPECTED_QUERY_SCOPE,
        "expected_file_name": EXPECTED_SOURCE_PACKAGE,
        "expected_package": {
            "file_name": EXPECTED_SOURCE_PACKAGE,
            "status": "SUCCESS",
        },
        "processing_package_count": 0,
        "goods_schema_exact": True,
        "critical_tables": {
            table: {"exists": True, "active_parts": 1}
            for table in CRITICAL_TABLES
        },
        "disks": [
            {
                "name": "default",
                "free_ratio": 0.25,
                "free_space": 250,
                "total_space": 1000,
            }
        ],
        "reasons": [],
    }


def test_repository_operator_evidence_is_valid_and_does_not_overclaim() -> None:
    evidence = load_operator_runtime_evidence()

    assert validate_operator_runtime_evidence(evidence) == []
    assert evidence["evidence_type"] == "PRIOR_RUNTIME_VALIDATION_OPERATOR_ACCEPTED"
    assert evidence["source_reference"]["issue_number"] == 247
    assert evidence["source_reference"]["comment_id"] == 5421693252
    assert evidence["fresh_full_corpus_validation_claimed"] is False
    assert evidence["package_replay_or_rescan_required"] is False
    assert evidence["target_host_runtime_details_reconstructed"] is False


def test_prior_operator_evidence_alone_is_pending_not_promoted() -> None:
    report = evaluate_m17_release_promotion(
        operator_evidence=_operator_evidence(),
        serving_state=None,
    )

    assert report["status"] == "PENDING_CURRENT_SERVING_STATE"
    assert report["operator_evidence_valid"] is True
    assert report["current_serving_state_present"] is False
    assert report["release_promotion_allowed"] is False
    assert report["fresh_full_corpus_validation_claimed"] is False
    assert report["package_replay_or_rescan_required"] is False
    assert [reason["code"] for reason in report["reasons"]] == [
        "CURRENT_SERVING_STATE_EVIDENCE_REQUIRED"
    ]


def test_prior_operator_evidence_plus_current_pass_allows_m17() -> None:
    report = evaluate_m17_release_promotion(
        operator_evidence=_operator_evidence(),
        serving_state=_serving_state(),
    )

    assert report["status"] == "READY_FOR_M1_7"
    assert report["operator_evidence_valid"] is True
    assert report["current_serving_state_valid"] is True
    assert report["release_promotion_allowed"] is True
    assert report["reasons"] == []


def test_warning_serving_state_does_not_authorize_release() -> None:
    serving_state = _serving_state()
    serving_state["status"] = "WARN"
    serving_state["reasons"] = [
        {
            "code": "CLICKHOUSE_DISK_LOW_FREE",
            "message": "fixture",
            "severity": "WARN",
        }
    ]

    report = evaluate_m17_release_promotion(
        operator_evidence=_operator_evidence(),
        serving_state=serving_state,
    )

    assert report["status"] == "BLOCKED"
    assert report["release_promotion_allowed"] is False
    assert "SERVING_STATE_NOT_PASS" in {
        reason["code"] for reason in report["reasons"]
    }


def test_schema_or_processing_drift_blocks_release() -> None:
    serving_state = _serving_state()
    serving_state["goods_schema_exact"] = False
    serving_state["processing_package_count"] = 1

    report = evaluate_m17_release_promotion(
        operator_evidence=_operator_evidence(),
        serving_state=serving_state,
    )

    codes = {reason["code"] for reason in report["reasons"]}
    assert report["release_promotion_allowed"] is False
    assert "SERVING_STATE_GOODS_SCHEMA_NOT_EXACT" in codes
    assert "SERVING_STATE_PACKAGE_PROCESSING" in codes


def test_missing_expected_critical_table_blocks_even_if_report_claims_pass() -> None:
    serving_state = _serving_state()
    del serving_state["critical_tables"]["cn_case_party_current"]

    report = evaluate_m17_release_promotion(
        operator_evidence=_operator_evidence(),
        serving_state=serving_state,
    )

    assert report["release_promotion_allowed"] is False
    assert "SERVING_STATE_CRITICAL_TABLE_BLOCKED" in {
        reason["code"] for reason in report["reasons"]
    }


def test_missing_active_part_blocks_release() -> None:
    serving_state = _serving_state()
    serving_state["critical_tables"]["cn_case_party_current"]["active_parts"] = 0

    report = evaluate_m17_release_promotion(
        operator_evidence=_operator_evidence(),
        serving_state=serving_state,
    )

    assert report["release_promotion_allowed"] is False
    assert "SERVING_STATE_CRITICAL_TABLE_BLOCKED" in {
        reason["code"] for reason in report["reasons"]
    }


def test_disk_headroom_is_independently_checked() -> None:
    serving_state = _serving_state()
    serving_state["disks"][0]["free_ratio"] = 0.19

    report = evaluate_m17_release_promotion(
        operator_evidence=_operator_evidence(),
        serving_state=serving_state,
    )

    assert report["release_promotion_allowed"] is False
    assert "SERVING_STATE_DISK_HEADROOM_BELOW_PROMOTION_THRESHOLD" in {
        reason["code"] for reason in report["reasons"]
    }


def test_query_scope_is_independently_checked() -> None:
    serving_state = _serving_state()
    serving_state["query_scope"] = "full_corpus"

    report = evaluate_m17_release_promotion(
        operator_evidence=_operator_evidence(),
        serving_state=serving_state,
    )

    assert report["release_promotion_allowed"] is False
    assert "SERVING_STATE_QUERY_SCOPE_MISMATCH" in {
        reason["code"] for reason in report["reasons"]
    }


def test_operator_evidence_cannot_claim_fresh_validation_or_package_rerun() -> None:
    evidence = _operator_evidence()
    evidence["fresh_full_corpus_validation_claimed"] = True
    evidence["package_replay_or_rescan_required"] = True

    reasons = validate_operator_runtime_evidence(evidence)
    codes = {reason["code"] for reason in reasons}

    assert "FRESH_FULL_CORPUS_VALIDATION_OVERCLAIMED" in codes
    assert "PACKAGE_RERUN_POLICY_DRIFT" in codes


def test_operator_evidence_source_reference_is_pinned() -> None:
    evidence = copy.deepcopy(_operator_evidence())
    evidence["source_reference"]["comment_id"] = 1

    reasons = validate_operator_runtime_evidence(evidence)

    assert "OPERATOR_EVIDENCE_SOURCE_REFERENCE_MISMATCH" in {
        reason["code"] for reason in reasons
    }


def test_repository_contract_stays_pending_without_current_target_report() -> None:
    report = build_m17_release_promotion_contract()

    assert report["status"] == "PENDING_CURRENT_SERVING_STATE"
    assert report["operator_evidence_valid"] is True
    assert report["release_promotion_allowed"] is False
    assert report["fresh_full_corpus_validation_claimed"] is False
