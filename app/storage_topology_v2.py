from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Mapping


CONTRACT_VERSION = "DATA_ENGINE_STORAGE_TOPOLOGY_V2"
RECOMMENDED_FREE_BPS = 3_000
HARD_FREE_BPS = 2_000
E_PLACEMENTS = ("warm_cn", "hot_global", "warm_us", "warm_global")
VHDX_NAME = re.compile(r"^(?:hot|warm)_[a-z0-9]+(?:_[a-z0-9]+)*$")


def _non_negative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def build_storage_topology() -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "read_only": True,
        "physical_drives": {
            "D": {
                "media": "NVME",
                "role": "PRIMARY_CN_US_HOT",
                "placements": ["hot_cn", "hot_us"],
            },
            "E": {
                "media": "NVME",
                "role": "GLOBAL_HOT_AND_ALL_WARM_GROWTH",
                "placements": ["hot_global", "warm_cn", "warm_us", "warm_global"],
            },
            "F": {
                "media": "HDD",
                "role": "RAW_BACKUP_ORIGINAL_VISUAL_AUTHORITY",
                "placements": ["raw", "backup", "original_visual", "knowledge_artifact"],
                "primary_mergetree_serving": False,
            },
        },
        "physical_reserve_policy": {
            "recommended_free_bps": RECOMMENDED_FREE_BPS,
            "hard_free_bps": HARD_FREE_BPS,
        },
        "vhdx_reserve_policy": {
            "recommended_free_bps": RECOMMENDED_FREE_BPS,
            "hard_free_bps": HARD_FREE_BPS,
            "name_pattern": VHDX_NAME.pattern,
        },
        "e_allocation_policy": {
            "basis": "MEASURED_APPROVED_BYTES_WITHIN_CAPACITY_AFTER_RECOMMENDED_RESERVE",
            "placements": list(E_PLACEMENTS),
            "optional_hot_jurisdiction_source": "hot_global",
            "optional_warm_jurisdiction_source": "warm_global",
            "overcommit_allowed": False,
        },
        "default_placement": {
            "hot": {"CN": "hot_cn", "US": "hot_us", "OTHER": "hot_global"},
            "warm": {"CN": "warm_cn", "US": "warm_us", "OTHER": "warm_global"},
            "raw": {"ANY": "raw"},
            "backup": {"ANY": "backup"},
            "original_visual": {"ANY": "original_visual"},
            "knowledge_artifact": {"ANY": "knowledge_artifact"},
        },
        "dedicated_jurisdiction_review": {
            "regulatory_isolation_is_sufficient": True,
            "capacity_pressure_is_sufficient": True,
            "measured_slo_contention_is_sufficient": True,
            "capacity_fit_and_separate_review_required": True,
            "automatic_promotion": False,
        },
        "monitoring": {
            "warning_state": "BELOW_RECOMMENDED_RESERVE",
            "critical_state": "BLOCKED_BELOW_HARD_RESERVE",
            "dimensions": ["physical_drive", "vhdx"],
            "automatic_remediation": False,
        },
        "constraints": {
            "mergetree_requires_linux_ext4": True,
            "provider_or_source_artifacts_remain_reconstructible": True,
            "live_move_alter_ttl_optimize_authorized": False,
            "vhdx_mutation_authorized": False,
            "source_move_or_delete_authorized": False,
            "production_mutation_authorized": False,
        },
    }


def placement_for(jurisdiction: str, tier: str) -> dict[str, str]:
    normalized_tier = tier.strip().lower()
    normalized_jurisdiction = jurisdiction.strip().upper()
    topology = build_storage_topology()
    placement = topology["default_placement"].get(normalized_tier)
    if placement is None:
        raise ValueError(f"unsupported storage tier: {tier}")
    name = placement.get(normalized_jurisdiction, placement.get("OTHER", placement.get("ANY")))
    if name is None:
        raise ValueError(f"no placement for tier {tier} and jurisdiction {jurisdiction}")
    drive = next(
        drive
        for drive, policy in topology["physical_drives"].items()
        if name in policy["placements"]
    )
    return {"drive": drive, "placement": name}


