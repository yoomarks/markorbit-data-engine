from __future__ import annotations

import pytest
from fastapi import HTTPException

import app.integration_api as integration_api
from app.cn.agent_exact_read import AgentExactReadInvalid, AgentExactReadUnavailable
from app.integration_g0_contract import g0_contract_descriptor


PATH = "/api/v1/cn/agents/{agent_code}"


def test_cn_agent_exact_route_wraps_owner_read_and_not_found(monkeypatch):
    sentinel_client = object()
    captured = {}

    def fake_read(client, agent_code):
        captured["client"] = client
        captured["agent_code"] = agent_code
        return {
            "agent_code": agent_code,
            "record": {
                "agent_code": agent_code,
                "entity_id": "20000000-0000-0000-0000-000000000002",
            },
            "semantics": "CURRENT_OFFICIAL_CNIPA_AGENT_CODE_FACT_NO_IDENTITY_RESOLUTION",
        }

    monkeypatch.setattr(integration_api, "clickhouse_client", lambda: sentinel_client)
    monkeypatch.setattr(integration_api, "read_agent_exact", fake_read)
    monkeypatch.setattr(integration_api, "engine_version", lambda: "M1.9-test")

    body = integration_api.integration_cn_agent_exact("A001")

    assert captured == {"client": sentinel_client, "agent_code": "A001"}
    assert body["jurisdiction"] == "CN"
    assert body["resource_kind"] == "AGENT_SOURCE_RECORD"
    assert body["fact_state"] == "observed"
    assert body["authority"] == "DATA_ENGINE_FACT_READ_MODEL"
    assert body["legal_conclusion"] is False
    assert body["payload"]["record"]["entity_id"].startswith("20000000-")

    monkeypatch.setattr(
        integration_api,
        "read_agent_exact",
        lambda _client, code: {
            "agent_code": code,
            "record": None,
            "semantics": "CURRENT_OFFICIAL_CNIPA_AGENT_CODE_FACT_NO_IDENTITY_RESOLUTION",
        },
    )
    missing = integration_api.integration_cn_agent_exact("A404")
    assert missing["fact_state"] == "not_found"
    assert missing["payload"]["record"] is None


def test_cn_agent_exact_route_maps_invalid_and_unavailable(monkeypatch):
    monkeypatch.setattr(integration_api, "clickhouse_client", object)

    def invalid(_client, _agent_code):
        raise AgentExactReadInvalid("bad agent code")

    monkeypatch.setattr(integration_api, "read_agent_exact", invalid)
    with pytest.raises(HTTPException) as caught:
        integration_api.integration_cn_agent_exact("bad")
    assert caught.value.status_code == 400
    assert caught.value.detail == {
        "code": "DATA_ENGINE_AGENT_EXACT_READ_INVALID",
        "message": "bad agent code",
        "retryable": False,
    }

    def unavailable(_client, _agent_code):
        raise AgentExactReadUnavailable("offline")

    monkeypatch.setattr(integration_api, "read_agent_exact", unavailable)
    with pytest.raises(HTTPException) as caught:
        integration_api.integration_cn_agent_exact("A001")
    assert caught.value.status_code == 503
    assert caught.value.detail == {
        "code": "DATA_ENGINE_AGENT_EXACT_READ_UNAVAILABLE",
        "message": "offline",
        "retryable": True,
    }


def test_g0_contract_advertises_exact_cn_agent_read_as_read_only():
    resources = g0_contract_descriptor()["query_contract"]["resources"]
    resource = next(item for item in resources if item["path"] == PATH)
    assert resource["pagination"] == "none"
    assert resource["read_model"] == "CN_AGENT_CURRENT"
    assert resource["index_key"] == "agent_code"
    assert resource["read_only"] is True
    assert resource["business_state_owned_outside_data_engine"] is True
