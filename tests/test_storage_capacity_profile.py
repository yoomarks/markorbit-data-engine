from app.storage_capacity_profile import build_capacity_profile


def test_capacity_profile_groups_active_table_families_and_shares() -> None:
    profile = build_capacity_profile(
        [
            {
                "table": "cn_goods_item_current",
                "active": True,
                "rows": 100,
                "bytes_on_disk": 600,
            },
            {
                "table": "cn_observed_event",
                "active": True,
                "rows": 50,
                "bytes_on_disk": 300,
            },
            {
                "table": "cn_case_current",
                "active": True,
                "rows": 25,
                "bytes_on_disk": 100,
            },
            {
                "table": "cn_goods_item_current",
                "active": False,
                "rows": 999,
                "bytes_on_disk": 999,
            },
        ]
    )

    assert profile["read_only"] is True
    assert profile["active_bytes"] == 1000
    assert profile["active_rows"] == 175
    assert [row["family"] for row in profile["families"]] == [
        "cn_goods",
        "cn_events",
        "cn_case_core",
    ]
    assert profile["families"][0]["byte_share"] == 0.6
    assert profile["largest_tables"][0]["table"] == "cn_goods_item_current"
    assert profile["review_priority"] == ["cn_goods", "cn_events"]


def test_capacity_profile_keeps_tier_decision_uncommitted() -> None:
    profile = build_capacity_profile([])

    assert profile["active_bytes"] == 0
    assert profile["active_rows"] == 0
    assert profile["families"] == []
    assert profile["tables"] == []
    assert profile["tier_decision"] == "REQUIRES_CONSUMER_AND_RECONSTRUCTIBILITY_REVIEW"
