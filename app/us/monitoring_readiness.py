from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from app.us.audit_real_data_v2 import build_audit as build_application_acceptance
from app.us.event_roles import load_active_event_role_map
from app.us_assignment.readiness import build_readiness as build_assignment_readiness
from app.us_ttab.readiness import build_readiness as build_ttab_readiness


MONITORING_READINESS_VERSION = "US_MONITORING_READINESS_M1.0"
MONITORING_SEMANTICS = (
    "MONITORING_COVERAGE_AND_SOURCE_READINESS_NOT_LEGAL_STATUS_OR_EVENT_ABSENCE_CONCLUSION"
)
SILENCE_TRUSTED = "NO_MATCHING_EVENTS_OBSERVED_IN_SCANNED_RANGE_SUBJECT_TO_CURSOR_AND_HORIZON"
SILENCE_UNTRUSTED = "SILENCE_IS_NOT_EVIDENCE_OF_NO_EVENT"


def _application_package_count(report: dict[str, Any]) -> int:
    packages = report.get("packages") or {}
    return sum(
        int(packages.get(key) or 0)
        for key in (
            "history_success_count",
            "daily_success_count",
        )
    )


def _application_domain(report: dict[str, Any]) -> dict[str, Any]:
    status = str(report.get("status") or "")
    reasons = (
        list(report.get("hard_fail_reasons") or [])
        + list(report.get("not_ready_reasons") or [])
        + list(report.get("warning_reasons") or [])
    )
    package_count = _application_package_count(report)
    if status == "PASS":
        state = "READY"
        queryable = True
        trusted = True
    elif status == "PASS_WITH_WARNINGS":
        state = "UNVERIFIED_OR_WARNINGS"
        queryable = True
        trusted = False
    elif status == "NOT_READY":
        state = "NOT_READY"
        queryable = package_count > 0
        trusted = False
    else:
        state = "FAILED" if status == "FAIL" else "NOT_READY"
        queryable = False
        trusted = False
    completeness = report.get("historical_part_completeness") or {}
    return {
        "domain": "application",
        "state": state,
        "queryable": queryable,
        "trusted": trusted,
        "acceptance_status": status,
        "reason_codes": list(dict.fromkeys(str(value) for value in reasons if value)),
        "successful_package_count": package_count,
        "historical_part_complete": bool(completeness.get("complete")),
        "expected_history_parts": completeness.get("expected_history_parts"),
        "semantics": "USPTO_APPLICATION_FACT_CORPUS_ACCEPTANCE_NOT_LEGAL_STATUS",
    }


def _recorded_fact_domain(name: str, report: dict[str, Any], semantics: str) -> dict[str, Any]:
    state_raw = str(report.get("state") or "")
    reasons = list(report.get("reason_codes") or [])
    ready_flag = bool(report.get("ready"))
    if state_raw == "ACCEPTED" and ready_flag:
        state = "READY"
        queryable = True
        trusted = True
    elif ready_flag:
        state = "READY_WITH_WARNINGS" if reasons else "READY"
        queryable = True
        trusted = not reasons
    elif state_raw == "SOURCE_VERIFICATION_REQUIRED":
        state = "UNVERIFIED"
        queryable = True
        trusted = False
    elif state_raw == "FAILED":
        state = "FAILED"
        queryable = False
        trusted = False
    else:
        state = "NOT_READY"
        queryable = False
        trusted = False
    acceptance = report.get("acceptance") or {}
    return {
        "domain": name,
        "state": state,
        "queryable": queryable,
        "trusted": trusted,
        "readiness_state": state_raw,
        "acceptance_status": str(acceptance.get("status") or ""),
        "reason_codes": reasons,
        "next_action": report.get("next_action"),
        "semantics": semantics,
    }


