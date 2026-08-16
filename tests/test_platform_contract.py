from app.platform_contract import platform_contract


def test_platformization_contract_keeps_cn_runtime_compatibility() -> None:
    contract = platform_contract()

    assert contract["version"] == "MARKORBIT_PLATFORMIZATION_M1.7"
    assert contract["goal"] == "GLOBAL_SOURCE_FACT_PLATFORM_BEFORE_NEXT_JURISDICTION"
    assert contract["work_engine"]["version"] == "MARKORBIT_WORK_ENGINE_V1"
    assert contract["work_dag"]["version"] == "MARKORBIT_WORK_DAG_V1"
    assert contract["domain_adapter"]["version"] == "MARKORBIT_DOMAIN_ADAPTER_V1"
    assert (
        contract["fact_event_envelope"]["version"]
        == "MARKORBIT_FACT_EVENT_ENVELOPE_V1"
    )
    assert contract["fact_event_envelope"]["legal_conclusion"] is False
    assert contract["data_trust"]["version"] == "MARKORBIT_DATA_TRUST_FRESHNESS_V1"
    assert contract["data_trust"]["legal_conclusion"] is False
    assert contract["planned_contracts"] == ["OPERATIONS_V2"]

    cn_dag = contract["active_publish_dags"]["cn_final_publish"]
    assert cn_dag["dag_version"] == "CN_FINAL_PUBLISH_DAG_V1"
    assert cn_dag["execution_mode"] == "LEGACY_PUBLISHER_MAPPED_TO_EXPLICIT_DAG"
    assert cn_dag["native_node_count"] == 0
    assert contract["compatibility"]["cn_inflight_checkpoint_schema_preserved"] is True
    assert contract["compatibility"]["cn_source_rank_semantics_unchanged"] is True
    assert contract["compatibility"]["storage_v2_semantics_unchanged"] is True
    assert contract["compatibility"]["cn_publish_sql_execution_unchanged"] is True
    assert contract["compatibility"]["existing_consumer_payloads_unchanged"] is True
    assert contract["compatibility"]["existing_acceptance_reports_unchanged"] is True
