import uuid

from app import integration_api
from app.integration_contract import (
    CONTRACT_VERSION,
    CONTRACT_VERSION_HEADER,
    REQUEST_ID_HEADER,
    SOURCE_OWNER,
    SOURCE_OWNER_HEADER,
)
from app.integration_transport import normalize_request_id, response_headers


def test_request_id_accepts_safe_client_value_and_replaces_invalid_values():
    assert normalize_request_id("client-123.alpha") == "client-123.alpha"

    generated = normalize_request_id("bad request id with spaces")
    assert str(uuid.UUID(generated)) == generated

    generated_long = normalize_request_id("x" * 129)
    assert str(uuid.UUID(generated_long)) == generated_long


def test_contract_headers_are_scoped_to_api_v1():
    request_id = "req-123"
    headers = response_headers("/api/v1/contract", request_id)
    assert headers == {
        REQUEST_ID_HEADER: request_id,
        CONTRACT_VERSION_HEADER: CONTRACT_VERSION,
        SOURCE_OWNER_HEADER: SOURCE_OWNER,
    }

    assert response_headers("/api/admin/packages", request_id) == {
        REQUEST_ID_HEADER: request_id
    }
    assert response_headers("/api/health", request_id) == {REQUEST_ID_HEADER: request_id}


def test_versioned_health_reports_ok_without_changing_legacy_health(monkeypatch):
    monkeypatch.setattr(
        integration_api,
        "health",
        lambda: {
            "api": "ok",
            "version": "M1.6",
            "postgres": "ok",
            "clickhouse": "ok",
        },
    )
    payload = integration_api.integration_health()
    assert payload["contract_version"] == CONTRACT_VERSION
    assert payload["source_owner"] == SOURCE_OWNER
    assert payload["service_role"] == "SOURCE_FACT_SERVICE"
    assert payload["status"] == "ok"
    assert payload["dependencies"]["postgres"] == "ok"


def test_versioned_health_degrades_when_a_dependency_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        integration_api,
        "health",
        lambda: {
            "api": "ok",
            "version": "M1.6",
            "postgres": "ok",
            "clickhouse": "error: unavailable",
        },
    )
    assert integration_api.integration_health()["status"] == "degraded"


def test_contract_advertises_health_and_transport_headers(monkeypatch):
    monkeypatch.setattr(integration_api, "integration_security_contract", lambda: {"auth_mode": "disabled"})
    payload = integration_api.integration_contract()
    assert "/api/v1/health" in payload["stable_resources"]
    assert payload["transport"] == {
        "request_id_header": REQUEST_ID_HEADER,
        "request_id_echoed": True,
        "contract_version_header": CONTRACT_VERSION_HEADER,
        "source_owner_header": SOURCE_OWNER_HEADER,
    }