def _event_role_domain(report: dict[str, Any]) -> dict[str, Any]:
    status = str(report.get("status") or "")
    ruleset = report.get("ruleset") or {}
    roles = report.get("roles") or {}
    ready = status == "PASS"
    return {
        "domain": "reviewed_event_roles",
        "state": "READY" if ready else "NOT_READY",
        "queryable": ready,
        "trusted": ready,
        "status": status,
        "reason_codes": [str(report.get("reason"))] if report.get("reason") else [],
        "ruleset_version": ruleset.get("ruleset_version"),
        "role_count": len(roles),
        "semantics": "EVIDENCE_BOUND_REVIEWED_EVENT_ROLE_MAPPING_NOT_USPTO_RAW_FACT",
    }


def _failed_domain(name: str, exc: Exception, semantics: str) -> dict[str, Any]:
    return {
        "domain": name,
        "state": "FAILED",
        "queryable": False,
        "trusted": False,
        "reason_codes": [f"{type(exc).__name__}:{exc}"],
        "error_type": type(exc).__name__,
        "error": str(exc),
        "semantics": semantics,
    }


def _safe_domain(
    name: str,
    semantics: str,
    loader: Callable[[], dict[str, Any]],
    normalizer: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    try:
        return normalizer(loader())
    except Exception as exc:
        return _failed_domain(name, exc, semantics)


def _feed(
    *,
    name: str,
    queryable: bool,
    trusted: bool,
    state: str | None = None,
    reason_codes: list[str] | None = None,
    capabilities: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if state is None:
        if trusted:
            state = "READY"
        elif queryable:
            state = "UNVERIFIED"
        else:
            state = "NOT_READY"
    return {
        "feed": name,
        "state": state,
        "queryable": queryable,
        "trusted_for_silence": trusted,
        "silence_semantics": SILENCE_TRUSTED if trusted else SILENCE_UNTRUSTED,
        "reason_codes": list(dict.fromkeys(reason_codes or [])),
        "capabilities": capabilities or {},
    }


def evaluate_monitoring_readiness(
    *,
    application: dict[str, Any],
    assignment: dict[str, Any],
    ttab: dict[str, Any],
    event_roles: dict[str, Any],
    expected_history_parts: int | None,
    verify_sources: bool,
) -> dict[str, Any]:
    app_queryable = bool(application.get("queryable"))
    app_trusted = bool(application.get("trusted"))
    role_ready = bool(event_roles.get("trusted"))

    feeds = {
        "case_changes": _feed(
            name="case_changes",
            queryable=app_queryable,
            trusted=app_trusted,
            reason_codes=list(application.get("reason_codes") or []),
        ),
        "assignments": _feed(
            name="assignments",
            queryable=bool(assignment.get("queryable")),
            trusted=bool(assignment.get("trusted")),
            state=str(assignment.get("state") or "NOT_READY"),
            reason_codes=list(assignment.get("reason_codes") or []),
        ),
        "ttab": _feed(
            name="ttab",
            queryable=bool(ttab.get("queryable")),
            trusted=bool(ttab.get("trusted")),
            state=str(ttab.get("state") or "NOT_READY"),
            reason_codes=list(ttab.get("reason_codes") or []),
        ),
        "reviewed_events": _feed(
            name="reviewed_events",
            queryable=app_queryable and role_ready,
            trusted=app_trusted and role_ready,
            reason_codes=(
                list(application.get("reason_codes") or [])
                + list(event_roles.get("reason_codes") or [])
            ),
        ),
    }

    if not app_queryable:
        deadline_state = "NOT_READY"
    elif not app_trusted:
        deadline_state = "UNVERIFIED"
    elif role_ready:
        deadline_state = "READY"
    else:
        deadline_state = "PARTIAL"
    feeds["deadlines"] = _feed(
        name="deadlines",
        queryable=app_queryable,
        trusted=app_trusted and role_ready,
        state=deadline_state,
        reason_codes=(
            list(application.get("reason_codes") or [])
            + ([] if role_ready else list(event_roles.get("reason_codes") or []))
        ),
        capabilities={
            "maintenance": {
                "available": app_queryable,
                "trusted": app_trusted,
            },
            "publication": {
                "available": app_queryable,
                "trusted": app_trusted,
            },
            "reviewed_oa_noa": {
                "available": app_queryable and role_ready,
                "trusted": app_trusted and role_ready,
            },
        },
    )

    trusted_count = sum(1 for feed in feeds.values() if feed["trusted_for_silence"])
    queryable_count = sum(1 for feed in feeds.values() if feed["queryable"])
    failed_domains = [
        name
        for name, domain in {
            "application": application,
            "assignment": assignment,
            "ttab": ttab,
            "reviewed_event_roles": event_roles,
        }.items()
        if domain.get("state") == "FAILED"
    ]
    if trusted_count == len(feeds):
        overall = "READY"
    elif trusted_count > 0:
        overall = "PARTIAL"
    elif queryable_count > 0:
        overall = "UNVERIFIED"
    elif failed_domains:
        overall = "FAILED"
    else:
        overall = "NOT_READY"

    return {
        "readiness_version": MONITORING_READINESS_VERSION,
        "state": overall,
        "ready": overall == "READY",
        "semantics": MONITORING_SEMANTICS,
        "expected_history_parts": expected_history_parts,
        "verify_sources": verify_sources,
        "coverage": {
            "feed_count": len(feeds),
            "queryable_feed_count": queryable_count,
            "trusted_feed_count": trusted_count,
            "all_feed_silence_trusted": trusted_count == len(feeds),
            "failed_domains": failed_domains,
        },
        "domains": {
            "application": application,
            "assignment": assignment,
            "ttab": ttab,
            "reviewed_event_roles": event_roles,
        },
        "feeds": feeds,
        "zero_alert_interpretation": (
            "A zero-event page is evidence only that no matching normalized events were observed "
            "within that feed's scanned cursor/range. It is never a legal conclusion. When "
            "trusted_for_silence=false, zero events must not be interpreted as source-complete silence."
        ),
        "legal_status_inference": False,
        "legal_ownership_conclusion": False,
        "ttab_outcome_conclusion": False,
        "substantive_rights_conclusion": False,
    }


def build_monitoring_readiness(
    *,
    raw_root: Path,
    expected_history_parts: int | None = None,
    verify_sources: bool = False,
) -> dict[str, Any]:
    if expected_history_parts is not None and expected_history_parts < 1:
        raise ValueError("expected_history_parts must be at least 1 when provided")

    application = _safe_domain(
        "application",
        "USPTO_APPLICATION_FACT_CORPUS_ACCEPTANCE_NOT_LEGAL_STATUS",
        lambda: build_application_acceptance(
            verify_source_files=verify_sources,
            expected_history_parts=expected_history_parts,
        ),
        _application_domain,
    )
    assignment = _safe_domain(
        "assignment",
        "USPTO_RECORDED_ASSIGNMENT_FACTS_NOT_LEGAL_TITLE_CONCLUSION",
        lambda: build_assignment_readiness(
            raw_root=raw_root,
            verify_sources=verify_sources,
        ),
        lambda report: _recorded_fact_domain(
            "assignment",
            report,
            "USPTO_RECORDED_ASSIGNMENT_FACTS_NOT_LEGAL_TITLE_CONCLUSION",
        ),
    )
    ttab = _safe_domain(
        "ttab",
        "USPTO_TTAB_PROCEDURAL_FACTS_NOT_OUTCOME_OR_SUBSTANTIVE_RIGHTS_CONCLUSION",
        lambda: build_ttab_readiness(
            raw_root=raw_root,
            verify_sources=verify_sources,
        ),
        lambda report: _recorded_fact_domain(
            "ttab",
            report,
            "USPTO_TTAB_PROCEDURAL_FACTS_NOT_OUTCOME_OR_SUBSTANTIVE_RIGHTS_CONCLUSION",
        ),
    )
    event_roles = _safe_domain(
        "reviewed_event_roles",
        "EVIDENCE_BOUND_REVIEWED_EVENT_ROLE_MAPPING_NOT_USPTO_RAW_FACT",
        lambda: load_active_event_role_map(raw_root),
        _event_role_domain,
    )
    return evaluate_monitoring_readiness(
        application=application,
        assignment=assignment,
        ttab=ttab,
        event_roles=event_roles,
        expected_history_parts=expected_history_parts,
        verify_sources=verify_sources,
    )
