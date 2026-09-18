from app import integration_api, integration_security


def test_integration_contract_freezes_service_and_write_boundaries():
    contract = integration_api.integration_contract()

    assert contract["contract_version"] == "MARKORBIT_DATA_ENGINE_INTEGRATION_V1"
    assert contract["source_owner"] == "MARKORBIT_DATA_ENGINE"
    assert contract["service_role"] == "SOURCE_FACT_SERVICE"
    assert contract["consumer_policy"] == {
        "query_plane_read_only": True,
        "change_feed_read_only": True,
        "cross_service_database_access": False,
        "consumer_writeback_to_source_facts": False,
        "business_state_owned_outside_data_engine": True,
    }
    assert contract["security"]["scheme"] == "BEARER_API_KEY"
    assert contract["security"]["default_mode"] == "disabled"
    assert contract["security"]["required_mode"] == "required"
    assert contract["security"]["fail_closed_when_required"] is True
    assert contract["planes"]["admin"]["part_of_consumer_contract"] is False
    assert contract["foundation_contracts"]["temporal_relationship"]["contract_version"] == (
        "MARKORBIT_TEMPORAL_RELATIONSHIP_V1"
    )
    assert contract["foundation_contracts"]["read_query_capability"]["contract_version"] == (
        "READ_QUERY_CAPABILITY_V2"
    )
    assert contract["foundation_contracts"]["read_query_capability"]["arbitrary_sql"] is False


def test_every_versioned_integration_route_is_read_only_and_authenticated():
    mutating_methods = {"POST", "PUT", "PATCH", "DELETE"}
    integration_routes = [
        route for route in integration_api.router.routes if route.path.startswith("/api/v1")
    ]

    assert integration_routes
    for route in integration_routes:
        assert not (set(route.methods or ()) & mutating_methods), route.path
        dependency_calls = {dependency.call for dependency in route.dependant.dependencies}
        assert integration_security.require_integration_auth in dependency_calls, route.path


def test_main_registers_stable_integration_routes_outside_admin_plane():
    import app.main as main

    routes = {route.path for route in main.app.routes}
    expected = {
        "/api/v1/contract",
        "/api/v1/cn/cases/{application_number}",
        "/api/v1/cn/agents/by-name",
        "/api/v1/cn/cases/{application_number}/relationships",
        "/api/v1/us/cases/{serial_number}",
        "/api/v1/us/cases/{serial_number}/events",
        "/api/v1/us/attorneys/by-name",
        "/api/v1/us/registrations/{registration_number}",
        "/api/v1/us/cases/{serial_number}/360",
        "/api/v1/us/cases/{serial_number}/history",
        "/api/v1/us/cases/{serial_number}/assignments",
        "/api/v1/us/cases/{serial_number}/ttab",
        "/api/v1/us/cases/{serial_number}/relationships",
        "/api/v1/us/changes",
    }

    assert expected <= routes
    assert all(not path.startswith("/api/v1/admin") for path in routes)
    assert all(not path.startswith("/api/v1/jobs") for path in routes)


def test_cn_case_wrapper_preserves_owner_and_delegates(monkeypatch):
    monkeypatch.setattr(
        integration_api,
        "cn_case",
        lambda application_number: {"case": {"application_number": application_number}},
    )

    result = integration_api.integration_cn_case("123456")

    assert result["source_owner"] == "MARKORBIT_DATA_ENGINE"
    assert result["jurisdiction"] == "CN"
    assert result["resource_kind"] == "TRADEMARK_CASE"
    assert result["legal_conclusion"] is False
    assert result["payload"]["case"]["application_number"] == "123456"


def test_cn_agent_name_wrapper_preserves_fact_boundary(monkeypatch):
    monkeypatch.setattr(integration_api, "clickhouse_client", lambda: object())
    monkeypatch.setattr(
        integration_api,
        "agents_by_name",
        lambda _client, name: {
            "input_name": name,
            "normalized_name": "示例代理事务所",
            "match_count": 1,
            "matches": [{"agent_code": "A100", "agent_name": name}],
        },
    )

    result = integration_api.integration_cn_agents_by_name("示例代理事务所")

    assert result["resource_kind"] == "AGENT_NAME_FACT_MATCHES"
    assert result["legal_conclusion"] is False
    assert result["payload"]["matches"][0]["agent_code"] == "A100"


