from app import integration_api


def test_integration_v1_exposes_platformization_without_changing_source_fact_boundary() -> None:
    contract = integration_api.integration_contract()
    platform = contract["platformization"]

    assert platform["version"] == "MARKORBIT_PLATFORMIZATION_M1.7"
    assert platform["work_engine"]["version"] == "MARKORBIT_WORK_ENGINE_V1"
    assert platform["domain_adapter"]["version"] == "MARKORBIT_DOMAIN_ADAPTER_V1"
    assert platform["compatibility"]["cn_inflight_checkpoint_schema_preserved"] is True
    assert contract["consumer_policy"]["cross_service_database_access"] is False
    assert contract["consumer_policy"]["consumer_writeback_to_source_facts"] is False
