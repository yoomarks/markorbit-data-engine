from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from app import integration_api, integration_runtime, integration_security
from app.integration_g0_contract import g0_contract_descriptor
from test_admin_control_security import _request


API_KEY = "scope-contract-test-credential-0001"


@pytest.mark.parametrize(
    ("method", "authorization", "expected_status"),
    [("GET", None, 401), ("GET", f"Bearer {API_KEY}", 200), ("POST", f"Bearer {API_KEY}", 405)],
)
def test_scope_descriptor_is_only_served_through_authenticated_readonly_route(
    monkeypatch, method, authorization, expected_status
):
    settings = SimpleNamespace(
        integration_auth_mode="required",
        integration_api_keys=API_KEY,
        integration_rate_limit_enabled=False,
    )
    monkeypatch.setattr(integration_security, "get_settings", lambda: settings)
    monkeypatch.setattr(integration_runtime, "get_settings", lambda: settings)
    app = FastAPI()
    app.include_router(integration_api.router)

    status, body = _request(app, method, "/api/v1/contract", authorization=authorization)
    assert status == expected_status
    assert API_KEY not in json.dumps(body)
    if status != 200:
        assert "foundation_contracts" not in body
        return
    descriptor = body["foundation_contracts"]["data_use_scope"]
    assert descriptor["status"] == "METADATA_ONLY"
    assert descriptor["runtime_enforcement_implemented"] is False
    assert descriptor["descriptor_grants_access"] is False
    assert descriptor["priority_jurisdiction_implies_dataset_availability"] is False


def test_additive_metadata_preserves_frozen_resources_and_needs_no_live_database(monkeypatch):
    def unavailable():
        raise AssertionError("Scope metadata must not open a database or probe production")

    monkeypatch.setattr(integration_api, "clickhouse_client", unavailable)
    monkeypatch.setattr(integration_api, "health", unavailable)
    frozen = json.loads(
        Path("docs/integrations/markorbit/MARKORBIT_DATA_ENGINE_INTEGRATION_V1.json").read_text(
            encoding="utf-8"
        )
    )
    contract = integration_api.integration_contract()
    assert contract["g0_contract"] == frozen == g0_contract_descriptor()
    assert contract["stable_resources"] == [
        resource["path"] for resource in frozen["query_contract"]["resources"]
    ]
    assert json.loads(json.dumps(contract)) == contract


def test_scope_metadata_does_not_bypass_invalid_service_auth_configuration(monkeypatch):
    settings = SimpleNamespace(
        integration_auth_mode="required",
        integration_api_keys="",
        integration_rate_limit_enabled=False,
    )
    monkeypatch.setattr(integration_security, "get_settings", lambda: settings)
    monkeypatch.setattr(integration_runtime, "get_settings", lambda: settings)
    app = FastAPI()
    app.include_router(integration_api.router)

    status, body = _request(app, "GET", "/api/v1/contract", authorization=f"Bearer {API_KEY}")
    assert status == 503
    assert body["detail"]["code"] == "DATA_ENGINE_INTEGRATION_AUTH_CONFIGURATION_INVALID"
    assert "foundation_contracts" not in body
