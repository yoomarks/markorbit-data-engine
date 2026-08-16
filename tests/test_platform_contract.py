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
    assert contract["operations"]["version"] == "MARKORBIT_OPERATIONS_V2"
    assert contract["foundation_contracts_complete"] is True
    assert contract["planned_contracts"] == []
    assert (
        contract["next_platformization_focus"]
        == "MIGRATE_PUBLISH_DAG_NODES_TO_NATIVE_EXECUTION_INCREMENTALLY"
    )

    cn_dag = contract["active_publish_dags"]["cn_final_publish"]
    assert cn_dag["dag_version"] == "CN_FINAL_PUBLISH_DAG_V1"
    assert cn_dag["execution_mode"] == "HYBRID_NATIVE_WITH_INFLIGHT_LEGACY_COMPATIBILITY"
    assert cn_dag["native_node_count"] == 12
    assert contract["compatibility"]["cn_inflight_checkpoint_schema_preserved"] is True
    assert contract["compatibility"]["cn_source_rank_semantics_unchanged"] is True
    assert contract["compatibility"]["storage_v2_semantics_unchanged"] is True
    assert contract["compatibility"]["cn_legacy_party_history_event_execution_unchanged"] is True
    assert (
        contract["compatibility"][
            "cn_legacy_case_delta_events_except_case_facts_preliminary_registration_unchanged"
        ]
        is True
    )
    assert contract["compatibility"]["cn_derived_case_baseline_suppression_unchanged"] is True
    assert (
        contract["compatibility"][
            "cn_agent_priority_madrid_case_current_case_party_current_close_case_scope_relation_scope_carve_out_case_facts_preliminary_registration_event_native_execution"
        ]
        is True
    )
    assert (
        contract["compatibility"][
            "cn_preexisting_final_publish_checkpoint_aux_fallback_preserved"
        ]
        is True
    )
    assert (
        contract["compatibility"][
            "cn_preexisting_final_publish_checkpoint_agent_fallback_preserved"
        ]
        is True
    )
    assert (
        contract["compatibility"][
            "cn_preexisting_final_publish_checkpoint_case_current_fallback_preserved"
        ]
        is True
    )
    assert (
        contract["compatibility"][
            "cn_preexisting_final_publish_checkpoint_case_party_current_fallback_preserved"
        ]
        is True
    )
    assert (
        contract["compatibility"][
            "cn_preexisting_final_publish_checkpoint_case_party_close_fallback_preserved"
        ]
        is True
    )
    assert (
        contract["compatibility"][
            "cn_preexisting_final_publish_checkpoint_case_scope_fallback_preserved"
        ]
        is True
    )
    assert (
        contract["compatibility"][
            "cn_preexisting_final_publish_checkpoint_case_relation_fallback_preserved"
        ]
        is True
    )
    assert (
        contract["compatibility"][
            "cn_preexisting_final_publish_checkpoint_scope_carve_out_fallback_preserved"
        ]
        is True
    )
    assert (
        contract["compatibility"][
            "cn_preexisting_final_publish_checkpoint_case_facts_event_fallback_preserved"
        ]
        is True
    )
    assert (
        contract["compatibility"][
            "cn_preexisting_final_publish_checkpoint_preliminary_publication_event_fallback_preserved"
        ]
        is True
    )
    assert (
        contract["compatibility"][
            "cn_preexisting_final_publish_checkpoint_registration_publication_event_fallback_preserved"
        ]
        is True
    )
    assert contract["compatibility"]["existing_consumer_payloads_unchanged"] is True
    assert contract["compatibility"]["existing_acceptance_reports_unchanged"] is True
    assert (
        contract["compatibility"]["existing_domain_mutation_gates_remain_authoritative"]
        is True
    )
