from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.integration_api as integration_api
from app.applicant_owner_read import OwnerReadConflict, OwnerReadUnavailable
from app.integration_g0_contract import g0_contract_descriptor
from app.integration_owner_read import owner_read_http_error, reject_unknown_query


def _request(path: str, query: str = "") -> Request:
    request = Request({
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [],
        "query_string": query.encode("ascii"),
    })
    request.state.request_id = "hop-request-1"
    return request


def test_owner_read_routes_are_get_only_and_g0_declared():
    paths = {
        "/api/v1/{jurisdiction}/applicants/{applicant_candidate_id}",
        "/api/v1/{jurisdiction}/applicants/{applicant_candidate_id}/portfolio",
        "/api/v1/{jurisdiction}/applicants/{applicant_candidate_id}/trademarks/{trademark_candidate_id}",
    }
    routes = {route.path: set(route.methods or ()) for route in integration_api.router.routes}
    assert all(routes[path] == {"GET"} for path in paths)
    resources = {
        item["path"] for item in g0_contract_descriptor()["query_contract"]["resources"]
    }
    assert paths <= resources


def test_owner_read_unknown_query_field_is_rejected():
    request = _request("/api/v1/us/applicants/x", "unexpected=1")
    with pytest.raises(HTTPException) as caught:
        reject_unknown_query(request, {"requester_workspace_id"})
    assert caught.value.status_code == 400
    assert caught.value.detail["code"] == "DATA_ENGINE_OWNER_READ_UNKNOWN_QUERY_FIELD"


def test_owner_read_error_mapping_preserves_conflict_and_unavailable():
    conflict = owner_read_http_error(OwnerReadConflict("stale ref"))
    unavailable = owner_read_http_error(OwnerReadUnavailable("epoch changed"))
    assert conflict.status_code == 409
    assert conflict.detail["retryable"] is False
    assert unavailable.status_code == 503
    assert unavailable.detail["retryable"] is True


def test_us_applicant_route_delegates_and_wraps_v1_envelope(monkeypatch):
    captured = {}
    result = SimpleNamespace(fact_state="observed", payload={"marker": "ok"})

    def fake_read(client, **kwargs):
        captured["client"] = client
        captured.update(kwargs)
        return result

    sentinel = object()
    monkeypatch.setattr(integration_api, "clickhouse_client", lambda: sentinel)
    monkeypatch.setattr(integration_api, "us_read_applicant_exact", fake_read)
    body = integration_api.integration_applicant_owner_exact(
        request=_request("/api/v1/us/applicants/x"),
        jurisdiction="us",
        applicant_candidate_id="us:applicant:" + "a" * 64,
        requester_workspace_id="ws-1",
        applicant_source_id="US_APPLICANT:" + "a" * 64,
        applicant_source_version="us-serving-epoch:" + "e" * 64,
        applicant_source_fingerprint_sha256="sha256:" + "f" * 64,
        applicant_observed_at="2026-09-01T00:00:00Z",
    )
    assert captured["client"] is sentinel
    assert captured["workspace_id"] == "ws-1"
    assert captured["request_id"] == "hop-request-1"
    assert body["resource_kind"] == "APPLICANT_IDENTITY_DISCOVERY"
    assert body["fact_state"] == "observed"
    assert body["payload"] == {"marker": "ok"}
    assert body["legal_conclusion"] is False
