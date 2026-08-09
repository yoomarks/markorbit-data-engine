from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import app.main as main
from app.us import alert_engine


def _assert_safe(event: dict) -> None:
    assert len(event["event_id"]) == 64
    assert event["actionability"] == "REVIEW_REQUIRED"
    assert event["delivery_semantics"] == alert_engine.DELIVERY_SEMANTICS
    assert event["legal_status_inference"] is False
    assert event["legal_ownership_conclusion"] is False
    assert event["ttab_outcome_conclusion"] is False
    assert event["substantive_rights_conclusion"] is False


def test_alert_routes_are_read_only_and_mounted() -> None:
    expected = {
        "/api/us/alerts/schema",
        "/api/us/alerts/case-changes",
        "/api/us/alerts/assignments",
        "/api/us/alerts/ttab",
        "/api/us/alerts/reviewed-events",
        "/api/us/alerts/deadlines",
    }
    routes = {route.path: route.methods for route in main.app.routes if route.path in expected}
    assert set(routes) == expected
    assert all(methods == {"GET"} for methods in routes.values())


def test_alert_schema_freezes_domain_scoped_cursor_contract() -> None:
    schema = alert_engine.alert_engine_schema()
    assert schema["version"] == "US_ALERT_ENGINE_M1.0"
    assert schema["global_source_rank_ordering"] is False
    assert schema["source_boundary_preserved"] is True
    assert schema["consumer_dedupe_key"] == "event_id"
    assert schema["subscription_storage_included"] is False
    assert schema["webhook_delivery_included"] is False
    assert schema["feeds"]["assignments"]["cursor"] == [
        "source_rank",
        "reel_frame_id",
        "source_package_id",
    ]
    assert schema["feeds"]["ttab"]["cursor"] == [
        "source_rank",
        "proceeding_number",
        "source_package_id",
    ]
    assert schema["feeds"]["deadlines"]["mode"] == "SNAPSHOT_CANDIDATE_SCAN"


def test_case_change_normalization_splits_subscription_event_types() -> None:
    page = {
        "after_source_rank": 10,
        "after_serial": "90000000",
        "next_cursor": {"source_rank": 11, "serial_number": "90000001"},
        "has_more_observations": False,
        "semantics": "fixture",
        "changes": [
            {
                "change_id": "c" * 64,
                "serial_number": "90000001",
                "source_rank": 11,
                "source_package_id": "00000000-0000-0000-0000-000000000011",
                "source_effective_date": date(2026, 8, 9),
                "source_file": "daily.zip",
                "change_types": ["STATUS_CODE_CHANGED", "OWNER_IDENTITY_SET_CHANGED"],
                "field_changes": {
                    "status_code": {"before": "640", "after": "700"},
                    "owners": {"before": ["Alpha LLC"], "after": ["Beta LLC"]},
                },
                "semantics": "observed-change",
            }
        ],
    }
    report = alert_engine.normalize_case_change_page(page)
    assert report["event_count"] == 2
    assert {event["event_type"] for event in report["events"]} == {
        "CASE_OWNER_CHANGED",
        "CASE_STATUS_CHANGED",
    }
    assert len({event["event_id"] for event in report["events"]}) == 2
    for event in report["events"]:
        _assert_safe(event)


