from __future__ import annotations

from datetime import date
from pathlib import Path

from app.config import get_settings
from app.us.alert_engine import (
    _reviewed_event_rows,
    alert_engine_schema,
    scan_assignment_alerts,
    scan_case_change_alerts,
    scan_deadline_alerts,
    scan_reviewed_event_alerts,
    scan_ttab_alerts,
)
from app.us.monitoring_readiness import build_monitoring_readiness


def _assert_event_contract(feed: dict) -> None:
    for event in feed.get("events") or []:
        assert len(str(event["event_id"])) == 64
        assert event["event_type"]
        assert event["source_domain"]
        assert event["subject_key"]
        assert event["actionability"] == "REVIEW_REQUIRED"
        assert event["legal_status_inference"] is False
        assert event["legal_ownership_conclusion"] is False
        assert event["ttab_outcome_conclusion"] is False
        assert event["substantive_rights_conclusion"] is False


def main() -> None:
    raw_root = Path(get_settings().raw_data_root)
    schema = alert_engine_schema()
    assert schema["version"] == "US_ALERT_ENGINE_M1.0"
    assert schema["global_source_rank_ordering"] is False
    assert schema["source_boundary_preserved"] is True

    change_feed = scan_case_change_alerts(scan_limit=10)
    assignment_feed = scan_assignment_alerts(scan_limit=10)
    ttab_feed = scan_ttab_alerts(scan_limit=10)
    reviewed_feed = scan_reviewed_event_alerts(raw_root=raw_root, scan_limit=10)
    deadline_feed = scan_deadline_alerts(
        raw_root=raw_root,
        as_of=date(2026, 8, 9),
        scan_limit=10,
        horizon_days=90,
        recent_past_days=30,
    )

    # Force the reviewed-event ClickHouse query through the real runtime schema even
    # when CI intentionally has no active production event-role mapping.
    _reviewed_event_rows(
        event_codes=["CI_ALERT_QUERY_CONTRACT_NO_MATCH"],
        after_source_rank=0,
        after_event_key="",
        scan_limit=1,
    )

    for feed in (
        change_feed,
        assignment_feed,
        ttab_feed,
        reviewed_feed,
        deadline_feed,
    ):
        _assert_event_contract(feed)

    readiness = build_monitoring_readiness(
        raw_root=raw_root,
        expected_history_parts=1,
        verify_sources=False,
    )
    assert readiness["readiness_version"] == "US_MONITORING_READINESS_M1.0"
    assert readiness["state"] in {"READY", "PARTIAL", "UNVERIFIED", "NOT_READY", "FAILED"}
    assert set(readiness["feeds"]) == {
        "case_changes",
        "assignments",
        "ttab",
        "reviewed_events",
        "deadlines",
    }
    assert readiness["legal_status_inference"] is False
    assert readiness["legal_ownership_conclusion"] is False
    assert readiness["ttab_outcome_conclusion"] is False
    assert readiness["substantive_rights_conclusion"] is False
    for feed in readiness["feeds"].values():
        if not feed["trusted_for_silence"]:
            assert feed["silence_semantics"] == "SILENCE_IS_NOT_EVIDENCE_OF_NO_EVENT"

    print(
        "US_ALERT_ENGINE_M1.0_RUNTIME_FIXTURE=PASS "
        f"changes={change_feed['event_count']} "
        f"assignments={assignment_feed['event_count']} "
        f"ttab={ttab_feed['event_count']} "
        f"reviewed={reviewed_feed['event_count']} "
        f"deadlines={deadline_feed['event_count']} "
        f"monitoring_readiness={readiness['state']}"
    )


if __name__ == "__main__":
    main()
