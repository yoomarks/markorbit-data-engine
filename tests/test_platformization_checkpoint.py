from __future__ import annotations

import pytest

from app.platformization_checkpoint import (
    assert_platformization_code_ready,
    build_platformization_checkpoint,
)
from app.release_promotion import PROMOTION_CONTRACT_VERSION


def _owners(**overrides):
    value = {
        "version": "MARKORBIT_WORK_ENGINE_OWNER_REGISTRY_V1",
        "owner_count": 2,
        "owners": [
            {"owner_scope": "CN_FINAL_PUBLISH"},
            {"owner_scope": "CONTACT_COUNTRY_INFERENCE"},
        ],
        "second_owner_scope": "CONTACT_COUNTRY_INFERENCE",
        "second_owner_is_non_cn": True,
        "second_owner_runtime_fixture_proof": True,
        "target_host_acceptance_claimed": False,
        "release_promotion_authorized": False,
    }
    value.update(overrides)
    return value


def _platform(**overrides):
    value = {
        "version": "MARKORBIT_PLATFORMIZATION_M1.7",
        "status": "CODE_READY_PENDING_RUNTIME_ACCEPTANCE",
        "foundation_contracts_complete": True,
        "work_engine_owners": _owners(),
        "runtime_acceptance_boundary": {
            "required": True,
            "evaluated_by_platform_contract": False,
            "authoritative_checkpoint": PROMOTION_CONTRACT_VERSION,
            "evidence_mode": "PRIOR_RUNTIME_OPERATOR_ACCEPTED_PLUS_CURRENT_SERVING_STATE",
            "real_corpus_success_claimed": False,
            "fresh_full_corpus_validation_claimed": False,
            "package_replay_or_rescan_required": False,
            "release_promotion_allowed_without_runtime_acceptance": False,
        },
    }
    value.update(overrides)
    return value


def _versions(release: str = "M1.6"):
    return {"engine_release": release, "components": {}}


def _native(**overrides):
    value = {
        "status": "COMPLETE",
        "native_business_node_count": 18,
        "intentional_compatibility_node_count": 3,
        "reasons": [],
    }
    value.update(overrides)
    return value


def _promotion(**overrides):
    value = {
        "contract_version": PROMOTION_CONTRACT_VERSION,
        "status": "PENDING_CURRENT_SERVING_STATE",
        "release_promotion_allowed": False,
        "operator_evidence_valid": True,
        "operator_evidence_id": "fixture-operator-evidence",
        "operator_evidence_sha256": "operator-sha",
        "operator_source_comment_body_sha256": "comment-sha",
        "current_serving_state_present": False,
        "current_serving_state_valid": False,
        "current_serving_state_sha256": None,
        "fresh_full_corpus_validation_claimed": False,
        "full_corpus_semantic_acceptance_claimed": False,
        "package_replay_or_rescan_required": False,
        "reasons": [{"code": "CURRENT_SERVING_STATE_EVIDENCE_REQUIRED"}],
    }
    value.update(overrides)
    return value


def _build(*, release: str = "M1.6", platform=None, promotion=None, native=None):
    return build_platformization_checkpoint(
        platform_builder=(lambda: platform or _platform()),
        version_builder=(lambda: _versions(release)),
        native_cutover_builder=(lambda: native or _native()),
        promotion_builder=(lambda: promotion or _promotion()),
    )


def test_m16_stays_code_ready_while_current_serving_state_is_pending() -> None:
    checkpoint = _build()

    assert checkpoint["status"] == "CODE_READY_PENDING_RUNTIME_ACCEPTANCE"
    assert checkpoint["code_ready"] is True
    assert checkpoint["runtime_acceptance_required"] is True
    assert checkpoint["runtime_acceptance_evaluated"] is False
    assert checkpoint["runtime_acceptance_passed"] is None
    assert checkpoint["required_runtime_acceptance"] == PROMOTION_CONTRACT_VERSION
    assert checkpoint["release_promotion_allowed"] is False
    assert checkpoint["engine_release"] == "M1.6"
    assert checkpoint["fresh_full_corpus_validation_claimed"] is False
    assert (
        checkpoint["next_action"]
        == "RUN_LIGHTWEIGHT_CN_SERVING_STATE_AND_EVALUATE_PROMOTION"
    )
    assert checkpoint["reasons"] == []


def test_m17_version_is_rejected_without_accepted_current_runtime_evidence() -> None:
    checkpoint = _build(release="M1.7")

    assert checkpoint["status"] == "BLOCKED"
    assert checkpoint["code_ready"] is False
    assert "ENGINE_RELEASE_PROMOTED_WITHOUT_ACCEPTED_RUNTIME_EVIDENCE" in {
        reason["code"] for reason in checkpoint["reasons"]
    }


def test_m17_version_is_accepted_only_after_promotion_contract_passes() -> None:
    promotion = _promotion(
        status="READY_FOR_M1_7",
        release_promotion_allowed=True,
        current_serving_state_present=True,
        current_serving_state_valid=True,
        current_serving_state_sha256="serving-sha",
        reasons=[],
    )
    checkpoint = _build(release="M1.7", promotion=promotion)

    assert checkpoint["status"] == "M1_7_RELEASE_PROMOTION_ACCEPTED"
    assert checkpoint["code_ready"] is True
    assert checkpoint["runtime_acceptance_required"] is False
    assert checkpoint["runtime_acceptance_evaluated"] is True
    assert checkpoint["runtime_acceptance_passed"] is True
    assert checkpoint["release_promotion_allowed"] is True
    assert checkpoint["promotion_current_serving_state_sha256"] == "serving-sha"
    assert checkpoint["next_action"] == "CONTINUE_M1_7_RELEASE_WORK"


