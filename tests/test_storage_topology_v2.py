import json

import pytest

from app.storage_topology_v2 import (
    CONTRACT_VERSION,
    build_storage_topology,
    dedicated_jurisdiction_review,
    e_allocation_budgets,
    evaluate_capacity_inventory,
    evaluate_physical_capacity,
    main,
    placement_for,
)


GIB = 1024**3


def test_topology_freezes_physical_roles_reserves_and_non_authority() -> None:
    topology = build_storage_topology()

    assert topology["contract_version"] == CONTRACT_VERSION
    assert topology["physical_drives"]["D"]["placements"] == ["hot_cn", "hot_us"]
    assert topology["physical_drives"]["E"]["placements"] == [
        "hot_global",
        "warm_cn",
        "warm_us",
        "warm_global",
    ]
    assert topology["physical_drives"]["F"]["placements"] == [
        "raw",
        "backup",
        "original_visual",
        "knowledge_artifact",
    ]
    assert topology["physical_drives"]["F"]["primary_mergetree_serving"] is False
    assert topology["physical_reserve_policy"] == {
        "recommended_free_bps": 3_000,
        "hard_free_bps": 2_000,
    }
    assert topology["vhdx_reserve_policy"]["recommended_free_bps"] == 3_000
    assert topology["vhdx_reserve_policy"]["hard_free_bps"] == 2_000
    assert topology["monitoring"]["automatic_remediation"] is False
    assert topology["e_allocation_policy"]["placements"] == [
        "warm_cn",
        "hot_global",
        "warm_us",
        "warm_global",
    ]
    assert all(value is False for key, value in topology["constraints"].items() if "authorized" in key)


def test_default_placement_is_deterministic_for_cn_us_and_other_jurisdictions() -> None:
    assert placement_for("CN", "hot") == {"drive": "D", "placement": "hot_cn"}
    assert placement_for("US", "hot") == {"drive": "D", "placement": "hot_us"}
    assert placement_for("SG", "hot") == {"drive": "E", "placement": "hot_global"}
    assert placement_for("US", "warm") == {"drive": "E", "placement": "warm_us"}
    assert placement_for("EU", "warm") == {"drive": "E", "placement": "warm_global"}
    assert placement_for("CN", "original_visual") == {
        "drive": "F",
        "placement": "original_visual",
    }


def test_e_budgets_conserve_capacity_after_recommended_reserve() -> None:
    budgets = e_allocation_budgets(
        2_000 * GIB,
        {
            "warm_cn": 840 * GIB,
            "hot_global": 200 * GIB,
            "warm_us": 100 * GIB,
            "warm_global": 60 * GIB,
        },
    )

    assert budgets["allocatable_bytes"] == 1_400 * GIB
    assert budgets["allocations"]["warm_cn"] == 840 * GIB
    assert budgets["unallocated_bytes"] == 200 * GIB


def test_capacity_evaluation_reports_recommended_and_hard_reserve_states() -> None:
    report = evaluate_physical_capacity(
        {
            "D": {"total_bytes": 100 * GIB, "free_bytes": 35 * GIB},
            "E": {"total_bytes": 200 * GIB, "free_bytes": 50 * GIB},
            "F": {"total_bytes": 400 * GIB, "free_bytes": 75 * GIB},
        }
    )

    assert report["state"] == "BLOCKED_BELOW_HARD_RESERVE"
    assert report["drives"]["D"]["state"] == "READY"
    assert report["drives"]["E"]["state"] == "BELOW_RECOMMENDED_RESERVE"
    assert report["drives"]["F"]["state"] == "BLOCKED_BELOW_HARD_RESERVE"
    assert report["production_mutation_authorized"] is False


def test_inventory_audit_emits_physical_and_vhdx_alerts_without_remediation() -> None:
    report = evaluate_capacity_inventory(
        {
            "D": {"total_bytes": 100 * GIB, "free_bytes": 35 * GIB},
            "E": {"total_bytes": 200 * GIB, "free_bytes": 70 * GIB},
            "F": {"total_bytes": 400 * GIB, "free_bytes": 130 * GIB},
        },
        {
            "hot_us": {"total_bytes": 100 * GIB, "free_bytes": 25 * GIB},
            "warm_cn": {"total_bytes": 100 * GIB, "free_bytes": 19 * GIB},
        },
    )

    assert report["state"] == "BLOCKED_BELOW_HARD_RESERVE"
    assert report["alerts"] == [
        {
            "severity": "WARNING",
            "dimension": "vhdx",
            "name": "hot_us",
            "state": "BELOW_RECOMMENDED_RESERVE",
        },
        {
            "severity": "CRITICAL",
            "dimension": "vhdx",
            "name": "warm_cn",
            "state": "BLOCKED_BELOW_HARD_RESERVE",
        },
    ]
    assert report["automatic_remediation"] is False
    assert report["production_mutation_authorized"] is False