def e_allocation_budgets(
    total_bytes: int, requested_bytes: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    total = _non_negative_int(total_bytes, "total_bytes")
    allocatable = total * (10_000 - RECOMMENDED_FREE_BPS) // 10_000
    requested = requested_bytes or {name: 0 for name in E_PLACEMENTS}
    if set(requested) != set(E_PLACEMENTS):
        raise ValueError("E allocations must contain exactly the governed placements")
    allocations = {
        name: _non_negative_int(requested[name], f"e_allocations.{name}")
        for name in E_PLACEMENTS
    }
    allocated = sum(allocations.values())
    if allocated > allocatable:
        raise ValueError("E allocations exceed capacity after recommended physical reserve")
    return {
        "physical_total_bytes": total,
        "recommended_reserve_bytes": total - allocatable,
        "allocatable_bytes": allocatable,
        "allocations": allocations,
        "unallocated_bytes": allocatable - allocated,
    }


def evaluate_physical_capacity(drives: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    expected = {"D", "E", "F"}
    if set(drives) != expected:
        raise ValueError("drives must contain exactly D, E, and F")

    results: dict[str, dict[str, Any]] = {}
    overall = "READY"
    for drive in sorted(expected):
        total = _non_negative_int(drives[drive].get("total_bytes"), f"drives.{drive}.total_bytes")
        free = _non_negative_int(drives[drive].get("free_bytes"), f"drives.{drive}.free_bytes")
        if total == 0 or free > total:
            raise ValueError(f"drives.{drive} capacity is invalid")
        recommended = total * RECOMMENDED_FREE_BPS // 10_000
        hard = total * HARD_FREE_BPS // 10_000
        if free < hard:
            state = "BLOCKED_BELOW_HARD_RESERVE"
            overall = "BLOCKED_BELOW_HARD_RESERVE"
        elif free < recommended:
            state = "BELOW_RECOMMENDED_RESERVE"
            if overall == "READY":
                overall = state
        else:
            state = "READY"
        results[drive] = {
            "total_bytes": total,
            "free_bytes": free,
            "recommended_free_bytes": recommended,
            "hard_free_bytes": hard,
            "state": state,
        }

    return {
        "contract_version": CONTRACT_VERSION,
        "read_only": True,
        "state": overall,
        "drives": results,
        "e_allocation_budgets": e_allocation_budgets(results["E"]["total_bytes"]),
        "production_mutation_authorized": False,
    }


def evaluate_capacity_inventory(
    physical_drives: Mapping[str, Mapping[str, Any]],
    vhdx_disks: Mapping[str, Mapping[str, Any]],
    e_allocations: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    physical = evaluate_physical_capacity(physical_drives)
    vhdx_results: dict[str, dict[str, Any]] = {}
    alerts: list[dict[str, str]] = []
    overall = physical["state"]

    for drive, result in physical["drives"].items():
        if result["state"] != "READY":
            alerts.append(
                {
                    "severity": "CRITICAL"
                    if result["state"] == "BLOCKED_BELOW_HARD_RESERVE"
                    else "WARNING",
                    "dimension": "physical_drive",
                    "name": drive,
                    "state": result["state"],
                }
            )

    for name in sorted(vhdx_disks):
        if VHDX_NAME.fullmatch(name) is None:
            raise ValueError(f"unsupported VHDX identity: {name}")
        total = _non_negative_int(vhdx_disks[name].get("total_bytes"), f"vhdx.{name}.total_bytes")
        free = _non_negative_int(vhdx_disks[name].get("free_bytes"), f"vhdx.{name}.free_bytes")
        if total == 0 or free > total:
            raise ValueError(f"vhdx.{name} capacity is invalid")
        recommended = total * RECOMMENDED_FREE_BPS // 10_000
        hard = total * HARD_FREE_BPS // 10_000
        if free < hard:
            state = "BLOCKED_BELOW_HARD_RESERVE"
            overall = state
        elif free < recommended:
            state = "BELOW_RECOMMENDED_RESERVE"
            if overall == "READY":
                overall = state
        else:
            state = "READY"
        vhdx_results[name] = {
            "total_bytes": total,
            "free_bytes": free,
            "recommended_free_bytes": recommended,
            "hard_free_bytes": hard,
            "state": state,
        }
        if state != "READY":
            alerts.append(
                {
                    "severity": "CRITICAL"
                    if state == "BLOCKED_BELOW_HARD_RESERVE"
                    else "WARNING",
                    "dimension": "vhdx",
                    "name": name,
                    "state": state,
                }
            )

    return {
        "contract_version": CONTRACT_VERSION,
        "read_only": True,
        "state": overall,
        "physical_drives": physical["drives"],
        "vhdx_disks": vhdx_results,
        "e_allocation_budgets": e_allocation_budgets(
            physical["drives"]["E"]["total_bytes"], e_allocations
        ),
        "alerts": alerts,
        "automatic_remediation": False,
        "production_mutation_authorized": False,
    }


def dedicated_jurisdiction_review(
    *,
    available_global_budget_bytes: int,
    projected_180_day_bytes: int,
    measured_query_slo_breach: bool = False,
    contention_attributable_to_jurisdiction: bool = False,
    regulatory_isolation_required: bool = False,
) -> dict[str, Any]:
    available = _non_negative_int(available_global_budget_bytes, "available_global_budget_bytes")
    projected = _non_negative_int(projected_180_day_bytes, "projected_180_day_bytes")
    booleans = (
        measured_query_slo_breach,
        contention_attributable_to_jurisdiction,
        regulatory_isolation_required,
    )
    if not all(isinstance(value, bool) for value in booleans):
        raise ValueError("jurisdiction promotion evidence is invalid")

    capacity_pressure = projected > available
    measured_contention = measured_query_slo_breach and contention_attributable_to_jurisdiction
    eligible = regulatory_isolation_required or capacity_pressure or measured_contention
    return {
        "contract_version": CONTRACT_VERSION,
        "decision": "ELIGIBLE_FOR_REVIEW" if eligible else "DEFAULT_GLOBAL",
        "available_global_budget_bytes": available,
        "projected_180_day_bytes": projected,
        "capacity_pressure": capacity_pressure,
        "measured_query_slo_breach": measured_query_slo_breach,
        "contention_attributable_to_jurisdiction": contention_attributable_to_jurisdiction,
        "regulatory_isolation_required": regulatory_isolation_required,
        "capacity_fit_required": True,
        "separate_approval_required": True,
        "automatic_promotion": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Print the read-only Storage Topology V2 contract.")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument(
        "--inventory",
        type=Path,
        help="Evaluate a JSON inventory containing physical_drives and vhdx_disks.",
    )
    args = parser.parse_args()
    report = build_storage_topology()
    if args.inventory is not None:
        inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
        if not isinstance(inventory, dict):
            raise ValueError("inventory must be a JSON object")
        report = evaluate_capacity_inventory(
            inventory.get("physical_drives", {}),
            inventory.get("vhdx_disks", {}),
            inventory.get("e_allocations"),
        )
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=None if args.compact else 2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
