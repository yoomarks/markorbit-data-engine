from __future__ import annotations

import json
from typing import Any, Callable

from app.cn.native_cutover_completion import cn_native_cutover_completion_contract
from app.component_versions import component_versions
from app.platform_contract import platform_contract


PLATFORMIZATION_CHECKPOINT_VERSION = "MARKORBIT_PLATFORMIZATION_CHECKPOINT_V1"
_EXPECTED_PLATFORM_VERSION = "MARKORBIT_PLATFORMIZATION_M1.7"
_EXPECTED_PLATFORM_STATUS = "CODE_READY_PENDING_RUNTIME_ACCEPTANCE"
_PRE_ACCEPTANCE_ENGINE_RELEASE = "M1.6"
_REQUIRED_RUNTIME_ACCEPTANCE = "CN_M16_FINAL_CHECKPOINT_V1"


def build_platformization_checkpoint(
    *,
    platform_builder: Callable[[], dict[str, Any]] = platform_contract,
    version_builder: Callable[[], dict[str, Any]] = component_versions,
    native_cutover_builder: Callable[[], dict[str, Any]] = cn_native_cutover_completion_contract,
) -> dict[str, Any]:
    """Evaluate M1.7 code readiness without claiming real-corpus acceptance.

    This checkpoint is deliberately static. It may run in CI and source checkouts,
    so it must never infer that a local CN replay or the real 2023_4 package has
    passed. Real data promotion remains owned by the existing read-only CN final
    checkpoint executed against the operator's actual PostgreSQL/ClickHouse state.
    """

    platform = platform_builder()
    versions = version_builder()
    native_cutover = native_cutover_builder()
    engine_release = str(versions.get("engine_release") or "")
    runtime_boundary = platform.get("runtime_acceptance_boundary") or {}

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
    if engine_release != _PRE_ACCEPTANCE_ENGINE_RELEASE:
        reasons.append(
            {
                "code": "ENGINE_RELEASE_PROMOTED_BEFORE_RUNTIME_ACCEPTANCE_BOUNDARY",
                "expected_pre_acceptance_release": _PRE_ACCEPTANCE_ENGINE_RELEASE,
                "actual": engine_release,
            }
        )

    code_ready = not reasons
    return {
        "version": PLATFORMIZATION_CHECKPOINT_VERSION,
        "status": "CODE_READY_PENDING_RUNTIME_ACCEPTANCE" if code_ready else "BLOCKED",
        "read_only": True,
        "static_only": True,
        "code_ready": code_ready,
        "runtime_acceptance_required": True,
        "runtime_acceptance_evaluated": False,
        "runtime_acceptance_passed": None,
        "required_runtime_acceptance": _REQUIRED_RUNTIME_ACCEPTANCE,
        "real_corpus_success_claimed": False,
        "release_promotion_allowed": False,
        "engine_release": engine_release,
        "expected_pre_acceptance_engine_release": _PRE_ACCEPTANCE_ENGINE_RELEASE,
        "platform_version": platform.get("version"),
        "platform_status": platform.get("status"),
        "cn_native_cutover_status": native_cutover.get("status"),
        "cn_native_business_node_count": native_cutover.get("native_business_node_count"),
        "cn_intentional_compatibility_node_count": native_cutover.get(
            "intentional_compatibility_node_count"
        ),
        "next_action": "RUN_REAL_CN_RUNTIME_ACCEPTANCE_SEPARATELY",
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
