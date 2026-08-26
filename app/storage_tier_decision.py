from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.storage_capacity_profile import build_live_capacity_profile
from app.storage_consumer_inventory import build_inventory


DECISION_VERSION = "DATA_ENGINE_STORAGE_TIER_DECISION_V1"

PROTECTED_CURRENT_TABLES = (
    "cn_goods_item_current",
    "cn_observed_event",
    "cn_case_party_current",
)

EXPECTED_TABLE_DECISIONS = {
    "cn_goods_item_current": "HOT_REQUIRED",
    "cn_goods_item_observation": "WARM_CANDIDATE_REQUIRES_SUMMARY_REPLACEMENT",
    "cn_observed_event": "HOT_WITH_COMPACTABLE_BASELINE",
    "cn_case_party_current": "HOT_REQUIRED",
    "cn_case_party_relation_history": "WARM_CANDIDATE_PENDING_VERIFICATION",
}

BLOCKED_ACTIONS = (
    "DROP_OR_DEMOTE_CN_GOODS_ITEM_CURRENT",
    "DROP_OR_DEMOTE_CN_CASE_PARTY_CURRENT",
    "NARROW_CN_GOODS_CURRENT_BEHIND_DYNAMIC_SELECT_ALL_API",
    "MOVE_UNVERIFIED_CN_EVENT_DELTAS_TO_WARM",
    "DELETE_LEGACY_PARTY_HISTORY_BEFORE_RECOVERY_VERIFICATION",
    "LIVE_OPTIMIZE_FINAL_FOR_CAPACITY_RECLAMATION",
    "DELETE_SOURCE_DOCKER_VOLUME_DURING_CUTOVER",
    "REIMPORT_OR_REVALIDATE_ACCEPTED_CN_SOURCE_PACKAGES_FOR_STORAGE_CUTOVER",
)


def _table_index(payload: dict[str, Any], key: str = "table") -> dict[str, dict[str, Any]]:
    return {
        str(row.get(key)): dict(row)
        for row in payload.get("tables") or []
        if row.get(key)
    }


def _metadata_equal(before: Any, after: Any) -> bool:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return False
    fields = (
        "active_table_count",
        "active_part_count",
        "active_rows",
        "active_bytes_on_disk",
    )
    try:
        return all(int(before[field]) == int(after[field]) for field in fields)
    except (KeyError, TypeError, ValueError):
        return False


def classify_placement_evidence(evidence: dict[str, Any] | None) -> dict[str, Any]:
    if not evidence:
        return {
            "status": "WAITING_TARGET_HOST_READINESS",
            "passed": False,
            "safe_to_cutover": False,
            "migration_completed": False,
            "reason": "No target-host readiness or completed-cutover evidence was supplied.",
        }

    if isinstance(evidence.get("readiness"), dict) and not evidence.get("migration_completed"):
        evidence = dict(evidence["readiness"])

    if evidence.get("migration_completed") is True:
        metadata_ok = _metadata_equal(
            evidence.get("metadata_before"), evidence.get("metadata_after")
        )
        checks = {
            "hot_cold_activated": evidence.get("hot_cold_activated") is True,
            "source_volume_retained": evidence.get("source_volume_retained") is True,
            "cold_disk_registered": evidence.get("cold_disk_registered") is True,
            "source_packages_revalidated": evidence.get("source_packages_revalidated") is False,
            "rollback_available": evidence.get("rollback_available") is True,
            "metadata_equal": metadata_ok,
        }
        passed = all(checks.values())
        return {
            "status": "PASS" if passed else "BLOCKED_CUTOVER_EVIDENCE",
            "passed": passed,
            "safe_to_cutover": True,
            "migration_completed": True,
            "checks": checks,
            "reason": (
                "Hot/Cold cutover evidence is complete and metadata-equivalent."
                if passed
                else "Completed-cutover evidence failed one or more fail-closed checks."
            ),
        }

    safe_to_cutover = evidence.get("safe_to_cutover") is True
    if safe_to_cutover:
        return {
            "status": "READY_FOR_CONTROLLED_CUTOVER",
            "passed": False,
            "safe_to_cutover": True,
            "migration_completed": False,
            "reason": (
                "Readiness is green, but storage placement is not accepted until the "
                "controlled cutover completes and metadata equivalence is proven."
            ),
        }

    return {
        "status": "BLOCKED_TARGET_HOST_READINESS",
        "passed": False,
        "safe_to_cutover": False,
        "migration_completed": False,
        "reason": "Target-host readiness is not green.",
    }


def classify_representation_contract(
    consumer_inventory: dict[str, Any],
) -> dict[str, Any]:
    table_rows = _table_index(consumer_inventory)
    violations: list[str] = []

    if consumer_inventory.get("status") != "PASS":
        violations.append("CONSUMER_INVENTORY_NOT_PASS")
    if consumer_inventory.get("missing_serving_anchors"):
        violations.append("MISSING_SERVING_ANCHOR")

    for table, expected in EXPECTED_TABLE_DECISIONS.items():
        row = table_rows.get(table)
        if row is None:
            violations.append(f"MISSING_TABLE_CONTRACT:{table}")
            continue
        if row.get("current_tier_decision") != expected:
            violations.append(f"TIER_CONTRACT_DRIFT:{table}")

    for table in PROTECTED_CURRENT_TABLES:
        row = table_rows.get(table)
        if row is None:
            continue
        if int(row.get("direct_serving_read_count") or 0) <= 0:
            violations.append(f"DIRECT_SERVING_ANCHOR_MISSING:{table}")

    passed = not violations
    return {
        "status": "PASS" if passed else "BLOCKED_CONTRACT_DRIFT",
        "passed": passed,
        "violations": sorted(violations),
        "protected_current_tables": list(PROTECTED_CURRENT_TABLES),
        "reason": (
            "Current serving anchors and reconstructibility constraints are explicit."
            if passed
            else "Storage representation contract drift must be resolved before scale-out."
        ),
    }


