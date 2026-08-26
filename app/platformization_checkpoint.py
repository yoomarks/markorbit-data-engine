from __future__ import annotations

import json
from typing import Any, Callable

from app.cn.native_cutover_completion import cn_native_cutover_completion_contract
from app.component_versions import component_versions
from app.platform_contract import platform_contract
from app.release_promotion import (
    PROMOTION_CONTRACT_VERSION,
    build_m17_release_promotion_contract,
)


PLATFORMIZATION_CHECKPOINT_VERSION = "MARKORBIT_PLATFORMIZATION_CHECKPOINT_V1"
_EXPECTED_PLATFORM_VERSION = "MARKORBIT_PLATFORMIZATION_M1.7"
_EXPECTED_PLATFORM_STATUS = "CODE_READY_PENDING_RUNTIME_ACCEPTANCE"
_EXPECTED_WORK_ENGINE_OWNER_REGISTRY_VERSION = "MARKORBIT_WORK_ENGINE_OWNER_REGISTRY_V1"
_PRE_ACCEPTANCE_ENGINE_RELEASE = "M1.6"
_PROMOTED_ENGINE_RELEASE = "M1.7"
_REQUIRED_RUNTIME_ACCEPTANCE = PROMOTION_CONTRACT_VERSION


def build_platformization_checkpoint(
    *,
    platform_builder: Callable[[], dict[str, Any]] = platform_contract,
    version_builder: Callable[[], dict[str, Any]] = component_versions,
    native_cutover_builder: Callable[[], dict[str, Any]] = cn_native_cutover_completion_contract,
    promotion_builder: Callable[[], dict[str, Any]] = build_m17_release_promotion_contract,
) -> dict[str, Any]:
    """Evaluate M1.7 code readiness without inventing target-runtime success.

    Static CI validates the platform and the committed operator-evidence contract.
    Release promotion remains fail-closed: M1.7 is accepted only when the promotion
    contract also contains current lightweight serving-state evidence. CI must not
    infer a fresh full-corpus acceptance from source code or historical issue state.
    """

    platform = platform_builder()
    versions = version_builder()
    native_cutover = native_cutover_builder()
    promotion = promotion_builder()
    engine_release = str(versions.get("engine_release") or "")
    runtime_boundary = platform.get("runtime_acceptance_boundary") or {}
    owner_registry = platform.get("work_engine_owners") or {}
    owners = owner_registry.get("owners") or []
    owner_scopes = {
        str(owner.get("owner_scope") or "").strip()
        for owner in owners
        if str(owner.get("owner_scope") or "").strip()
    }

    reasons: list[dict[str, Any]] = []
    if platform.get("version") != _EXPECTED_PLATFORM_VERSION:
        reasons.append(
            {
                "code": "PLATFORM_VERSION_MISMATCH",
                "expected": _EXPECTED_PLATFORM_VERSION,
                "actual": platform.get("version"),
            }
        )
    if platform.get("status") != _EXPECTED_PLATFORM_STATUS:
        reasons.append(
            {
                "code": "PLATFORM_STATUS_MISMATCH",
                "expected": _EXPECTED_PLATFORM_STATUS,
                "actual": platform.get("status"),
            }
        )
    if platform.get("foundation_contracts_complete") is not True:
        reasons.append({"code": "FOUNDATION_CONTRACTS_INCOMPLETE"})
    if owner_registry.get("version") != _EXPECTED_WORK_ENGINE_OWNER_REGISTRY_VERSION:
        reasons.append(
            {
                "code": "WORK_ENGINE_OWNER_REGISTRY_VERSION_MISMATCH",
                "expected": _EXPECTED_WORK_ENGINE_OWNER_REGISTRY_VERSION,
                "actual": owner_registry.get("version"),
            }
        )
    if len(owner_scopes) < 2 or int(owner_registry.get("owner_count") or 0) < 2:
        reasons.append(
            {
                "code": "WORK_ENGINE_SECOND_OWNER_NOT_REGISTERED",
                "distinct_owner_scopes": sorted(owner_scopes),
                "owner_count": int(owner_registry.get("owner_count") or 0),
            }
        )
    if owner_registry.get("second_owner_is_non_cn") is not True:
        reasons.append({"code": "WORK_ENGINE_SECOND_OWNER_MUST_BE_NON_CN"})
    if owner_registry.get("second_owner_runtime_fixture_proof") is not True:
        reasons.append({"code": "WORK_ENGINE_SECOND_OWNER_RUNTIME_PROOF_MISSING"})
    if owner_registry.get("target_host_acceptance_claimed") is not False:
        reasons.append({"code": "WORK_ENGINE_OWNER_REGISTRY_OVERCLAIMS_TARGET_HOST_ACCEPTANCE"})
    if owner_registry.get("release_promotion_authorized") is not False:
        reasons.append({"code": "WORK_ENGINE_OWNER_REGISTRY_AUTHORIZES_EARLY_PROMOTION"})

    if runtime_boundary.get("required") is not True:
        reasons.append({"code": "RUNTIME_ACCEPTANCE_BOUNDARY_NOT_REQUIRED"})
    if runtime_boundary.get("evaluated_by_platform_contract") is not False:
        reasons.append({"code": "PLATFORM_CONTRACT_MUST_NOT_EVALUATE_REAL_RUNTIME"})
    if runtime_boundary.get("authoritative_checkpoint") != _REQUIRED_RUNTIME_ACCEPTANCE:
        reasons.append(
            {
                "code": "RUNTIME_ACCEPTANCE_AUTHORITY_DRIFT",
                "expected": _REQUIRED_RUNTIME_ACCEPTANCE,
                "actual": runtime_boundary.get("authoritative_checkpoint"),
            }
        )
    if runtime_boundary.get("real_corpus_success_claimed") is not False:
        reasons.append({"code": "STATIC_PLATFORM_CONTRACT_CLAIMS_REAL_CORPUS_SUCCESS"})
    if runtime_boundary.get("release_promotion_allowed_without_runtime_acceptance") is not False:
        reasons.append({"code": "EARLY_RELEASE_PROMOTION_POLICY_ENABLED"})
    if runtime_boundary.get("package_replay_or_rescan_required") is not False:
        reasons.append({"code": "PLATFORM_REQUIRES_EXPENSIVE_CN_RERUN"})

    if promotion.get("contract_version") != _REQUIRED_RUNTIME_ACCEPTANCE:
        reasons.append(
            {
                "code": "PROMOTION_CONTRACT_VERSION_MISMATCH",
                "expected": _REQUIRED_RUNTIME_ACCEPTANCE,
                "actual": promotion.get("contract_version"),
            }
        )
    if promotion.get("operator_evidence_valid") is not True:
        reasons.append(
            {
                "code": "OPERATOR_RUNTIME_EVIDENCE_INVALID",
                "promotion_reasons": promotion.get("reasons") or [],
            }
        )
    if promotion.get("fresh_full_corpus_validation_claimed") is not False:
        reasons.append({"code": "PROMOTION_CONTRACT_OVERCLAIMS_FRESH_FULL_CORPUS_VALIDATION"})
    if promotion.get("package_replay_or_rescan_required") is not False:
        reasons.append({"code": "PROMOTION_CONTRACT_REQUIRES_PACKAGE_RERUN"})

    if native_cutover.get("status") != "COMPLETE":
        reasons.append(
            {
                "code": "CN_NATIVE_CUTOVER_INCOMPLETE",
                "native_cutover_status": native_cutover.get("status"),
                "native_cutover_reasons": native_cutover.get("reasons") or [],
            }
        )
    if native_cutover.get("native_business_node_count") != 18:
        reasons.append(
            {
                "code": "CN_NATIVE_BUSINESS_NODE_COUNT_DRIFT",
                "expected": 18,
                "actual": native_cutover.get("native_business_node_count"),
            }
        )
    if native_cutover.get("intentional_compatibility_node_count") != 3:
        reasons.append(
            {
                "code": "CN_INTENTIONAL_COMPATIBILITY_NODE_COUNT_DRIFT",
                "expected": 3,
                "actual": native_cutover.get("intentional_compatibility_node_count"),
            }
        )

    release_promotion_allowed = promotion.get("release_promotion_allowed") is True
    if engine_release == _PRE_ACCEPTANCE_ENGINE_RELEASE:
        pass
    elif engine_release == _PROMOTED_ENGINE_RELEASE:
        if not release_promotion_allowed:
            reasons.append(
                {
                    "code": "ENGINE_RELEASE_PROMOTED_WITHOUT_ACCEPTED_RUNTIME_EVIDENCE",
                    "required_release": _PROMOTED_ENGINE_RELEASE,
                    "promotion_status": promotion.get("status"),
                }
            )
    else:
        reasons.append(
            {
                "code": "ENGINE_RELEASE_UNSUPPORTED_FOR_M17_BOUNDARY",
                "expected": [_PRE_ACCEPTANCE_ENGINE_RELEASE, _PROMOTED_ENGINE_RELEASE],
                "actual": engine_release,
            }
        )

    code_ready = not reasons
    runtime_present = promotion.get("current_serving_state_present") is True
    runtime_valid = promotion.get("current_serving_state_valid") is True
    if engine_release == _PROMOTED_ENGINE_RELEASE and code_ready:
        status = "M1_7_RELEASE_PROMOTION_ACCEPTED"
        next_action = "CONTINUE_M1_7_RELEASE_WORK"
    elif code_ready:
        status = "CODE_READY_PENDING_RUNTIME_ACCEPTANCE"
        next_action = "RUN_LIGHTWEIGHT_CN_SERVING_STATE_AND_EVALUATE_PROMOTION"
    else:
        status = "BLOCKED"
        next_action = "RESOLVE_PLATFORMIZATION_CHECKPOINT_REASONS"

    return {
        "version": PLATFORMIZATION_CHECKPOINT_VERSION,
        "status": status,
        "read_only": True,
        "static_only": True,
        "code_ready": code_ready,
        "runtime_acceptance_required": not release_promotion_allowed,
        "runtime_acceptance_evaluated": runtime_present,
        "runtime_acceptance_passed": runtime_valid if runtime_present else None,
        "required_runtime_acceptance": _REQUIRED_RUNTIME_ACCEPTANCE,
        "real_corpus_success_claimed": False,
        "fresh_full_corpus_validation_claimed": False,
        "release_promotion_allowed": release_promotion_allowed,
        "engine_release": engine_release,
        "expected_pre_acceptance_engine_release": _PRE_ACCEPTANCE_ENGINE_RELEASE,
        "promoted_engine_release": _PROMOTED_ENGINE_RELEASE,
        "platform_version": platform.get("version"),
        "platform_status": platform.get("status"),
        "promotion_contract_status": promotion.get("status"),
        "promotion_operator_evidence_valid": promotion.get("operator_evidence_valid"),
        "promotion_operator_evidence_id": promotion.get("operator_evidence_id"),
        "promotion_operator_evidence_sha256": promotion.get("operator_evidence_sha256"),
        "promotion_current_serving_state_present": runtime_present,
        "promotion_current_serving_state_valid": runtime_valid,
        "work_engine_owner_registry_version": owner_registry.get("version"),
        "work_engine_owner_count": len(owner_scopes),
        "work_engine_owner_scopes": sorted(owner_scopes),
        "work_engine_second_owner_scope": owner_registry.get("second_owner_scope"),
        "work_engine_second_owner_runtime_fixture_proof": owner_registry.get(
            "second_owner_runtime_fixture_proof"
        ),
        "cn_native_cutover_status": native_cutover.get("status"),
        "cn_native_business_node_count": native_cutover.get("native_business_node_count"),
        "cn_intentional_compatibility_node_count": native_cutover.get(
            "intentional_compatibility_node_count"
        ),
        "next_action": next_action,
        "reasons": reasons,
    }


def assert_platformization_code_ready() -> dict[str, Any]:
    checkpoint = build_platformization_checkpoint()
    if not checkpoint["code_ready"]:
        raise RuntimeError(
            "M1.7 platformization static checkpoint failed: "
            + json.dumps(checkpoint["reasons"], ensure_ascii=False, sort_keys=True)
        )
    return checkpoint


def main() -> int:
    checkpoint = build_platformization_checkpoint()
    print(json.dumps(checkpoint, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if checkpoint["code_ready"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
