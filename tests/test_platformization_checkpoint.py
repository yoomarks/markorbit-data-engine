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
            "real_corpus_success_claimed": False,
            "fresh_full_corpus_validation_claimed": False,
            "package_replay_or_rescan_required": False,
            "release_promotion_allowed_without_runtime_acceptance": False,
        },
    }
    value.update(overrides)
    return value


def _versions(release="M1.6"):
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
        "operator_evidence_id": "fixture",
        "operator_evidence_sha256": "a" * 64,
        "current_serving_state_present": False,
        "current_serving_state_valid": False,
        "fresh_full_corpus_validation_claimed": False,
        "package_replay_or_rescan_required": False,
        "reasons": [{"code": "CURRENT_SERVING_STATE_EVIDENCE_REQUIRED"}],
    }
    value.update(overrides)
    return value


def _checkpoint(**overrides):
    builders = {
        "platform_builder": _platform,
        "version_builder": _versions,
        "native_cutover_builder": _native,
        "promotion_builder": _promotion,
    }
    builders.update(overrides)
    return build_platformization_checkpoint(**builders)


def test_static_checkpoint_is_code_ready_without_claiming_runtime_acceptance() -> None:
    checkpoint = _checkpoint()

    assert checkpoint["status"] == "CODE_READY_PENDING_RUNTIME_ACCEPTANCE"
    assert checkpoint["read_only"] is True
    assert checkpoint["static_only"] is True
    assert checkpoint["code_ready"] is True
    assert checkpoint["runtime_acceptance_required"] is True
    assert checkpoint["runtime_acceptance_evaluated"] is False
    assert checkpoint["runtime_acceptance_passed"] is None
    assert checkpoint["required_runtime_acceptance"] == PROMOTION_CONTRACT_VERSION
    assert checkpoint["real_corpus_success_claimed"] is False
    assert checkpoint["fresh_full_corpus_validation_claimed"] is False
    assert checkpoint["release_promotion_allowed"] is False
    assert checkpoint["engine_release"] == "M1.6"
    assert checkpoint["platform_status"] == "CODE_READY_PENDING_RUNTIME_ACCEPTANCE"
    assert checkpoint["promotion_contract_status"] == "PENDING_CURRENT_SERVING_STATE"
    assert checkpoint["promotion_operator_evidence_valid"] is True
    assert (
        checkpoint["work_engine_owner_registry_version"]
        == "MARKORBIT_WORK_ENGINE_OWNER_REGISTRY_V1"
    )
    assert checkpoint["work_engine_owner_count"] == 2
    assert checkpoint["work_engine_owner_scopes"] == [
        "CN_FINAL_PUBLISH",
        "CONTACT_COUNTRY_INFERENCE",
    ]
    assert checkpoint["work_engine_second_owner_scope"] == "CONTACT_COUNTRY_INFERENCE"
    assert checkpoint["work_engine_second_owner_runtime_fixture_proof"] is True
    assert (
        checkpoint["next_action"]
        == "RUN_LIGHTWEIGHT_CN_SERVING_STATE_AND_EVALUATE_PROMOTION"
    )
    assert checkpoint["reasons"] == []


def test_static_checkpoint_blocks_incomplete_native_cutover() -> None:
    checkpoint = _checkpoint(
        native_cutover_builder=lambda: _native(
            status="INCOMPLETE",
            reasons=[{"code": "DRIFT"}],
        )
    )

    assert checkpoint["status"] == "BLOCKED"
    assert checkpoint["code_ready"] is False
    assert any(
        reason["code"] == "CN_NATIVE_CUTOVER_INCOMPLETE"
        for reason in checkpoint["reasons"]
    )


def test_static_checkpoint_blocks_m17_without_current_promotion_evidence() -> None:
    checkpoint = _checkpoint(version_builder=lambda: _versions("M1.7"))

    assert checkpoint["code_ready"] is False
    assert any(
        reason["code"] == "ENGINE_RELEASE_PROMOTED_WITHOUT_ACCEPTED_RUNTIME_EVIDENCE"
        for reason in checkpoint["reasons"]
    )


def test_static_checkpoint_accepts_m17_only_when_promotion_contract_is_ready() -> None:
    checkpoint = _checkpoint(
        version_builder=lambda: _versions("M1.7"),
        promotion_builder=lambda: _promotion(
            status="READY_FOR_M1_7",
            release_promotion_allowed=True,
            current_serving_state_present=True,
            current_serving_state_valid=True,
            reasons=[],
        ),
    )

    assert checkpoint["code_ready"] is True
    assert checkpoint["status"] == "M1_7_RELEASE_PROMOTION_ACCEPTED"
    assert checkpoint["runtime_acceptance_required"] is False
    assert checkpoint["runtime_acceptance_evaluated"] is True
    assert checkpoint["runtime_acceptance_passed"] is True
    assert checkpoint["release_promotion_allowed"] is True
    assert checkpoint["next_action"] == "CONTINUE_M1_7_RELEASE_WORK"