def _capacity_summary(capacity_profile: dict[str, Any]) -> dict[str, Any]:
    tables = _table_index(capacity_profile)
    protected_bytes = sum(
        int((tables.get(table) or {}).get("bytes_on_disk") or 0)
        for table in PROTECTED_CURRENT_TABLES
    )
    observation_bytes = int(
        (tables.get("cn_goods_item_observation") or {}).get("bytes_on_disk") or 0
    )
    event_bytes = int((tables.get("cn_observed_event") or {}).get("bytes_on_disk") or 0)

    return {
        "active_bytes": int(capacity_profile.get("active_bytes") or 0),
        "active_rows": int(capacity_profile.get("active_rows") or 0),
        "protected_current_table_bytes": protected_bytes,
        "goods_observation_warm_candidate_upper_bound_bytes": observation_bytes,
        "observed_event_table_bytes": event_bytes,
        "observed_event_reclaimable_bytes": None,
        "observed_event_note": (
            "Only a verified reconstructible baseline subset may compact; the whole event "
            "table is not a Warm candidate, so no reclaim estimate is asserted here."
        ),
    }


def build_storage_tier_decision(
    capacity_profile: dict[str, Any],
    consumer_inventory: dict[str, Any],
    placement_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    representation = classify_representation_contract(consumer_inventory)
    placement = classify_placement_evidence(placement_evidence)
    capacity = _capacity_summary(capacity_profile)

    families = {
        "cn_case_core": {
            "tier": "HOT_REQUIRED",
            "decision": "KEEP_CURRENT_SERVING_STATE_HOT",
        },
        "cn_goods": {
            "tier": "SPLIT_HOT_WARM",
            "decision": (
                "KEEP_CN_GOODS_ITEM_CURRENT_HOT; GOODS OBSERVATION BASELINE/HISTORY MAY "
                "MOVE WARM ONLY AFTER SUMMARY CONTRACT REPLACEMENT AND EQUIVALENCE PROOF."
            ),
        },
        "cn_events": {
            "tier": "HOT_WITH_VERIFIED_WARM_SUBSET",
            "decision": (
                "KEEP API-SERVING EVENTS HOT; COMPACT OR MOVE ONLY VERIFIED RECONSTRUCTIBLE "
                "BASELINES, NEVER TRUE DELTAS OR PRIOR-STATE EVIDENCE BY DEFAULT."
            ),
        },
        "cn_party": {
            "tier": "SPLIT_HOT_WARM",
            "decision": (
                "KEEP CN_CASE_PARTY_CURRENT HOT; LEGACY WIDE HISTORY IS WARM-CANDIDATE "
                "ONLY AFTER RECOVERY/CONSUMER VERIFICATION."
            ),
        },
        "cn_reference": {
            "tier": "HOT_SMALL_REFERENCE",
            "decision": "KEEP ONLINE; NOT A CURRENT CAPACITY PRIORITY.",
        },
    }

    storage_scale_out_allowed = representation["passed"] and placement["passed"]
    required_evidence: list[str] = []
    if not representation["passed"]:
        required_evidence.append("RESTORE_STORAGE_CONSUMER_AND_TIER_CONTRACT")
    if placement["status"] == "WAITING_TARGET_HOST_READINESS":
        required_evidence.append("TARGET_HOST_READ_ONLY_CUTOVER_READINESS")
    elif placement["status"] == "READY_FOR_CONTROLLED_CUTOVER":
        required_evidence.append("COMPLETED_HOT_COLD_CUTOVER_WITH_METADATA_EQUIVALENCE")
    elif not placement["passed"]:
        required_evidence.append("RESOLVE_TARGET_HOST_STORAGE_PLACEMENT_BLOCKER")

    return {
        "decision_version": DECISION_VERSION,
        "read_only": True,
        "representation_gate": representation,
        "placement_gate": placement,
        "families": families,
        "capacity": capacity,
        "blocked_actions": list(BLOCKED_ACTIONS),
        "required_evidence": required_evidence,
        "storage_scale_out": {
            "status": "GO" if storage_scale_out_allowed else "NO_GO",
            "allowed": storage_scale_out_allowed,
            "scope": "STORAGE_ARCHITECTURE_ONLY",
            "note": (
                "GO removes the storage-architecture blocker only; jurisdiction-specific "
                "credentials, acceptance gates, and rollout approvals still apply."
            ),
        },
    }


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail-closed MarkOrbit Hot/Warm storage tier and scale-out decision."
    )
    parser.add_argument("--capacity-json", type=Path, default=None)
    parser.add_argument("--inventory-json", type=Path, default=None)
    parser.add_argument("--placement-json", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--require-storage-scale-out-go", action="store_true")
    args = parser.parse_args()

    capacity = (
        _read_json(args.capacity_json)
        if args.capacity_json is not None
        else build_live_capacity_profile()
    )
    inventory = (
        _read_json(args.inventory_json)
        if args.inventory_json is not None
        else build_inventory()
    )
    placement = (
        _read_json(args.placement_json) if args.placement_json is not None else None
    )
    report = build_storage_tier_decision(capacity, inventory, placement)
    payload = json.dumps(
        report,
        ensure_ascii=False,
        indent=None if args.compact else 2,
        sort_keys=True,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)

    if not report["representation_gate"]["passed"]:
        return 4
    if args.require_storage_scale_out_go and not report["storage_scale_out"]["allowed"]:
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
