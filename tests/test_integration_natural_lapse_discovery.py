from datetime import date

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.integration_api as integration_api
from app.us.natural_lapse_discovery import ADMITTED_REASON, NaturalLapseUnavailable


def request(query: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/us/discovery/natural-lapses",
            "headers": [],
            "query_string": query.encode("ascii"),
        }
    )


def call_route(*, req: Request, **overrides):
    values = {
        "request": req,
        "lapse_reason": ADMITTED_REASON,
        "event_date_start": date(2026, 1, 1),
        "event_date_end": date(2026, 2, 1),
        "nice_class": None,
        "serial_number_start": None,
        "serial_number_end": None,
        "page_size": 50,
        "cursor": None,
    }
    values.update(overrides)
    return integration_api.integration_us_natural_lapse_discovery(**values)


def test_route_is_get_only_and_delegates_to_accepted_us_target(monkeypatch):
    captured = {}
    sentinel = object()

    def fake_execute(query, *, client):
        captured["query"] = query
        captured["client"] = client
        return {"result_state": "RESULTS", "results": [{"legal_conclusion": False}]}

    monkeypatch.setattr(integration_api, "accepted_us_target_read_client", lambda: sentinel)
    monkeypatch.setattr(integration_api, "execute_us_natural_lapse_page", fake_execute)
    body = call_route(
        req=request(
            "lapse_reason=REGISTRATION_MAINTENANCE_LAPSE&event_date_start=2026-01-01&"
            "event_date_end=2026-02-01&nice_class=9&serial_number_start=90000000&"
            "serial_number_end=91000000&page_size=25"
        ),
        nice_class=9,
        serial_number_start="90000000",
        serial_number_end="91000000",
        page_size=25,
    )
    route = next(
        item
        for item in integration_api.router.routes
        if item.path == "/api/v1/us/discovery/natural-lapses"
    )
    assert set(route.methods or ()) == {"GET"}
    assert captured["client"] is sentinel
    assert captured["query"].nice_class == 9
    assert captured["query"].serial_number_start == "90000000"
    assert body["resource_kind"] == "NATURAL_LAPSE_SOURCE_FACT_DISCOVERY"
    assert body["legal_conclusion"] is False
    assert body["payload"]["result_state"] == "RESULTS"


def test_route_rejects_fuzzy_or_prefix_escape_hatches():
    with pytest.raises(HTTPException) as caught:
        call_route(
            req=request(
                "lapse_reason=REGISTRATION_MAINTENANCE_LAPSE&event_date_start=2026-01-01&"
                "event_date_end=2026-02-01&fuzzy=true"
            )
        )
    assert caught.value.status_code == 400
    assert caught.value.detail["fields"] == ["fuzzy"]


def test_route_maps_projection_unavailable_to_503(monkeypatch):
    monkeypatch.setattr(integration_api, "accepted_us_target_read_client", lambda: object())

    def unavailable(*_args, **_kwargs):
        raise NaturalLapseUnavailable("projection state missing")

    monkeypatch.setattr(integration_api, "execute_us_natural_lapse_page", unavailable)
    with pytest.raises(HTTPException) as caught:
        call_route(
            req=request(
                "lapse_reason=REGISTRATION_MAINTENANCE_LAPSE&event_date_start=2026-01-01&"
                "event_date_end=2026-02-01"
            )
        )
    assert caught.value.status_code == 503
    assert caught.value.detail["result_state"] == "UNAVAILABLE"
    assert caught.value.detail["retryable"] is True


def test_g0_contract_declares_frozen_bounded_natural_lapse_resource():
    resources = integration_api.g0_contract_descriptor()["query_contract"]["resources"]
    item = next(
        entry for entry in resources if entry["path"] == "/api/v1/us/discovery/natural-lapses"
    )
    assert item["hard_bounds"] == {"max_pages": 10, "max_results": 1000}
    assert item["query"]["lapse_reason"]["required"] is True
    assert item["query"]["nice_class"]["required"] is False
    assert item["query"]["serial_number_start"]["required"] is False
    assert item["query"]["event_date_end"]["max_span_days"] == 366
    assert item["source_event_mapping"]["event_code"] == "CAEX"
    assert "prefix" in item["unsupported_v1"]
    assert item["result_states"][-1] == "UNAVAILABLE"
