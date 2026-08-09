import pytest
from fastapi import HTTPException

import app.main as main


def test_us_case_rejects_non_eight_digit_serial() -> None:
    with pytest.raises(HTTPException) as exc_info:
        main.us_case("97ABC456")
    assert exc_info.value.status_code == 400


def test_us_summary_labels_status_as_raw_official(monkeypatch) -> None:
    monkeypatch.setattr(main, "_ensure_us_api_ready", lambda: None)
    responses = iter(
        [
            [{"table_name": "us_case_current", "row_count": 2}],
            [{"status_code": "700", "case_count": 1}],
            [{"use_1a_cases": 1, "madrid_66a_cases": 1}],
        ]
    )
    monkeypatch.setattr(main, "_query_dicts", lambda _sql: next(responses))

    result = main.us_summary()
    assert result["us_model_version"] == "US_M1.3"
    assert result["status_semantics"] == "OFFICIAL_RAW_NOT_LEGAL_INTERPRETATION"
    assert result["tables"][0]["row_count"] == 2
    assert result["status_codes"][0]["status_code"] == "700"


def test_us_case_returns_all_official_fact_families(monkeypatch) -> None:
    monkeypatch.setattr(main, "_ensure_us_api_ready", lambda: None)
    responses = iter(
        [
            [{"serial_number": "97123456", "status_code": "700"}],
            [{"party_name": "Owner LLC"}],
            [{"primary_code": "009"}],
            [{"event_code": "NWAP"}],
            [{"type_code": "GS0091"}],
        ]
    )
    monkeypatch.setattr(main, "_query_dicts", lambda _sql: next(responses))

    result = main.us_case("97123456")
    assert result["model_version"] == "US_M1.3"
    assert result["case"]["serial_number"] == "97123456"
    assert result["owners"][0]["party_name"] == "Owner LLC"
    assert result["classifications"][0]["primary_code"] == "009"
    assert result["events"][0]["event_code"] == "NWAP"
    assert result["statements"][0]["type_code"] == "GS0091"


def test_us_case_not_found_is_404(monkeypatch) -> None:
    monkeypatch.setattr(main, "_ensure_us_api_ready", lambda: None)
    monkeypatch.setattr(main, "_query_dicts", lambda _sql: [])
    with pytest.raises(HTTPException) as exc_info:
        main.us_case("97123456")
    assert exc_info.value.status_code == 404
