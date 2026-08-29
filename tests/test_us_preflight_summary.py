from __future__ import annotations

from app.us.preflight_summary import build_summary


def test_discovery_summary_accepts_only_unpinned_tail_not_ready():
    summary = build_summary(
        {
            "status": "NOT_READY",
            "preflight_version": "US_SOURCE_PREFLIGHT_V1",
            "safe_to_replay": False,
            "source_inventory": {
                "physical_source_count": 310,
                "semantic_source_count": 310,
                "history_source_count": 91,
                "daily_source_count": 219,
            },
            "historical_baseline_end": "2025-12-31",
            "archive_staging_required_count": 0,
            "hard_issue_types": [],
            "not_ready_reasons": ["historical_tail_part_count_not_pinned"],
            "warning_reasons": [],
        }
    )

    assert summary["discovery_only_not_ready"] is True
    assert summary["pinned_pass"] is False
    assert summary["history_source_count"] == 91
    assert summary["daily_source_count"] == 219


def test_discovery_summary_rejects_extra_not_ready_reason():
    summary = build_summary(
        {
            "status": "NOT_READY",
            "safe_to_replay": False,
            "source_inventory": {"history_source_count": 91},
            "hard_issue_types": [],
            "not_ready_reasons": [
                "historical_tail_part_count_not_pinned",
                "historical_part_sequence_incomplete",
            ],
        }
    )

    assert summary["discovery_only_not_ready"] is False
    assert summary["pinned_pass"] is False


def test_pinned_summary_requires_safe_pass_without_blockers():
    summary = build_summary(
        {
            "status": "PASS_WITH_WARNINGS",
            "safe_to_replay": True,
            "source_inventory": {
                "history_source_count": 91,
                "daily_source_count": 219,
            },
            "hard_issue_types": [],
            "not_ready_reasons": [],
            "warning_reasons": ["no_daily_packages_observed"],
        }
    )

    assert summary["discovery_only_not_ready"] is False
    assert summary["pinned_pass"] is True


def test_pinned_summary_rejects_hard_issue_even_if_status_claims_pass():
    summary = build_summary(
        {
            "status": "PASS",
            "safe_to_replay": True,
            "source_inventory": {"history_source_count": 91},
            "hard_issue_types": ["SEMANTIC_PARTITION_SHA_CONFLICT"],
            "not_ready_reasons": [],
        }
    )

    assert summary["pinned_pass"] is False
