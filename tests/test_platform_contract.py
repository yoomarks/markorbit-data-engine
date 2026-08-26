from app.platform_contract import platform_contract
from app.release_promotion import PROMOTION_CONTRACT_VERSION


def test_platformization_contract_keeps_cn_runtime_compatibility() -> None:
    contract = platform_contract()

    assert contract["version"] == "MARKORBIT_PLATFORMIZATION_M1.7"
    assert contract["status"] == "CODE_READY_PENDING_RUNTIME_ACCEPTANCE"
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
        == "RUN_LIGHTWEIGHT_CN_SERVING_STATE_AND_EVALUATE_PROMOTION"
    )

    runtime = contract["runtime_acceptance_boundary"]
    assert runtime["required"] is True
    assert runtime["evaluated_by_platform_contract"] is False
    assert runtime["authoritative_checkpoint"] == PROMOTION_CONTRACT_VERSION
    assert (
        runtime["evidence_mode"]
        == "PRIOR_RUNTIME_OPERATOR_ACCEPTED_PLUS_CURRENT_SERVING_STATE"
    )
    assert runtime["real_corpus_success_claimed"] is False
    assert runtime["fresh_full_corpus_validation_claimed"] is False
    assert runtime["package_replay_or_rescan_required"] is False
    assert runtime["release_promotion_allowed_without_runtime_acceptance"] is False

    cn_dag = contract["active_publish_dags"]["cn_final_publish"]
    assert cn_dag["dag_version"] == "CN_FINAL_PUBLISH_DAG_V1"
    assert cn_dag["execution_mode"] == "HYBRID_NATIVE_WITH_INFLIGHT_LEGACY_COMPATIBILITY"
    assert cn_dag["native_node_count"] == 18

    cutover = contract["cn_native_cutover"]
    assert cutover["version"] == "CN_NATIVE_CUTOVER_COMPLETION_V1"
    assert cutover["status"] == "COMPLETE"
    assert cutover["native_business_node_count"] == 18
    assert cutover["intentional_compatibility_node_count"] == 3
    assert cutover["native_business_node_set_frozen"] is True
    assert cutover["no_executable_legacy_business_nodes_remaining"] is True
    assert cutover["storage_v2_suppression_boundaries_frozen"] is True

    assert contract["compatibility"]["cn_inflight_checkpoint_schema_preserved"] is True
    assert contract["compatibility"]["cn_source_rank_semantics_unchanged"] is True
    assert contract["compatibility"]["storage_v2_semantics_unchanged"] is True
    assert contract["compatibility"]["cn_legacy_party_history_event_execution_unchanged"] is True
    assert contract["compatibility"]["cn_party_observed_event_semantics_unchanged"] is True
    assert (
        contract["compatibility"][
            "cn_legacy_case_delta_events_except_case_facts_preliminary_registration_exclusive_mark_name_agent_code_unchanged"
        ]
        is True
    )
    assert (
        contract["compatibility"][
            "cn_goods_scope_event_storage_v2_delta_rewrite_contract_preserved"
        ]
        is True
    )
    assert contract["compatibility"]["cn_derived_case_baseline_suppression_unchanged"] is True
    assert (
        contract["compatibility"][
            "cn_agent_priority_madrid_case_current_case_party_current_close_case_scope_relation_scope_carve_out_case_facts_preliminary_registration_exclusive_mark_name_agent_code_goods_scope_party_superseded_party_observed_event_native_execution"
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
    assert (
        contract["compatibility"][
            "cn_preexisting_final_publish_checkpoint_exclusive_term_event_fallback_preserved"
        ]
        is True
    )
    assert (
        contract["compatibility"][
            "cn_preexisting_final_publish_checkpoint_mark_name_event_fallback_preserved"
        ]
        is True
    )
    assert (
        contract["compatibility"][
            "cn_preexisting_final_publish_checkpoint_agent_code_event_fallback_preserved"
        ]
        is True
    )
    assert (
        contract["compatibility"][
            "cn_preexisting_final_publish_checkpoint_goods_scope_event_fallback_preserved"
        ]
        is True
    )
    assert (
        contract["compatibility"][
            "cn_preexisting_final_publish_checkpoint_party_superseded_event_fallback_preserved"
        ]
        is True
    )
    assert (
        contract["compatibility"][
            "cn_preexisting_final_publish_checkpoint_party_observed_event_fallback_preserved"
        ]
        is True
    )
    assert contract["compatibility"]["existing_consumer_payloads_unchanged"] is True
    assert contract["compatibility"]["existing_acceptance_reports_unchanged"] is True
    assert (
        contract["compatibility"]["existing_domain_mutation_gates_remain_authoritative"]
        is True
    )