def test_static_checkpoint_blocks_foundation_or_node_count_drift() -> None:
    checkpoint = _checkpoint(
        platform_builder=lambda: _platform(foundation_contracts_complete=False),
        native_cutover_builder=lambda: _native(
            native_business_node_count=17,
            intentional_compatibility_node_count=4,
        ),
    )

    codes = {reason["code"] for reason in checkpoint["reasons"]}
    assert checkpoint["code_ready"] is False
    assert "FOUNDATION_CONTRACTS_INCOMPLETE" in codes
    assert "CN_NATIVE_BUSINESS_NODE_COUNT_DRIFT" in codes
    assert "CN_INTENTIONAL_COMPATIBILITY_NODE_COUNT_DRIFT" in codes


def test_static_checkpoint_blocks_missing_second_owner_proof() -> None:
    checkpoint = _checkpoint(
        platform_builder=lambda: _platform(
            work_engine_owners=_owners(
                owner_count=1,
                owners=[{"owner_scope": "CN_FINAL_PUBLISH"}],
                second_owner_is_non_cn=False,
                second_owner_runtime_fixture_proof=False,
            )
        )
    )

    codes = {reason["code"] for reason in checkpoint["reasons"]}
    assert checkpoint["code_ready"] is False
    assert "WORK_ENGINE_SECOND_OWNER_NOT_REGISTERED" in codes
    assert "WORK_ENGINE_SECOND_OWNER_MUST_BE_NON_CN" in codes
    assert "WORK_ENGINE_SECOND_OWNER_RUNTIME_PROOF_MISSING" in codes


def test_static_checkpoint_blocks_owner_registry_overclaim() -> None:
    checkpoint = _checkpoint(
        platform_builder=lambda: _platform(
            work_engine_owners=_owners(
                target_host_acceptance_claimed=True,
                release_promotion_authorized=True,
            )
        )
    )

    codes = {reason["code"] for reason in checkpoint["reasons"]}
    assert checkpoint["code_ready"] is False
    assert "WORK_ENGINE_OWNER_REGISTRY_OVERCLAIMS_TARGET_HOST_ACCEPTANCE" in codes
    assert "WORK_ENGINE_OWNER_REGISTRY_AUTHORIZES_EARLY_PROMOTION" in codes


def test_static_checkpoint_blocks_runtime_boundary_overclaim() -> None:
    platform = _platform()
    platform["runtime_acceptance_boundary"] = {
        "required": False,
        "evaluated_by_platform_contract": True,
        "authoritative_checkpoint": "SOME_OTHER_GATE",
        "real_corpus_success_claimed": True,
        "release_promotion_allowed_without_runtime_acceptance": True,
        "package_replay_or_rescan_required": True,
    }
    checkpoint = _checkpoint(platform_builder=lambda: platform)

    codes = {reason["code"] for reason in checkpoint["reasons"]}
    assert checkpoint["code_ready"] is False
    assert "RUNTIME_ACCEPTANCE_BOUNDARY_NOT_REQUIRED" in codes
    assert "PLATFORM_CONTRACT_MUST_NOT_EVALUATE_REAL_RUNTIME" in codes
    assert "RUNTIME_ACCEPTANCE_AUTHORITY_DRIFT" in codes
    assert "STATIC_PLATFORM_CONTRACT_CLAIMS_REAL_CORPUS_SUCCESS" in codes
    assert "EARLY_RELEASE_PROMOTION_POLICY_ENABLED" in codes
    assert "PLATFORM_REQUIRES_EXPENSIVE_CN_RERUN" in codes


def test_static_checkpoint_blocks_invalid_operator_evidence_contract() -> None:
    checkpoint = _checkpoint(
        promotion_builder=lambda: _promotion(
            operator_evidence_valid=False,
            reasons=[{"code": "OPERATOR_EVIDENCE_FIELD_MISMATCH"}],
        )
    )

    codes = {reason["code"] for reason in checkpoint["reasons"]}
    assert checkpoint["code_ready"] is False
    assert "OPERATOR_RUNTIME_EVIDENCE_INVALID" in codes


def test_repository_state_passes_static_checkpoint() -> None:
    checkpoint = assert_platformization_code_ready()
    assert checkpoint["code_ready"] is True
    assert checkpoint["runtime_acceptance_evaluated"] is False
    assert checkpoint["promotion_operator_evidence_valid"] is True
    assert checkpoint["work_engine_owner_count"] >= 2
    assert checkpoint["work_engine_second_owner_runtime_fixture_proof"] is True


def test_assertion_raises_on_blocked_checkpoint(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.platformization_checkpoint.build_platformization_checkpoint",
        lambda: {"code_ready": False, "reasons": [{"code": "BLOCKED"}]},
    )
    with pytest.raises(RuntimeError, match="M1.7 platformization static checkpoint failed"):
        assert_platformization_code_ready()
