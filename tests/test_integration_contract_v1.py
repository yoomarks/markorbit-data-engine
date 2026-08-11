from app import integration_api


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
    assert contract["planes"]["admin"]["part_of_consumer_contract"] is False


def test_every_versioned_integration_route_is_read_only():
    mutating_methods = {"POST", "PUT", "PATCH", "DELETE"}
    integration_routes = [
        route for route in integration_api.router.routes if route.path.startswith("/api/v1")
    ]

    assert integration_routes
    for route in integration_routes:
        assert not (set(route.methods or ()) & mutating_methods), route.path


def test_main_registers_stable_integration_routes_outside_admin_plane():
    import app.main as main

    routes = {route.path for route in main.app.routes}
    expected = {
        "/api/v1/contract",
        "/api/v1/cn/cases/{application_number}",
        "/api/v1/us/cases/{serial_number}",
        "/api/v1/us/cases/{serial_number}/360",
        "/api/v1/us/cases/{serial_number}/history",
        "/api/v1/us/cases/{serial_number}/assignments",
        "/api/v1/us/cases/{serial_number}/ttab",
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