def test_assignment_feed_emits_first_recorded_observation_per_linked_serial(monkeypatch) -> None:
    record = {
        "reel_frame_id": "1234/5678",
        "reel_no": "1234",
        "frame_no": "5678",
        "recorded_date": date(2026, 8, 8),
        "recorded_date_raw": "2026-08-08",
        "conveyance_text": "ASSIGNS THE ENTIRE INTEREST",
        "source_kind": "DAILY",
        "source_effective_date": date(2026, 8, 9),
        "source_file": "assignment.xml",
        "source_package_id": "00000000-0000-0000-0000-000000000123",
        "source_rank": 123,
        "observed_at": datetime(2026, 8, 9, tzinfo=timezone.utc),
    }
    monkeypatch.setattr(
        alert_engine,
        "_assignment_first_rows",
        lambda **kwargs: [record],
    )
    monkeypatch.setattr(
        alert_engine,
        "_assignment_children",
        lambda records: (
            {
                "1234/5678|00000000-0000-0000-0000-000000000123": [
                    {
                        "serial_number": "90000001",
                        "registration_number": "7000001",
                    },
                    {
                        "serial_number": "90000002",
                        "registration_number": "7000002",
                    },
                ]
            },
            {
                "1234/5678|00000000-0000-0000-0000-000000000123": [
                    "New Owner LLC"
                ]
            },
        ),
    )
    report = alert_engine.scan_assignment_alerts(scan_limit=10)
    assert report["record_count"] == 1
    assert report["event_count"] == 2
    assert {event["serial_number"] for event in report["events"]} == {
        "90000001",
        "90000002",
    }
    assert all(event["event_type"] == "NEW_RECORDED_ASSIGNMENT" for event in report["events"])
    assert report["next_cursor"]["reel_frame_id"] == "1234/5678"
    for event in report["events"]:
        assert event["payload"]["assignee_names"] == ["New Owner LLC"]
        _assert_safe(event)


def test_ttab_feed_emits_new_proceeding_per_linked_serial(monkeypatch) -> None:
    record = {
        "proceeding_number": "91234567",
        "proceeding_type": "Opposition",
        "proceeding_type_code": "OPP",
        "filing_date": date(2026, 8, 8),
        "filing_date_raw": "08/08/2026",
        "status_text": "Pending",
        "status_code": "9",
        "status_date": date(2026, 8, 8),
        "source_kind": "TTABVUE_PROCEEDING_RAWXML_SNAPSHOT",
        "source_snapshot_at": datetime(2026, 8, 9, tzinfo=timezone.utc),
        "source_file": "ttab.xml",
        "source_package_id": "00000000-0000-0000-0000-000000000456",
        "source_rank": 456,
    }
    monkeypatch.setattr(alert_engine, "_ttab_first_rows", lambda **kwargs: [record])
    monkeypatch.setattr(
        alert_engine,
        "_ttab_properties",
        lambda records: {
            "91234567|00000000-0000-0000-0000-000000000456": [
                {"serial_number": "90000001", "registration_number": "7000001"}
            ]
        },
    )
    report = alert_engine.scan_ttab_alerts(scan_limit=10)
    assert report["event_count"] == 1
    event = report["events"][0]
    assert event["event_type"] == "TTAB_NEW_PROCEEDING"
    assert event["serial_number"] == "90000001"
    assert event["proceeding_number"] == "91234567"
    assert event["payload"]["proceeding_type_code"] == "OPP"
    _assert_safe(event)


