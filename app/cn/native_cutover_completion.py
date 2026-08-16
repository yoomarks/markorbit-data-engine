from __future__ import annotations

import json
from typing import Any

from app.cn.publish_dag import CN_FINAL_PUBLISH_DAG


CN_NATIVE_CUTOVER_COMPLETION_VERSION = "CN_NATIVE_CUTOVER_COMPLETION_V1"

_INTENTIONAL_COMPATIBILITY = {
    "PARTY_HISTORY_SUPERSEDED": {
        "operation_kind": "PUBLISH_HISTORY_COMPAT",
        "target": "cn_case_party_relation_history",
        "audit_policy": "LEGACY_COMPATIBILITY_SINK_MAY_BE_SUPPRESSED",
        "production_policy": "SUPPRESSED_AND_SHAPE_AUDITED_BY_STORAGE_V2_PARTY_HISTORY",
    },
    "PARTY_HISTORY_OBSERVED": {
        "operation_kind": "PUBLISH_HISTORY_COMPAT",
        "target": "cn_case_party_relation_history",
        "audit_policy": "LEGACY_COMPATIBILITY_SINK_MAY_BE_SUPPRESSED",
        "production_policy": "SUPPRESSED_AND_SHAPE_AUDITED_BY_STORAGE_V2_PARTY_HISTORY",
    },
    "DERIVED_CASE_EVENT": {
        "operation_kind": "EMIT_EVENT",
        "target": "cn_observed_event",
        "audit_policy": "EVENT_DELTA_ADAPTER_V2",
        "production_policy": "SUPPRESSED_RECONSTRUCTIBLE_BASELINE_BY_STORAGE_V2_EVENT_ADAPTER",
    },
}


def cn_native_cutover_completion_contract() -> dict[str, Any]:
    nodes = {node.task_id: node for node in CN_FINAL_PUBLISH_DAG.nodes}
    native_ids = tuple(node.task_id for node in CN_FINAL_PUBLISH_DAG.nodes if node.native_execution)
    compatibility_ids = tuple(
        node.task_id for node in CN_FINAL_PUBLISH_DAG.nodes if not node.native_execution
    )
    expected_compatibility = tuple(_INTENTIONAL_COMPATIBILITY)

    reasons: list[dict[str, Any]] = []
    unexpected_compatibility = sorted(set(compatibility_ids) - set(expected_compatibility))
    missing_compatibility = sorted(set(expected_compatibility) - set(compatibility_ids))
    if unexpected_compatibility:
        reasons.append(
            {
                "code": "UNEXPECTED_EXECUTABLE_COMPATIBILITY_NODE",
                "nodes": unexpected_compatibility,
            }
        )
    if missing_compatibility:
        reasons.append(
            {
                "code": "INTENTIONAL_SUPPRESSION_BOUNDARY_CHANGED",
                "nodes": missing_compatibility,
            }
        )

    suppression_boundaries: list[dict[str, Any]] = []
    for task_id, expected in _INTENTIONAL_COMPATIBILITY.items():
        node = nodes.get(task_id)
        if node is None:
            reasons.append({"code": "SUPPRESSION_NODE_MISSING", "node": task_id})
            continue
        mismatches: dict[str, Any] = {}
        for field in ("operation_kind", "target", "audit_policy"):
            actual = getattr(node, field)
            if actual != expected[field]:
                mismatches[field] = {"expected": expected[field], "actual": actual}
        if node.native_execution:
            mismatches["native_execution"] = {"expected": False, "actual": True}
        if mismatches:
            reasons.append(
                {
                    "code": "SUPPRESSION_BOUNDARY_CONTRACT_DRIFT",
                    "node": task_id,
                    "mismatches": mismatches,
                }
            )
        suppression_boundaries.append(
            {
                "task_id": task_id,
                "operation_kind": node.operation_kind,
                "target": node.target,
                "audit_policy": node.audit_policy,
                "native_execution": node.native_execution,
                "production_policy": expected["production_policy"],
            }
        )

    status = "COMPLETE" if not reasons else "INCOMPLETE"
    return {
        "version": CN_NATIVE_CUTOVER_COMPLETION_VERSION,
        "status": status,
        "dag_id": CN_FINAL_PUBLISH_DAG.dag_id,
        "dag_version": CN_FINAL_PUBLISH_DAG.version,
        "total_node_count": len(CN_FINAL_PUBLISH_DAG.nodes),
        "native_business_node_count": len(native_ids),
        "native_business_nodes": list(native_ids),
        "intentional_compatibility_node_count": len(compatibility_ids),
        "intentional_compatibility_nodes": list(compatibility_ids),
        "suppression_boundaries": suppression_boundaries,
        "no_executable_legacy_business_nodes_remaining": not unexpected_compatibility,
        "storage_v2_suppression_boundaries_frozen": not missing_compatibility
        and not any(reason["code"] == "SUPPRESSION_BOUNDARY_CONTRACT_DRIFT" for reason in reasons),
        "reasons": reasons,
    }


def assert_cn_native_cutover_complete() -> dict[str, Any]:
    contract = cn_native_cutover_completion_contract()
    if contract["status"] != "COMPLETE":
        raise RuntimeError(
            "CN native cutover completion contract failed: "
            + json.dumps(contract["reasons"], ensure_ascii=False, sort_keys=True)
        )
    return contract


def main() -> int:
    contract = cn_native_cutover_completion_contract()
    print(json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if contract["status"] == "COMPLETE" else 4


if __name__ == "__main__":
    raise SystemExit(main())
