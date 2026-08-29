from __future__ import annotations

import pytest
from fastapi import HTTPException

import app.integration_api as integration_api
from app.discovery_contract import DiscoveryCursorError
from app.integration_g0_contract import g0_contract_descriptor
from app.integration_runtime import enforce_integration_rate_limit
from app.integration_security import require_integration_auth


PATH = "/api/v1/cn/discovery/preliminary-publications"


def test_integration_router_keeps_auth_and_rate_limit_dependencies():
    dependencies = {item.dependency for item in integration_api.router.dependencies}
    assert require_integration_auth in dependencies
    assert enforce_integration_rate_limit in dependencies


def test_discovery_route_delegates_bounded_request_and_wraps_fact_envelope(monkeypatch):
    captured = {}
    page = {
        "stream_id": "CN_PRELIMINARY_PUBLICATION_FACT_DISCOVERY_V2",
        "results": [{"application_number": "10000001"}],
        "next_cursor": "cursor-v1",
        "provenance": {"snapshot": "epoch-v1"},
    }

    def fake_execute(request, *, client):
        captured["request"] = request
        captured["client"] = client
        return page

    sentinel_client = object()
    monkeypatch.setattr(integration_api, "execute_page", fake_execute)
    monkeypatch.setattr(integration_api, "clickhouse_client", lambda: sentinel_client)
    monkeypatch.setattr(integration_api, "engine_version", lambda: "M1.7-test")

    body = integration_api.integration_cn_preliminary_publication_discovery(
        application_number_start="10000000",
        application_number_end="10001000",
        page_size=25,
        cursor="opaque-cursor",
    )

    assert body["jurisdiction"] == "CN"
    assert body["resource_kind"] == "PRELIMINARY_PUBLICATION_FACT_DISCOVERY"
    assert body["authority"] == "DATA_ENGINE_FACT_READ_MODEL"
    assert body["legal_conclusion"] is False
    assert body["fact_state"] == "observed"
    assert body["payload"] == page
    request = captured["request"]
    assert request.application_number_start == "10000000"
    assert request.application_number_end == "10001000"
    assert request.page_size == 25
    assert request.cursor == "opaque-cursor"
    assert captured["client"] is sentinel_client


def test_discovery_route_rejects_invalid_range():
    with pytest.raises(HTTPException) as caught:
        integration_api.integration_cn_preliminary_publication_discovery(
            application_number_start="10001000",
            application_number_end="10000000",
            page_size=25,
            cursor=None,
        )
    assert caught.value.status_code == 400
    assert caught.value.detail["code"] == "DATA_ENGINE_DISCOVERY_QUERY_INVALID"


def test_discovery_route_maps_cursor_context_mismatch_to_conflict(monkeypatch):
    def fail(_request, *, client):
        del client
        raise DiscoveryCursorError("cursor/query mismatch")

    monkeypatch.setattr(integration_api, "execute_page", fail)
    monkeypatch.setattr(integration_api, "clickhouse_client", object)
    with pytest.raises(HTTPException) as caught:
        integration_api.integration_cn_preliminary_publication_discovery(
            application_number_start="10000000",
            application_number_end="10001000",
            page_size=25,
            cursor="opaque-cursor",
        )
    assert caught.value.status_code == 409
    assert caught.value.detail == {
        "code": "DATA_ENGINE_DISCOVERY_CURSOR_CONFLICT",
        "message": "cursor/query mismatch",
        "retryable": False,
    }


def test_g0_contract_exposes_bounded_discovery_resource():
    resources = g0_contract_descriptor()["query_contract"]["resources"]
    resource = next(item for item in resources if item["path"] == PATH)
    assert resource["pagination"] == "bounded_keyset_cursor"
    assert resource["snapshot"] == "CN_QUIESCENT_SERVING_EPOCH"
    assert resource["hard_bounds"] == {"max_pages": 10, "max_results": 1000}
    assert resource["read_budget"]["max_rows_to_read"] == 250000
    assert resource["read_budget"]["max_bytes_to_read"] == 268435456
    assert resource["business_state_owned_outside_data_engine"] is True