def test_warning_promotion_can_still_authorize_m17_when_contract_allows_it() -> None:
    promotion = _promotion(
        status="READY_FOR_M1_7_WITH_WARNINGS",
        release_promotion_allowed=True,
        current_serving_state_present=True,
        current_serving_state_valid=True,
        current_serving_state_sha256="serving-warn-sha",
        reasons=[],
    )
    checkpoint = _build(release="M1.7", promotion=promotion)

    assert checkpoint["code_ready"] is True
    assert checkpoint["release_promotion_allowed"] is True


def test_runtime_boundary_cannot_restore_expensive_rerun_or_overclaim() -> None:
    platform = _platform()
    platform["runtime_acceptance_boundary"] = {
        "required": False,
        "evaluated_by_platform_contract": True,
        "authoritative_checkpoint": "CN_M16_FINAL_CHECKPOINT_V1",
        "evidence_mode": "FULL_CORPUS",
        "real_corpus_success_claimed": True,
        "fresh_full_corpus_validation_claimed": True,
        "package_replay_or_rescan_required": True,
        "release_promotion_allowed_without_runtime_acceptance": True,
    }
    checkpoint = _build(platform=platform)

    codes = {reason["code"] for reason in checkpoint["reasons"]}
    assert checkpoint["code_ready"] is False
    assert "RUNTIME_ACCEPTANCE_BOUNDARY_NOT_REQUIRED" in codes
    assert "RUNTIME_ACCEPTANCE_AUTHORITY_DRIFT" in codes
    assert "RUNTIME_ACCEPTANCE_EVIDENCE_MODE_DRIFT" in codes
    assert "STATIC_PLATFORM_CONTRACT_CLAIMS_REAL_CORPUS_SUCCESS" in codes
    assert "PLATFORM_OVERCLAIMS_FRESH_FULL_CORPUS_VALIDATION" in codes
    assert "PLATFORM_REQUIRES_EXPENSIVE_CN_RERUN" in codes
    assert "EARLY_RELEASE_PROMOTION_POLICY_ENABLED" in codes


def test_invalid_operator_evidence_blocks_even_pre_promotion_m16() -> None:
    promotion = _promotion(
        operator_evidence_valid=False,
        reasons=[{"code": "OPERATOR_EVIDENCE_SOURCE_REFERENCE_MISMATCH"}],
    )
    checkpoint = _build(promotion=promotion)

    assert checkpoint["code_ready"] is False
    assert "OPERATOR_RUNTIME_EVIDENCE_INVALID" in {
        reason["code"] for reason in checkpoint["reasons"]
    }


def test_promotion_contract_cannot_claim_fresh_full_corpus_semantics_or_rerun() -> None:
    promotion = _promotion(
        fresh_full_corpus_validation_claimed=True,
        full_corpus_semantic_acceptance_claimed=True,
        package_replay_or_rescan_required=True,
    )
    checkpoint = _build(promotion=promotion)

    codes = {reason["code"] for reason in checkpoint["reasons"]}
    assert checkpoint["code_ready"] is False
    assert "PROMOTION_CONTRACT_OVERCLAIMS_FRESH_FULL_CORPUS_VALIDATION" in codes
    assert "PROMOTION_CONTRACT_OVERCLAIMS_FULL_CORPUS_SEMANTICS" in codes
    assert "PROMOTION_CONTRACT_REQUIRES_PACKAGE_RERUN" in codes


def test_incomplete_native_cutover_or_owner_registry_still_blocks() -> None:
    platform = _platform(
        work_engine_owners=_owners(
            owner_count=1,
            owners=[{"owner_scope": "CN_FINAL_PUBLISH"}],
            second_owner_is_non_cn=False,
            second_owner_runtime_fixture_proof=False,
        )
    )
    checkpoint = _build(
        platform=platform,
        native=_native(status="INCOMPLETE", reasons=[{"code": "DRIFT"}]),
    )

    codes = {reason["code"] for reason in checkpoint["reasons"]}
    assert "WORK_ENGINE_SECOND_OWNER_NOT_REGISTERED" in codes
    assert "WORK_ENGINE_SECOND_OWNER_MUST_BE_NON_CN" in codes
    assert "WORK_ENGINE_SECOND_OWNER_RUNTIME_PROOF_MISSING" in codes
    assert "CN_NATIVE_CUTOVER_INCOMPLETE" in codes


def test_unsupported_engine_release_blocks() -> None:
    checkpoint = _build(release="M1.8")

    assert checkpoint["code_ready"] is False
    assert "ENGINE_RELEASE_UNSUPPORTED_FOR_M17_BOUNDARY" in {
        reason["code"] for reason in checkpoint["reasons"]
    }


def test_repository_state_passes_static_checkpoint_without_target_report() -> None:
    checkpoint = assert_platformization_code_ready()

    assert checkpoint["code_ready"] is True
    assert checkpoint["engine_release"] == "M1.6"
    assert checkpoint["promotion_operator_evidence_valid"] is True
    assert checkpoint["promotion_current_serving_state_present"] is False
    assert checkpoint["release_promotion_allowed"] is False


def test_assertion_raises_on_blocked_checkpoint(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.platformization_checkpoint.build_platformization_checkpoint",
        lambda: {"code_ready": False, "reasons": [{"code": "BLOCKED"}]},
    )
    with pytest.raises(RuntimeError, match="M1.7 platformization static checkpoint failed"):
        assert_platformization_code_ready()
