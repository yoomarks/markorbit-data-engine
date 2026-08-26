from __future__ import annotations

from app.storage_reclaim_projection import build_reclaim_projection


def _capacity() -> dict:
    rows = [
        ("cn_goods_item_current", 345_800, 1_000),
        ("cn_goods_item_observation", 54_700, 200),
        ("cn_observed_event", 119_100, 400),
        ("cn_case_party_current", 71_600, 300),
        ("cn_case_party_relation_history", 10_000, 100),
    ]
    return {
        "active_bytes": sum(row[1] for row in rows),
        "active_rows": sum(row[2] for row in rows),
        "tables": [
            {"table": table, "bytes_on_disk": byte_count, "rows": row_count}
            for table, byte_count, row_count in rows
        ],
    }


def _deep_audit() -> dict:
    return {
        "audit_version": "DATA_ENGINE_STORAGE_V2_AUDIT_V2",
        "mode": "deep",
        "read_only": True,
        "cn_goods_item_observation": {
            "first_observed_rows": 80,
            "reobserved_rows": 20,
        },
        "cn_observed_event": {
            "reconstructible_baseline_candidate_rows": 100,
        },
        "cn_case_party_relation_history": {
            "observed_current_rows": 50,
        },
    }


def test_projection_does_not_start_or_assume_deep_audit() -> None:
    report = build_reclaim_projection(_capacity())

    assert report["status"] == "WAITING_DEEP_AUDIT_EVIDENCE"
    assert report["planning_reclaim_estimate_bytes"] is None
    assert report["us_projection"]["status"].startswith("NO_NUMERIC_ESTIMATE")
    assert report["global_projection"]["status"].startswith("NO_NUMERIC_ESTIMATE")
    assert report["authorization"] == "NONE_READ_ONLY_PLANNING_EVIDENCE"


def test_projection_uses_candidate_row_share_only_as_planning_estimate() -> None:
    report = build_reclaim_projection(_capacity(), _deep_audit())

    assert report["status"] == "PASS_PLANNING_PROJECTION_AVAILABLE"
    candidates = {row["table"]: row for row in report["candidates"]}

    goods = candidates["cn_goods_item_observation"]
    assert goods["candidate_rows"] == 100
    assert goods["candidate_row_share"] == 0.5
    assert goods["planning_estimate_bytes"] == 27_350
    assert goods["measured_reclaimable_bytes"] is None

    events = candidates["cn_observed_event"]
    assert events["candidate_row_share"] == 0.25
    assert events["planning_estimate_bytes"] == 29_775

    party = candidates["cn_case_party_relation_history"]
    assert party["candidate_row_share"] == 0.5
    assert party["planning_estimate_bytes"] == 5_000

    assert report["planning_reclaim_estimate_bytes"] == 62_125
    assert report["planning_retained_active_bytes"] == report["active_bytes"] - 62_125


def test_projection_protects_current_serving_tables_from_candidate_set() -> None:
    report = build_reclaim_projection(_capacity(), _deep_audit())

    candidate_tables = {row["table"] for row in report["candidates"]}
    assert "cn_goods_item_current" not in candidate_tables
    assert "cn_case_party_current" not in candidate_tables
    assert set(report["protected_current_tables"]) == {
        "cn_goods_item_current",
        "cn_observed_event",
        "cn_case_party_current",
    }


def test_invalid_non_deep_receipt_fails_closed() -> None:
    audit = _deep_audit()
    audit["mode"] = "physical"

    report = build_reclaim_projection(_capacity(), audit)

    assert report["status"] == "BLOCKED_INVALID_DEEP_AUDIT_EVIDENCE"
    assert "DEEP_AUDIT_MODE_REQUIRED" in report["violations"]
    assert report["planning_reclaim_estimate_bytes"] is None


def test_candidate_count_larger_than_active_table_rows_fails_closed() -> None:
    audit = _deep_audit()
    audit["cn_observed_event"]["reconstructible_baseline_candidate_rows"] = 401

    report = build_reclaim_projection(_capacity(), audit)

    assert report["status"] == "BLOCKED_DEEP_AUDIT_CAPACITY_MISMATCH"
    assert "cn_observed_event" in report["violations"][0]
    assert report["planning_reclaim_estimate_bytes"] is None


def test_missing_legacy_party_history_table_does_not_invent_bytes() -> None:
    capacity = _capacity()
    capacity["tables"] = [
        row
        for row in capacity["tables"]
        if row["table"] != "cn_case_party_relation_history"
    ]
    capacity["active_bytes"] -= 10_000
    capacity["active_rows"] -= 100

    report = build_reclaim_projection(capacity, _deep_audit())
    party = next(
        row
        for row in report["candidates"]
        if row["table"] == "cn_case_party_relation_history"
    )

    assert party["status"] == "TABLE_NOT_PRESENT_IN_CAPACITY_PROFILE"
    assert party["planning_estimate_bytes"] is None
