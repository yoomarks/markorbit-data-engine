from datetime import date

import pytest
from fastapi import HTTPException

import app.us.semantic_api as api


def test_strict_serial_requires_eight_digits() -> None:
    assert api._strict_serial("97123456") == "97123456"
    with pytest.raises(HTTPException) as exc_info:
        api._strict_serial("97ABC456")
    assert exc_info.value.status_code == 400


def test_focused_status_reference_lookup_is_explicit_about_unmapped(monkeypatch) -> None:
    monkeypatch.setattr(
        api,
        "lookup_active_status_codes",
        lambda _codes: {
            "reference": {"reference_version": "STATUS_V1"},
            "mappings": {},
        },
    )
    result = api.us_status_reference_lookup("999")
    assert result["status_code"] == "999"
    assert result["mapped"] is False
    assert result["official_reference"] is None
    with pytest.raises(HTTPException) as exc_info:
        api.us_status_reference_lookup("9A9")
    assert exc_info.value.status_code == 400


def test_focused_event_reference_lookup_normalizes_uppercase(monkeypatch) -> None:
    monkeypatch.setattr(
        api,
        "lookup_active_event_codes",
        lambda codes: {
            "reference": {"reference_version": "EVENT_V1"},
            "mappings": {
                "NEWAP": {"official_description": "Fixture event"}
                if codes == ["NEWAP"]
                else None
            },
        },
    )
    result = api.us_event_reference_lookup("newap")
    assert result["event_code"] == "NEWAP"
    assert result["mapped"] is True
    with pytest.raises(HTTPException) as exc_info:
        api.us_event_reference_lookup("bad code")
    assert exc_info.value.status_code == 400


def test_maintenance_rule_metadata_exposes_versioned_evidence_only() -> None:
    result = api.us_maintenance_rule_metadata()
    assert result["rule_version"].startswith("US_MAINTENANCE_")
    assert result["production_legal_status_inference"] is False
    assert result["evidence_refs"]
    assert result["business_day_adjustment"] == "NOT_CALCULATED_CHECK_USPTO"


def test_maintenance_endpoint_uses_case_facts_without_inferring_status(monkeypatch) -> None:
    monkeypatch.setattr(
        api,
        "_case_semantic_facts",
        lambda _serial: {
            "registration_number": "6111140",
            "registration_date": date(2020, 7, 28),
            "madrid_66a": 0,
            "madrid_66a_current": 0,
            "international_registration_date": None,
            "renewal_date": None,
            "section_8_filed": 0,
            "section_8_accepted": 0,
            "section_15_filed": 0,
            "section_15_acknowledged": 0,
        },
    )
    result = api.us_maintenance_schedule("97123456", as_of=date(2026, 8, 9))
    assert result["registration_number"] == "6111140"
    assert result["schedule"]["mode"] == "MODERN_NON_MADRID"
    first = result["schedule"]["obligations"][0]
    assert first["code"] == "SECTION_8_FIRST"
    assert first["state_as_of"] == "OPEN_GRACE"
    assert result["schedule"]["semantics"] == "DEADLINE_CALCULATION_NOT_CASE_LEGAL_STATUS"


def test_maintenance_endpoint_returns_not_ready_without_registration_date(monkeypatch) -> None:
    monkeypatch.setattr(
        api,
        "_case_semantic_facts",
        lambda _serial: {"registration_date": None},
    )
    result = api.us_maintenance_schedule("97123456", as_of=date(2026, 8, 9))
    assert result["status"] == "NOT_READY"
    assert result["reason"] == "registration_date_missing"


def test_status_interpretation_api_keeps_three_layers_separate(monkeypatch) -> None:
    monkeypatch.setattr(
        api,
        "_case_semantic_facts",
        lambda _serial: {"status_code": "700", "status_date": date(2026, 8, 1)},
    )
    monkeypatch.setattr(api, "_case_event_codes", lambda _serial: ["NEWAP"])
    monkeypatch.setattr(
        api,
        "lookup_active_status_codes",
        lambda _codes: {
            "reference": {"reference_version": "STATUS_REF_V1"},
            "mappings": {"700": {"official_description": "Official reference text"}},
        },
    )
    monkeypatch.setattr(
        api,
        "lookup_active_event_codes",
        lambda _codes: {
            "reference": {"reference_version": "EVENT_REF_V1"},
            "mappings": {"NEWAP": {"official_description": "Official event text"}},
        },
    )
    monkeypatch.setattr(
        api,
        "interpret_status",
        lambda **_kwargs: {
            "result": "UNKNOWN",
            "confidence": "LOW",
            "reason": "active_ruleset_missing",
            "legal_interpretation_produced": False,
        },
    )
    result = api.us_status_interpretation("97123456")
    assert result["raw_uspto_fact"]["status_code"] == "700"
    assert result["official_reference"]["status"]["official_description"] == "Official reference text"
    assert result["markorbit_derived_interpretation"]["result"] == "UNKNOWN"
    assert result["markorbit_derived_interpretation"]["legal_interpretation_produced"] is False


def test_semantic_api_router_is_read_only() -> None:
    paths = {route.path: methods for route in api.router.routes for methods in [route.methods]}
    assert paths
    for methods in paths.values():
        assert methods == {"GET"}
