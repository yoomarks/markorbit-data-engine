from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.integration_discovery_api as discovery_api
from app.config import get_settings
from app.discovery_contract import DiscoveryCursorError
from app.integration_g0_contract import g0_contract_descriptor


API_KEY = "integration-discovery-test-key-0000000000000001"
PATH = "/api/v1/cn/discovery/preliminary-publications"


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("INTEGRATION_AUTH_MODE", "required")
    monkeypatch.setenv("INTEGRATION_API_KEYS", API_KEY)
    monkeypatch.setenv("INTEGRATION_RATE_LIMIT_ENABLED", "false")
    get_settings.cache_clear()
    app = FastAPI()
    app.include_router(discovery_api.router)
    return TestClient(app)


def _params(**overrides):
    params = {
        "application_number_start": "10000000",
        "application_number_end": "10001000",
        "page_size": 25,
    }
    params.update(overrides)
    return params


def test_discovery_route_requires_integration_bearer(monkeypatch):
    client = _client(monkeypatch)
    response = client.get(PATH, params=_params())
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "DATA_ENGINE_INTEGRATION_AUTH_REQUIRED"
    get_settings.cache_clear()


def test_discovery_route_delegates_bounded_request(monkeypatch):
    captured = {}
    page = {
        "contract_version": "DATA_ENGINE_DISCOVERY_CONTRACT_V1",
        "items": [{"application_number": "10000001"}],
        "next_cursor": "cursor-v1",
        "provenance": {"snapshot": "epoch-v1"},
    }

    def fake_execute(request):
        captured["request"] = request
        return page

    monkeypatch.setattr(
        discovery_api, "execute_preliminary_publication_discovery", fake_execute
    )
    client = _client(monkeypatch)
    response = client.get(
        PATH,
        params=_params(cursor="opaque-cursor"),
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200
    assert response.json() == page
    request = captured["request"]
    assert request.application_number_start == "10000000"
    assert request.application_number_end == "10001000"
    assert request.page_size == 25
    assert request.cursor == "opaque-cursor"
    get_settings.cache_clear()


def test_discovery_route_rejects_invalid_range(monkeypatch):
    client = _client(monkeypatch)
    response = client.get(
        PATH,
        params=_params(
            application_number_start="10001000",
            application_number_end="10000000",
        ),
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "DATA_ENGINE_DISCOVERY_QUERY_INVALID"
    get_settings.cache_clear()


def test_discovery_route_maps_cursor_context_mismatch_to_conflict(monkeypatch):
    def fail(_request):
        raise DiscoveryCursorError("cursor/query mismatch")

    monkeypatch.setattr(discovery_api, "execute_preliminary_publication_discovery", fail)
    client = _client(monkeypatch)
    response = client.get(
        PATH,
        params=_params(cursor="opaque-cursor"),
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "DATA_ENGINE_DISCOVERY_CURSOR_CONFLICT",
        "message": "cursor/query mismatch",
        "retryable": False,
    }
    get_settings.cache_clear()


def test_g0_contract_exposes_bounded_discovery_resource():
    resources = g0_contract_descriptor()["query_contract"]["resources"]
    resource = next(
        item
        for item in resources
        if item["path"] == "/api/v1/cn/discovery/preliminary-publications"
    )
    assert resource["pagination"] == "bounded_keyset_cursor"
    assert resource["snapshot"] == "CN_QUIESCENT_SERVING_EPOCH"
    assert resource["hard_bounds"] == {"max_pages": 10, "max_results": 1000}
    assert resource["read_budget"]["max_rows_to_read"] == 250000
    assert resource["read_budget"]["max_bytes_to_read"] == 268435456
    assert resource["business_state_owned_outside_data_engine"] is True
