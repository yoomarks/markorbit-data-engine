from __future__ import annotations

from copy import deepcopy

from app.storage_tier_decision import build_storage_tier_decision


def _capacity() -> dict:
    rows = [
        ("cn_goods_item_current", "cn_goods", 345_800_000_000, 1_639_720_127),
        ("cn_goods_item_observation", "cn_goods", 54_700_000_000, 219_463_289),
        ("cn_observed_event", "cn_events", 119_100_000_000, 413_031_435),
        ("cn_case_party_current", "cn_party", 71_600_000_000, 264_649_807),
        ("cn_case_current", "cn_case_core", 26_300_000_000, 132_205_465),
    ]
    return {
        "profile_version": "DATA_ENGINE_STORAGE_CAPACITY_PROFILE_V1",
        "read_only": True,
        "active_bytes": sum(row[2] for row in rows),
        "active_rows": sum(row[3] for row in rows),
        "tables": [
            {
                "table": table,
                "family": family,
                "bytes_on_disk": byte_count,
                "rows": row_count,
            }
            for table, family, byte_count, row_count in rows
        ],
    }


def _inventory() -> dict:
    decisions = {
        "cn_goods_item_current": "HOT_REQUIRED",
        "cn_goods_item_observation": "WARM_CANDIDATE_REQUIRES_SUMMARY_REPLACEMENT",
        "cn_observed_event": "HOT_WITH_COMPACTABLE_BASELINE",
        "cn_case_party_current": "HOT_REQUIRED",
        "cn_case_party_relation_history": "WARM_CANDIDATE_PENDING_VERIFICATION",
    }
    direct_reads = {
        "cn_goods_item_current": 1,
        "cn_goods_item_observation": 0,
        "cn_observed_event": 1,
        "cn_case_party_current": 1,
        "cn_case_party_relation_history": 0,
    }
    return {
        "contract_version": "DATA_ENGINE_STORAGE_CONSUMER_CONTRACT_V1",
        "status": "PASS",
        "missing_serving_anchors": [],
        "tables": [
            {
                "table": table,
                "current_tier_decision": decision,
                "direct_serving_read_count": direct_reads[table],
            }
            for table, decision in decisions.items()
        ],
    }


def _metadata() -> dict:
    return {
        "active_table_count": 12,
        "active_part_count": 345,
        "active_rows": 2_948_784_069,
        "active_bytes_on_disk": 679_200_000_000,
    }


def test_decision_is_no_go_without_target_host_evidence() -> None:
    report = build_storage_tier_decision(_capacity(), _inventory())

    assert report["representation_gate"]["status"] == "PASS"
    assert report["placement_gate"]["status"] == "WAITING_TARGET_HOST_READINESS"
    assert report["storage_scale_out"]["status"] == "NO_GO"
    assert "TARGET_HOST_READ_ONLY_CUTOVER_READINESS" in report["required_evidence"]
    assert "DROP_OR_DEMOTE_CN_GOODS_ITEM_CURRENT" in report["blocked_actions"]


def test_green_readiness_allows_cutover_but_not_scale_out() -> None:
    report = build_storage_tier_decision(
        _capacity(),
        _inventory(),
        {"safe_to_cutover": True},
    )

    assert report["placement_gate"]["status"] == "READY_FOR_CONTROLLED_CUTOVER"
    assert report["placement_gate"]["passed"] is False
    assert report["storage_scale_out"]["allowed"] is False
    assert (
        "COMPLETED_HOT_COLD_CUTOVER_WITH_METADATA_EQUIVALENCE"
        in report["required_evidence"]
    )


def test_completed_metadata_equivalent_cutover_removes_storage_blocker() -> None:
    metadata = _metadata()
    evidence = {
        "migration_completed": True,
        "hot_cold_activated": True,
        "source_volume_retained": True,
        "cold_disk_registered": True,
        "source_packages_revalidated": False,
        "rollback_available": True,
        "metadata_before": metadata,
        "metadata_after": dict(metadata),
    }

    report = build_storage_tier_decision(_capacity(), _inventory(), evidence)

    assert report["placement_gate"]["status"] == "PASS"
    assert report["representation_gate"]["status"] == "PASS"
    assert report["storage_scale_out"] == {
        "status": "GO",
        "allowed": True,
        "scope": "STORAGE_ARCHITECTURE_ONLY",
        "note": (
            "GO removes the storage-architecture blocker only; jurisdiction-specific "
            "credentials, acceptance gates, and rollout approvals still apply."
        ),
    }


def test_completed_cutover_with_metadata_drift_is_blocked() -> None:
    before = _metadata()
    after = dict(before)
    after["active_rows"] += 1
    evidence = {
        "migration_completed": True,
        "hot_cold_activated": True,
        "source_volume_retained": True,
        "cold_disk_registered": True,
        "source_packages_revalidated": False,
        "rollback_available": True,
        "metadata_before": before,
        "metadata_after": after,
    }

    report = build_storage_tier_decision(_capacity(), _inventory(), evidence)

    assert report["placement_gate"]["status"] == "BLOCKED_CUTOVER_EVIDENCE"
    assert report["placement_gate"]["checks"]["metadata_equal"] is False
    assert report["storage_scale_out"]["allowed"] is False


def test_current_serving_tier_contract_drift_fails_closed() -> None:
    inventory = deepcopy(_inventory())
    goods = next(
        row for row in inventory["tables"] if row["table"] == "cn_goods_item_current"
    )
    goods["current_tier_decision"] = "WARM_CANDIDATE"

    report = build_storage_tier_decision(_capacity(), inventory)

    assert report["representation_gate"]["status"] == "BLOCKED_CONTRACT_DRIFT"
    assert (
        "TIER_CONTRACT_DRIFT:cn_goods_item_current"
        in report["representation_gate"]["violations"]
    )
    assert report["storage_scale_out"]["allowed"] is False


def test_capacity_report_does_not_claim_whole_event_table_reclaimable() -> None:
    report = build_storage_tier_decision(_capacity(), _inventory())

    capacity = report["capacity"]
    assert capacity["goods_observation_warm_candidate_upper_bound_bytes"] == 54_700_000_000
    assert capacity["observed_event_table_bytes"] == 119_100_000_000
    assert capacity["observed_event_reclaimable_bytes"] is None
    assert report["families"]["cn_goods"]["tier"] == "SPLIT_HOT_WARM"
    assert report["families"]["cn_events"]["tier"] == "HOT_WITH_VERIFIED_WARM_SUBSET"
