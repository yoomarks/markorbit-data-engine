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
        "/api/v1/us/applicants/by-name",
        "/api/v1/{jurisdiction}/applicants/{applicant_candidate_id}",
        "/api/v1/{jurisdiction}/applicants/{applicant_candidate_id}/portfolio",
        "/api/v1/us/applicants/{applicant_candidate_id}/recorded-history",
        "/api/v1/{jurisdiction}/applicants/{applicant_candidate_id}/trademarks/{trademark_candidate_id}",
    }
    routes = {route.path: set(route.methods or ()) for route in integration_api.router.routes}
    assert all(routes[path] == {"GET"} for path in paths)
    resource_items = g0_contract_descriptor()["query_contract"]["resources"]
    resources = {item["path"] for item in resource_items}
    assert paths <= resources
    by_name = next(item for item in resource_items if item["path"] == "/api/v1/us/applicants/by-name")
    assert by_name["hard_bounds"] == {"max_pages": 100, "max_results": 100}
    route_order = [route.path for route in integration_api.router.routes]
    assert route_order.index("/api/v1/us/applicants/by-name") < route_order.index(
        "/api/v1/{jurisdiction}/applicants/{applicant_candidate_id}"
    )


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
    monkeypatch.setattr(integration_api, "accepted_us_target_read_client", lambda: sentinel)
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

def test_us_applicant_name_route_delegates_and_wraps_v1_envelope(monkeypatch):
    captured = {}
    result = SimpleNamespace(fact_state="observed", payload={"results": [{"marker": "name"}]})

    def fake_read(client, **kwargs):
        captured["client"] = client
        captured.update(kwargs)
        return result

    sentinel = object()
    monkeypatch.setattr(integration_api, "accepted_us_target_read_client", lambda: sentinel)
    monkeypatch.setattr(integration_api, "us_discover_applicants_by_name", fake_read)
    body = integration_api.integration_us_applicants_by_name(
        request=_request(
            "/api/v1/us/applicants/by-name",
            "name=Example%20Holdings%20LLC&requester_workspace_id=ws-1&page_size=25",
        ),
        name="Example Holdings LLC",
        requester_workspace_id="ws-1",
        page_size=25,
        cursor=None,
    )
    assert captured["client"] is sentinel
    assert captured["workspace_id"] == "ws-1"
    assert captured["request_id"] == "hop-request-1"
    assert captured["name"] == "Example Holdings LLC"
    assert captured["page_size"] == 25
    assert body["jurisdiction"] == "US"
    assert body["resource_kind"] == "APPLICANT_IDENTITY_DISCOVERY"
    assert body["fact_state"] == "observed"
    assert body["payload"] == {"results": [{"marker": "name"}]}
    assert body["legal_conclusion"] is False


def test_us_applicant_name_route_rejects_unknown_query_field():
    request = _request(
        "/api/v1/us/applicants/by-name",
        "name=Example&requester_workspace_id=ws-1&fuzzy=true",
    )
    with pytest.raises(HTTPException) as caught:
        integration_api.integration_us_applicants_by_name(
            request=request, name="Example", requester_workspace_id="ws-1",
            page_size=50, cursor=None,
        )
    assert caught.value.status_code == 400
    assert caught.value.detail["fields"] == ["fuzzy"]


def test_us_applicant_recorded_history_bridges_without_identity_claim(monkeypatch):
    current_client = object()
    history_client = object()
    current = SimpleNamespace(
        fact_state="observed",
        payload={
            "results": [{
                "candidate_type": "APPLICANT_IDENTITY",
                "applicant_candidate_id": "us:applicant:" + "a" * 64,
                "display_name": "Example Holdings LLC",
                "review_required": True,
                "verified_legal_identity": False,
            }],
            "source_snapshot": {"source_version": "epoch-1", "observed_at": "2026-09-01T00:00:00Z"},
        },
    )
    captured = {}

    def fake_current(client, **kwargs):
        assert client is current_client
        captured["current"] = kwargs
        return current

    def fake_history(request, *, client):
        assert client is history_client
        captured["history_request"] = request
        return {
            "results": [{"serial_number": "90000001", "identity_resolution_claimed": False}],
            "result_count": 1,
            "next_cursor": None,
            "semantics": "EXACT_NORMALIZED_NAME;NOT_CROSS_SOURCE_IDENTITY_RESOLUTION",
        }

    monkeypatch.setattr(integration_api, "accepted_us_target_read_client", lambda: current_client)
    monkeypatch.setattr(integration_api, "clickhouse_client", lambda: history_client)
    monkeypatch.setattr(integration_api, "us_read_applicant_exact", fake_current)
    monkeypatch.setattr(
        integration_api, "execute_us_recorded_party_history_page", fake_history
    )

    body = integration_api.integration_us_applicant_recorded_history(
        request=_request("/api/v1/us/applicants/x/recorded-history"),
        applicant_candidate_id="us:applicant:" + "a" * 64,
        requester_workspace_id="ws-1",
        applicant_source_id="US_APPLICANT:" + "a" * 64,
        applicant_source_version="us-serving-epoch:" + "e" * 64,
        applicant_source_fingerprint_sha256="sha256:" + "f" * 64,
        applicant_observed_at="2026-09-01T00:00:00Z",
        source_domain="US_ASSIGNMENT",
        relationship_type="ASSIGNEE",
        page_size=25,
        cursor=None,
    )
    history_request = captured["history_request"]
    assert history_request.name == "Example Holdings LLC"
    assert history_request.source_domain == "US_ASSIGNMENT"
    assert history_request.relationship_type == "ASSIGNEE"
    assert history_request.page_size == 25

    assert captured["current"]["candidate_id"] == "us:applicant:" + "a" * 64
    assert body["resource_kind"] == "APPLICANT_RECORDED_RELATIONSHIP_DISCOVERY"
    assert body["fact_state"] == "observed"
    assert body["legal_conclusion"] is False
    payload = body["payload"]
    assert payload["historical_match_state"] == "observed"
    assert payload["identity_resolution_claimed"] is False
    assert payload["review_required"] is True
    assert payload["legal_conclusion"] is False
    assert payload["authority_consequences"] == {
        "verifiedLegalIdentityEstablished": False,
        "historicalOwnershipEstablished": False,
        "historicalRepresentationEstablished": False,
        "customerRelationshipEstablished": False,
        "legalConclusionCreated": False,
        "externalActionAuthorized": False,
    }
    assert "NOT_CROSS_SOURCE_IDENTITY_RESOLUTION" in payload["semantics"]
