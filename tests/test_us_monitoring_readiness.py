from __future__ import annotations

import app.main as main
from app.us.monitoring_readiness import (
    SILENCE_TRUSTED,
    SILENCE_UNTRUSTED,
    evaluate_monitoring_readiness,
)


def _domain(name: str, *, queryable: bool, trusted: bool, state: str = "READY") -> dict:
    return {
        "domain": name,
        "state": state,
        "queryable": queryable,
        "trusted": trusted,
        "reason_codes": [],
    }


def test_monitoring_readiness_route_is_get_only_and_mounted() -> None:
    routes = {
        route.path: route.methods
        for route in main.app.routes
        if route.path == "/api/us/alerts/readiness"
    }
    assert routes == {"/api/us/alerts/readiness": {"GET"}}


def test_all_trusted_domains_make_all_alert_feeds_ready() -> None:
    report = evaluate_monitoring_readiness(
        application=_domain("application", queryable=True, trusted=True),
        assignment=_domain("assignment", queryable=True, trusted=True),
        ttab=_domain("ttab", queryable=True, trusted=True),
        event_roles=_domain("reviewed_event_roles", queryable=True, trusted=True),
        expected_history_parts=5,
        verify_sources=True,
    )
    assert report["state"] == "READY"
    assert report["ready"] is True
    assert report["coverage"]["trusted_feed_count"] == 5
    assert report["coverage"]["all_feed_silence_trusted"] is True
    for feed in report["feeds"].values():
        assert feed["trusted_for_silence"] is True
        assert feed["silence_semantics"] == SILENCE_TRUSTED
    assert report["legal_status_inference"] is False
    assert report["legal_ownership_conclusion"] is False
    assert report["ttab_outcome_conclusion"] is False
    assert report["substantive_rights_conclusion"] is False


def test_zero_alerts_are_not_trusted_when_sources_are_unverified() -> None:
    report = evaluate_monitoring_readiness(
        application=_domain(
            "application",
            queryable=True,
            trusted=False,
            state="UNVERIFIED_OR_WARNINGS",
        ),
        assignment=_domain("assignment", queryable=True, trusted=False, state="UNVERIFIED"),
        ttab=_domain("ttab", queryable=False, trusted=False, state="NOT_READY"),
        event_roles=_domain("reviewed_event_roles", queryable=False, trusted=False, state="NOT_READY"),
        expected_history_parts=None,
        verify_sources=False,
    )
    assert report["state"] == "UNVERIFIED"
    assert report["ready"] is False
    assert report["coverage"]["trusted_feed_count"] == 0
    assert report["feeds"]["case_changes"]["queryable"] is True
    assert report["feeds"]["case_changes"]["trusted_for_silence"] is False
    assert report["feeds"]["case_changes"]["silence_semantics"] == SILENCE_UNTRUSTED
    assert report["feeds"]["reviewed_events"]["queryable"] is False
    assert report["feeds"]["deadlines"]["state"] == "UNVERIFIED"


def test_deadline_feed_is_partial_without_reviewed_event_roles() -> None:
    report = evaluate_monitoring_readiness(
        application=_domain("application", queryable=True, trusted=True),
        assignment=_domain("assignment", queryable=True, trusted=True),
        ttab=_domain("ttab", queryable=True, trusted=True),
        event_roles={
            **_domain(
                "reviewed_event_roles",
                queryable=False,
                trusted=False,
                state="NOT_READY",
            ),
            "reason_codes": ["active_event_role_ruleset_missing"],
        },
        expected_history_parts=5,
        verify_sources=True,
    )
    deadlines = report["feeds"]["deadlines"]
    assert report["state"] == "PARTIAL"
    assert deadlines["state"] == "PARTIAL"
    assert deadlines["queryable"] is True
    assert deadlines["trusted_for_silence"] is False
    assert deadlines["capabilities"]["maintenance"] == {
        "available": True,
        "trusted": True,
    }
    assert deadlines["capabilities"]["publication"] == {
        "available": True,
        "trusted": True,
    }
    assert deadlines["capabilities"]["reviewed_oa_noa"] == {
        "available": False,
        "trusted": False,
    }


def test_independent_assignment_or_ttab_readiness_keeps_monitoring_partial() -> None:
    report = evaluate_monitoring_readiness(
        application=_domain("application", queryable=False, trusted=False, state="FAILED"),
        assignment=_domain("assignment", queryable=True, trusted=True),
        ttab=_domain("ttab", queryable=True, trusted=True),
        event_roles=_domain("reviewed_event_roles", queryable=True, trusted=True),
        expected_history_parts=1,
        verify_sources=True,
    )
    assert report["state"] == "PARTIAL"
    assert set(report["coverage"]["failed_domains"]) == {"application"}
    assert report["feeds"]["assignments"]["trusted_for_silence"] is True
    assert report["feeds"]["ttab"]["trusted_for_silence"] is True
    assert report["feeds"]["case_changes"]["queryable"] is False
    assert report["feeds"]["reviewed_events"]["queryable"] is False


def test_no_queryable_domain_is_not_ready() -> None:
    report = evaluate_monitoring_readiness(
        application=_domain("application", queryable=False, trusted=False, state="NOT_READY"),
        assignment=_domain("assignment", queryable=False, trusted=False, state="NOT_READY"),
        ttab=_domain("ttab", queryable=False, trusted=False, state="NOT_READY"),
        event_roles=_domain("reviewed_event_roles", queryable=False, trusted=False, state="NOT_READY"),
        expected_history_parts=None,
        verify_sources=False,
    )
    assert report["state"] == "NOT_READY"
    assert report["coverage"]["queryable_feed_count"] == 0
    assert report["coverage"]["trusted_feed_count"] == 0
    assert all(
        feed["silence_semantics"] == SILENCE_UNTRUSTED
        for feed in report["feeds"].values()
    )