def test_inventory_accepts_governed_future_jurisdiction_vhdx_name() -> None:
    report = evaluate_capacity_inventory(
        {
            drive: {"total_bytes": 100, "free_bytes": 30}
            for drive in ("D", "E", "F")
        },
        {"hot_sg": {"total_bytes": 100, "free_bytes": 30}},
    )

    assert report["vhdx_disks"]["hot_sg"]["state"] == "READY"


def test_inventory_cli_emits_machine_readable_audit(tmp_path, monkeypatch, capsys) -> None:
    inventory = tmp_path / "inventory.json"
    inventory.write_text(
        json.dumps(
            {
                "physical_drives": {
                    name: {"total_bytes": 100, "free_bytes": 30}
                    for name in ("D", "E", "F")
                },
                "vhdx_disks": {"hot_us": {"total_bytes": 100, "free_bytes": 30}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.argv", ["storage_topology_v2", "--compact", "--inventory", str(inventory)])

    assert main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["state"] == "READY"
    assert report["alerts"] == []
    assert report["production_mutation_authorized"] is False


@pytest.mark.parametrize(
    ("kwargs", "decision"),
    [
        (
            {
                "available_global_budget_bytes": 250,
                "projected_180_day_bytes": 300,
            },
            "ELIGIBLE_FOR_REVIEW",
        ),
        (
            {
                "available_global_budget_bytes": 500,
                "projected_180_day_bytes": 100,
                "measured_query_slo_breach": True,
                "contention_attributable_to_jurisdiction": True,
            },
            "ELIGIBLE_FOR_REVIEW",
        ),
        (
            {
                "available_global_budget_bytes": 500,
                "projected_180_day_bytes": 200,
            },
            "DEFAULT_GLOBAL",
        ),
    ],
)
def test_dedicated_jurisdiction_review_is_deterministic(kwargs: dict, decision: str) -> None:
    result = dedicated_jurisdiction_review(**kwargs)

    assert result["decision"] == decision
    assert result["separate_approval_required"] is True
    assert result["automatic_promotion"] is False


def test_regulatory_isolation_only_makes_placement_eligible_for_review() -> None:
    result = dedicated_jurisdiction_review(
        available_global_budget_bytes=1_000,
        projected_180_day_bytes=1,
        regulatory_isolation_required=True,
    )

    assert result["decision"] == "ELIGIBLE_FOR_REVIEW"
    assert result["capacity_fit_required"] is True
    assert result["automatic_promotion"] is False


@pytest.mark.parametrize(
    "call",
    [
        lambda: placement_for("CN", "unknown"),
        lambda: e_allocation_budgets(-1),
        lambda: e_allocation_budgets(
            100,
            {"warm_cn": 71, "hot_global": 0, "warm_us": 0, "warm_global": 0},
        ),
        lambda: evaluate_physical_capacity({"D": {}, "E": {}}),
        lambda: evaluate_physical_capacity(
            {
                "D": {"total_bytes": 1, "free_bytes": 2},
                "E": {"total_bytes": 1, "free_bytes": 1},
                "F": {"total_bytes": 1, "free_bytes": 1},
            }
        ),
        lambda: evaluate_capacity_inventory(
            {
                "D": {"total_bytes": 1, "free_bytes": 1},
                "E": {"total_bytes": 1, "free_bytes": 1},
                "F": {"total_bytes": 1, "free_bytes": 1},
            },
            {"unexpected": {"total_bytes": 1, "free_bytes": 1}},
        ),
        lambda: dedicated_jurisdiction_review(
            available_global_budget_bytes=-1,
            projected_180_day_bytes=0,
        ),
    ],
)
def test_invalid_topology_inputs_fail_closed(call) -> None:
    with pytest.raises(ValueError):
        call()
