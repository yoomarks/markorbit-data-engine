"""Freeze the next bounded CN Hot representative-read plan without running it.

This operator consumes the accepted two-key canary evidence and emits a plan only.
It never connects to PostgreSQL or ClickHouse and never changes serving state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ISSUE = 843
PARENT = 837
PRIOR_PLAN_SHA = "69052db6e09b11eba457aabb4bee9d89f4506659621103bf069d1a98860dc1ba"
EXACT_CANARY_RECEIPT_SHA = "3b94c0690c5ee88f81e3be0e3972d610225ffde647bb20e68c63d2c821b7c4fc"
ROUTE_SHA = "4953d0193b8f218b658ce7a01c1d565e3662401aed0e2f5fb9754fe378c1155d"
EXACT_CANARY_OPERATOR_SHA = "bc5c36b0bb113d85f2fb7cfeebb50aa761fa96215addc7d8abb4dc80fd7cc74d"
ROUTE = "/api/v1/cn/discovery/preliminary-publications"
SOURCE_TABLE = "markorbit_facts.cn_case_current"

# These are bounded lexical route intervals, not claims about corpus distribution.
# Every interval must return data and pass full source/target response parity before
# the future canary can be accepted.
REPRESENTATIVE_RANGES = (
    {
        "stratum": "literal_quote_sentinel",
        "start_inclusive": '"',
        "end_exclusive": "#",
        "reason": "preserve the accepted literal-quoted application-number boundary",
    },
    {
        "stratum": "direct_numeric_1",
        "start_inclusive": "1",
        "end_exclusive": "2",
        "reason": "direct CN numeric family, including the accepted exact-key witness",
    },
    {
        "stratum": "direct_numeric_2",
        "start_inclusive": "2",
        "end_exclusive": "3",
        "reason": "separate direct CN numeric interval",
    },
    {
        "stratum": "direct_numeric_9",
        "start_inclusive": "9",
        "end_exclusive": ":",
        "reason": "upper numeric lexical interval without crossing into letter prefixes",
    },
    {
        "stratum": "madrid_g_family",
        "start_inclusive": "G",
        "end_exclusive": "H",
        "reason": "CN Madrid-designation application-number family",
    },
)

READ_LIMITS = {
    "max_threads": 1,
    "max_rows_to_read": 131072,
    "max_bytes_to_read": 128 * 1024 * 1024,
    "max_execution_time": 10,
    "read_overflow_mode": "throw",
    "max_result_rows": 51,
    "max_memory_usage": 512 * 1024 * 1024,
}


def require(ok: bool, why: str) -> None:
    if not ok:
        raise RuntimeError(why)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_bound_json(path: Path, expected_sha: str, label: str) -> dict[str, Any]:
    require(path.is_file(), f"{label} is missing")
    require(sha(path) == expected_sha, f"{label} SHA-256 drift")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimeError(f"{label} is not valid JSON") from exc
    require(isinstance(payload, dict), f"{label} must be a JSON object")
    return payload


def _validate_prior(plan: dict[str, Any]) -> None:
    scope = plan.get("read_canary")
    require(
        plan.get("issue") == ISSUE
        and plan.get("parent") == PARENT
        and plan.get("status") == "FROZEN_READ_ONLY_PLANS_NO_APPLY"
        and isinstance(scope, dict)
        and scope.get("route") == ROUTE
        and scope.get("source_table") == "cn_case_current"
        and scope.get("serving_endpoint") == "SOURCE_ONLY"
        and scope.get("serving_route_activation_authorized") is False
        and plan.get("merge_capacity", {}).get("start_merges_or_optimize_authorized") is False
        and plan.get("production_mutation_performed") is False,
        "accepted C2b plan no longer proves source-only/no-Apply state",
    )


def _validate_exact_canary(receipt: dict[str, Any]) -> None:
    probes = receipt.get("probes")
    require(
        receipt.get("version") == "CN_HOT_C2B_EXACT_READ_CANARY_R1"
        and receipt.get("status") == "READ_ONLY_EXACT_KEY_PARITY_ACCEPTED_NOT_SERVING"
        and receipt.get("plan_sha256") == PRIOR_PLAN_SHA
        and receipt.get("route_activation_authorized") is False
        and receipt.get("api_started") is False
        and receipt.get("writer_started") is False
        and receipt.get("merge_started") is False
        and receipt.get("source_cleaned") is False
        and isinstance(probes, list)
        and len(probes) == 2
        and {probe.get("key") for probe in probes} == {"10002014", '"10002014"'}
        and all(
            probe.get("result_count") == 1
            and probe.get("route_output_equal") is True
            and probe.get("serving_epoch_equal") is True
            for probe in probes
        ),
        "exact-key canary evidence is not the accepted no-serving receipt",
    )


def build_plan(
    prior: dict[str, Any],
    receipt: dict[str, Any],
    *,
    route_sha: str,
    exact_canary_operator_sha: str,
) -> dict[str, Any]:
    _validate_prior(prior)
    _validate_exact_canary(receipt)
    require(route_sha == ROUTE_SHA, "preliminary-publication route code SHA drift")
    require(
        exact_canary_operator_sha == EXACT_CANARY_OPERATOR_SHA,
        "accepted exact-read canary operator SHA drift",
    )
    ranges = [
        {
            **item,
            "page_size": 50,
            "fetch_limit": 51,
            "cursor": None,
        }
        for item in REPRESENTATIVE_RANGES
    ]
    require(
        len(ranges) == len({item["stratum"] for item in ranges}) == 5
        and all(item["start_inclusive"] < item["end_exclusive"] for item in ranges),
        "representative route interval contract is invalid",
    )
    return {
        "version": "CN_HOT_C2B_REPRESENTATIVE_READ_PLAN_V1",
        "issue": ISSUE,
        "parent": PARENT,
        "status": "FROZEN_REPRESENTATIVE_READ_PLAN_NO_APPLY",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "accepted_inputs": {
            "prior_plan_sha256": PRIOR_PLAN_SHA,
            "exact_canary_receipt_sha256": EXACT_CANARY_RECEIPT_SHA,
            "route_code_sha256": ROUTE_SHA,
            "exact_canary_operator_sha256": EXACT_CANARY_OPERATOR_SHA,
            "control_epoch": prior["control_epoch"],
        },
        "route": ROUTE,
        "source_table": SOURCE_TABLE,
        "serving_endpoint_before": "SOURCE_ONLY",
        "serving_endpoint_after": "SOURCE_ONLY",
        "ranges": ranges,
        "execution_budget": {
            "backend_order": ["source", "target"],
            "serial_only": True,
            "warmup_iterations_per_range": 1,
            "measured_iterations_per_range": 2,
            "total_query_ceiling": len(ranges) * 2 * 3,
            "cooldown_milliseconds_between_queries": 250,
            "read_limits": dict(READ_LIMITS),
            "max_pages_per_request": 1,
            "max_results_per_request": 50,
        },
        "acceptance": {
            "all_ranges_must_be_nonempty": True,
            "source_target_full_response_equality": True,
            "query_snapshot_cursor_provenance_equality": True,
            "serving_epoch_equal_before_between_after_every_pair": True,
            "actual_rows_and_bytes_must_stay_within_limits": True,
            "latency_and_read_statistics_recorded_per_query": True,
            "hard_query_timeout_seconds": READ_LIMITS["max_execution_time"],
            "performance_slo_claimed": False,
            "route_activation_authorized_on_success": False,
        },
        "rollback_contract": {
            "canary_mutates_serving": False,
            "canary_rollback_action": "NONE_SOURCE_REMAINS_SERVING",
            "on_any_error": "FAIL_CLOSED_KEEP_SOURCE_ONLY",
            "no_cross_backend_partial_response": True,
            "future_route_activation_requires_separate_reviewed_change": True,
            "future_activation_must_be_per_route_not_global_endpoint": True,
            "future_rollback_requires_source_epoch_and_health_revalidation": True,
        },
        "explicit_non_authority": {
            "production_queries": False,
            "route_activation": False,
            "api_or_worker_start": False,
            "writer_cutover": False,
            "start_merges_or_optimize": False,
            "source_reclaim": False,
            "docker_wsl_vhdx_operation": False,
        },
        "next_gate": "MERGE_THEN_FREEZE_EXACT_SHA_AND_REQUEST_SEPARATE_READ_ONLY_AUTHORITY",
        "production_mutation_performed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--prior-plan", required=True, type=Path)
    parser.add_argument("--exact-canary-receipt", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    require(not args.output.exists(), "representative-read plan already exists: no overwrite")
    prior = read_bound_json(args.prior_plan, PRIOR_PLAN_SHA, "accepted prior plan")
    receipt = read_bound_json(
        args.exact_canary_receipt,
        EXACT_CANARY_RECEIPT_SHA,
        "accepted exact-key canary receipt",
    )
    route_path = args.repo / "app/cn/discovery_preliminary_publication.py"
    canary_path = args.repo / "scripts/run_cn_hot_c2b_exact_read_canary.py"
    plan = build_plan(
        prior,
        receipt,
        route_sha=sha(route_path),
        exact_canary_operator_sha=sha(canary_path),
    )
    payload = json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as target:
        target.write(payload)
    plan_sha = sha(args.output)
    print("C2B_REPRESENTATIVE_READ_PLAN_SHA256=" + plan_sha, flush=True)
    print("C2B_REPRESENTATIVE_READ_STATUS=" + plan["status"], flush=True)
    print(
        "C2B_REPRESENTATIVE_READ_FUTURE_AUTHORITY="
        + f"GO #843 CN-HOT-REPRESENTATIVE-READ {plan_sha} BOUNDED-RANGES-NO-SERVING",
        flush=True,
    )


if __name__ == "__main__":
    main()
