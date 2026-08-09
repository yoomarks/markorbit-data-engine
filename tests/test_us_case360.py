from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import app.main as main
from app.us import case360


def _application() -> dict:
    return {
        "case": {
            "serial_number": "90000001",
            "registration_number": "7000001",
            "registration_date": date(2024, 1, 15),
            "status_code": "700",
            "madrid_66a": False,
            "madrid_66a_current": False,
        },
        "owners": [{"party_name": "Example Owner LLC"}],
        "classifications": [],
        "events": [],
        "statements": [],
        "correspondent": None,
        "design_searches": [],
        "prior_registrations": [],
        "foreign_applications": [],
        "madrid_filings": [],
        "madrid_events": [],
    }


def _patch_domains(monkeypatch) -> None:
    monkeypatch.setattr(case360, "_application_snapshot", lambda serial: _application())
    monkeypatch.setattr(
        case360,
        "build_case_timeline",
        lambda serial, limit: {
            "serial_number": serial,
            "observation_count": 2,
            "observations": [{"source_rank": 1}, {"source_rank": 2}],
            "changes": [{"change_type": "STATUS_CHANGED"}],
        },
    )
    monkeypatch.setattr(
        case360,
        "_assignment_snapshot",
        lambda serial, limit: {
            "assignment_count": 1,
            "records": [{"reel_frame_id": "1/2"}],
            "owner_name_reconciliation": {
                "comparison": "MATCH",
                "legal_ownership_conclusion": False,
            },
        },
    )
    monkeypatch.setattr(
        case360,
        "proceedings_for_serial",
        lambda serial, limit: [{"proceeding_number": "91234567"}],
    )
    monkeypatch.setattr(
        case360,
        "resolve_case_deadline_evidence",
        lambda serial_number, raw_root: {
            "status": "READY",
            "serial_number": serial_number,
            "automatic_inputs": {},
        },
    )
    monkeypatch.setattr(
        case360,
        "_maintenance_snapshot",
        lambda application, as_of: {
            "rule_version": "fixture",
            "as_of": as_of,
            "semantics": case360.MAINTENANCE_SEMANTICS,
        },
    )
    monkeypatch.setattr(
        case360,
        "get_settings",
        lambda: SimpleNamespace(raw_data_root="/tmp/markorbit-case360-test"),
    )


def test_case_360_routes_are_read_only_and_mounted() -> None:
    expected = {
        "/api/us/case-360/schema",
        "/api/us/cases/{serial_number}/360",
    }
    routes = {route.path: route.methods for route in main.app.routes if route.path in expected}
    assert set(routes) == expected
    assert all(methods == {"GET"} for methods in routes.values())


def test_case_360_schema_freezes_source_boundaries() -> None:
    schema = case360.case_360_schema()
    assert schema["view_version"] == "US_CASE_360_M1.0"
    assert schema["source_boundary_preserved"] is True
    assert schema["partial_domain_failure_isolated"] is True
    assert schema["legal_status_inference"] is False
    assert schema["legal_ownership_conclusion"] is False
    assert schema["ttab_outcome_conclusion"] is False
    assert schema["substantive_rights_conclusion"] is False
    assert "uspto_recorded_assignments" in schema["source_domains"]
    assert "ttab_procedural_facts" in schema["source_domains"]


def test_case_360_composes_existing_domains_without_collapsing_semantics(monkeypatch) -> None:
    _patch_domains(monkeypatch)
    report = case360.build_case_360(
        "90000001",
        as_of=date(2026, 8, 9),
        history_limit=20,
        assignment_limit=10,
        ttab_limit=10,
    )
    assert report is not None
    assert report["view_version"] == "US_CASE_360_M1.0"
    assert report["serial_number"] == "90000001"
    assert set(report["domains"]) == {
        "application",
        "change_history",
        "assignment",
        "ttab",
        "deadline_evidence",
        "maintenance",
    }
    assert all(status == "AVAILABLE" for status in report["coverage"].values())
    assert report["domains"]["application"]["data"]["case"]["status_code"] == "700"
    assert report["domains"]["assignment"]["data"]["assignment_count"] == 1
    assert report["domains"]["ttab"]["data"]["proceedings"][0]["proceeding_number"] == "91234567"
    assert report["legal_status_inference"] is False
    assert report["legal_ownership_conclusion"] is False
    assert report["ttab_outcome_conclusion"] is False
    assert report["substantive_rights_conclusion"] is False


def test_case_360_isolates_auxiliary_domain_failure(monkeypatch) -> None:
    _patch_domains(monkeypatch)

    def fail_ttab(serial: str, limit: int):
        raise RuntimeError("TTAB fixture unavailable")

    monkeypatch.setattr(case360, "proceedings_for_serial", fail_ttab)
    report = case360.build_case_360("90000001", as_of=date(2026, 8, 9))
    assert report is not None
    assert report["coverage"]["application"] == "AVAILABLE"
    assert report["coverage"]["assignment"] == "AVAILABLE"
    assert report["coverage"]["ttab"] == "NOT_AVAILABLE"
    assert report["domains"]["ttab"]["error_type"] == "RuntimeError"
    assert len(report["warnings"]) == 1
    assert "ttab" in report["warnings"][0]


def test_case_360_requires_existing_application_case(monkeypatch) -> None:
    monkeypatch.setattr(case360, "_application_snapshot", lambda serial: None)
    assert case360.build_case_360("90000001") is None


def test_case_360_rejects_invalid_serial_and_limits() -> None:
    try:
        case360.build_case_360("123")
    except ValueError as exc:
        assert "exactly 8 digits" in str(exc)
    else:
        raise AssertionError("invalid serial must be rejected")

    try:
        case360.build_case_360("90000001", history_limit=0)
    except ValueError as exc:
        assert "history_limit" in str(exc)
    else:
        raise AssertionError("invalid history_limit must be rejected")