def test_reviewed_event_feed_refuses_unreviewed_code_inference(monkeypatch) -> None:
    monkeypatch.setattr(
        alert_engine,
        "load_active_event_role_map",
        lambda raw_root: {
            "status": "NOT_READY",
            "reason": "active_event_role_ruleset_missing",
            "roles": {},
        },
    )
    called = False

    def should_not_query(**kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(alert_engine, "_reviewed_event_rows", should_not_query)
    report = alert_engine.scan_reviewed_event_alerts(raw_root=Path("/tmp"))
    assert report["event_count"] == 0
    assert report["event_role_state"]["status"] == "NOT_READY"
    assert called is False


def test_reviewed_event_feed_uses_evidence_bound_role(monkeypatch) -> None:
    monkeypatch.setattr(
        alert_engine,
        "load_active_event_role_map",
        lambda raw_root: {
            "status": "PASS",
            "reason": None,
            "ruleset": {"ruleset_version": "fixture-v1"},
            "roles": {
                "OA123": {
                    "role": "OFFICE_ACTION_NONFINAL_ISSUED",
                    "rule_id": "fixture-rule",
                    "rationale": "fixture reviewed mapping",
                    "source_refs": ["fixture evidence"],
                }
            },
        },
    )
    monkeypatch.setattr(
        alert_engine,
        "_reviewed_event_rows",
        lambda **kwargs: [
            {
                "event_key": "a" * 64,
                "serial_number": "90000001",
                "event_code": "OA123",
                "event_date": date(2026, 8, 8),
                "event_sequence": 1,
                "event_type_code": "OA",
                "description_text": "Nonfinal office action",
                "source_package_kind": "DAILY",
                "source_effective_date": date(2026, 8, 9),
                "source_file": "daily.zip",
                "source_package_id": "00000000-0000-0000-0000-000000000789",
                "source_rank": 789,
                "observed_at": datetime(2026, 8, 9, tzinfo=timezone.utc),
            }
        ],
    )
    report = alert_engine.scan_reviewed_event_alerts(raw_root=Path("/tmp"), scan_limit=10)
    assert report["event_count"] == 1
    event = report["events"][0]
    assert event["event_type"] == "OA_NONFINAL_ISSUED"
    assert event["payload"]["reviewed_role"] == "OFFICE_ACTION_NONFINAL_ISSUED"
    assert event["payload"]["ruleset_version"] == "fixture-v1"
    _assert_safe(event)


def test_deadline_candidate_normalization_is_snapshot_and_stable() -> None:
    page = {
        "as_of": date(2026, 8, 9),
        "after_serial": "",
        "last_scanned_serial": "90000001",
        "has_more_cases": False,
        "event_role_state": {"status": "PASS"},
        "semantics": "candidate-fixture",
        "candidates": [
            {
                "candidate_id": "90000001:APPLICATION:NONFINAL_OFFICE_ACTION_RESPONSE:2026-08-20",
                "serial_number": "90000001",
                "registration_number": "",
                "family": "APPLICATION",
                "code": "NONFINAL_OFFICE_ACTION_RESPONSE",
                "label": "Office Action response candidate",
                "due_date": date(2026, 8, 20),
                "urgency": "DUE_WITHIN_30_DAYS",
                "state": "OPEN",
                "source": "REVIEWED_EVENT_ROLE_PLUS_VERSIONED_OA_RULE",
                "details": {"issue_date": date(2026, 5, 20)},
            },
            {
                "candidate_id": "90000001:MAINTENANCE:SECTION_8:2026-09-01",
                "serial_number": "90000001",
                "registration_number": "7000001",
                "family": "MAINTENANCE",
                "code": "SECTION_8",
                "label": "Section 8 filing window",
                "due_date": date(2026, 9, 1),
                "urgency": "DUE_WITHIN_30_DAYS",
                "state": "OPEN_REGULAR",
                "source": "OFFICIAL_CASE_REGISTRATION_DATE_PLUS_VERSIONED_RULE",
                "details": {},
            },
        ],
    }
    first = alert_engine.normalize_deadline_page(page)
    second = alert_engine.normalize_deadline_page(page)
    assert first["mode"] == "SNAPSHOT_CANDIDATE_SCAN"
    assert [event["event_id"] for event in first["events"]] == [
        event["event_id"] for event in second["events"]
    ]
    assert {event["event_type"] for event in first["events"]} == {
        "OA_DEADLINE_CANDIDATE",
        "MAINTENANCE_WINDOW",
    }
    for event in first["events"]:
        _assert_safe(event)


def test_cursor_validation_rejects_unsafe_or_invalid_values() -> None:
    try:
        alert_engine._cursor_text("bad'cursor", "cursor")
    except ValueError as exc:
        assert "unsupported" in str(exc)
    else:
        raise AssertionError("unsafe cursor must be rejected")

    try:
        alert_engine._cursor_uuid("not-a-uuid", "package")
    except ValueError as exc:
        assert "UUID" in str(exc)
    else:
        raise AssertionError("invalid UUID cursor must be rejected")