def test_cn_relationship_timeline_wrapper_preserves_fact_boundary(monkeypatch):
    monkeypatch.setattr(integration_api, "clickhouse_client", lambda: object())
    monkeypatch.setattr(
        integration_api,
        "relationships_for_trademark",
        lambda _client, application, scope: {
            "application_number": application,
            "scope": scope,
            "relationship_count": 1,
            "relationships": [{"edge": {"relationship_type": "FORMER_OWNER"}}],
        },
    )

    result = integration_api.integration_cn_case_relationships(
        "12345678", scope="historical"
    )

    assert result["resource_kind"] == "TRADEMARK_RELATIONSHIP_TIMELINE"
    assert result["legal_conclusion"] is False
    assert result["payload"]["relationships"][0]["edge"]["relationship_type"] == (
        "FORMER_OWNER"
    )


def test_change_feed_wrapper_preserves_cursor_payload(monkeypatch):
    monkeypatch.setattr(
        integration_api,
        "us_change_feed",
        lambda **kwargs: {
            "changes": [{"serial_number": "99278031"}],
            "next_cursor": {
                "source_rank": kwargs["after_source_rank"] + 1,
                "serial_number": "99278031",
            },
        },
    )

    result = integration_api.integration_us_changes(
        after_source_rank=10,
        after_serial="99270000",
        scan_limit=25,
    )

    assert result["resource_kind"] == "TRADEMARK_CHANGE_FEED"
    assert result["legal_conclusion"] is False
    assert result["payload"]["next_cursor"] == {
        "source_rank": 11,
        "serial_number": "99278031",
    }


def test_registration_wrapper_preserves_fact_boundary(monkeypatch):
    monkeypatch.setattr(
        integration_api,
        "accepted_us_target_read_client",
        lambda: object(),
    )
    monkeypatch.setattr(
        integration_api,
        "lookup_registration",
        lambda _client, registration: {
            "registration_number": registration,
            "candidate_count": 1,
            "match_count": 1,
            "trademarks": [{"serial_number": "90000001"}],
        },
    )

    result = integration_api.integration_us_registration("7265548")

    assert result["resource_kind"] == "TRADEMARK_CASE_BY_REGISTRATION"
    assert result["fact_state"] == "observed"
    assert result["legal_conclusion"] is False
    assert result["payload"]["trademarks"][0]["serial_number"] == "90000001"


def test_event_timeline_wrapper_preserves_fact_boundary(monkeypatch):
    monkeypatch.setattr(integration_api, "accepted_us_target_read_client", lambda: object())
    monkeypatch.setattr(
        integration_api,
        "events_for_serial",
        lambda _client, serial, limit: {
            "serial_number": serial,
            "event_count": 1,
            "events": [{"event_code": "DOCK"}],
        },
    )

    result = integration_api.integration_us_case_events("90000001", limit=20)

    assert result["resource_kind"] == "TRADEMARK_EVENT_TIMELINE"
    assert result["legal_conclusion"] is False
    assert result["payload"]["events"][0]["event_code"] == "DOCK"


def test_attorney_name_wrapper_preserves_fact_boundary(monkeypatch):
    monkeypatch.setattr(integration_api, "accepted_us_target_read_client", lambda: object())
    monkeypatch.setattr(
        integration_api,
        "attorneys_by_name",
        lambda _client, name: {
            "input_name": name,
            "normalized_name": name.lower(),
            "match_count": 1,
            "matches": [{"serial_number": "90000001", "attorney_name": name}],
        },
    )

    result = integration_api.integration_us_attorneys_by_name("Jane Q. Attorney")

    assert result["resource_kind"] == "ATTORNEY_NAME_FACT_MATCHES"
    assert result["legal_conclusion"] is False
    assert result["payload"]["matches"][0]["serial_number"] == "90000001"


def test_us_relationship_timeline_wrapper_preserves_fact_boundary(monkeypatch):
    monkeypatch.setattr(integration_api, "clickhouse_client", lambda: object())
    monkeypatch.setattr(
        integration_api,
        "us_relationships_for_trademark",
        lambda _client, serial, scope: {
            "serial_number": serial,
            "scope": scope,
            "relationship_count": 1,
            "relationships": [{"edge": {"relationship_type": "ASSIGNOR"}}],
        },
    )

    result = integration_api.integration_us_case_relationships(
        "90000001", scope="historical"
    )

    assert result["resource_kind"] == "TRADEMARK_RELATIONSHIP_TIMELINE"
    assert result["legal_conclusion"] is False
    assert result["payload"]["relationships"][0]["edge"]["relationship_type"] == (
        "ASSIGNOR"
    )
