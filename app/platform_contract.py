from __future__ import annotations

from typing import Any

from app.cn.native_cutover_completion import cn_native_cutover_completion_contract
from app.cn.publish_dag import cn_final_publish_dag_contract
from app.data_trust import data_trust_contract
from app.domain_adapter import domain_adapter_contract
from app.fact_event_envelope import fact_event_envelope_contract
from app.operations_v2 import operations_contract
from app.snapshot_delta.ipos_sg_tasks import ipos_sg_operator_task_contract
from app.work_dag import work_dag_contract
from app.work_engine import work_engine_contract


PLATFORMIZATION_VERSION = "MARKORBIT_PLATFORMIZATION_M1.7"


def platform_contract() -> dict[str, Any]:
    return {
        "version": PLATFORMIZATION_VERSION,
        "status": "CODE_READY_PENDING_RUNTIME_ACCEPTANCE",
        "goal": "GLOBAL_SOURCE_FACT_PLATFORM_BEFORE_NEXT_JURISDICTION",
        "work_engine": work_engine_contract(),
        "work_dag": work_dag_contract(),
        "active_publish_dags": {
            "cn_final_publish": cn_final_publish_dag_contract(),
        },
        "active_source_dags": {
            "sg_ipos_authenticated_operator": ipos_sg_operator_task_contract(),
        },
        "cn_native_cutover": cn_native_cutover_completion_contract(),
        "domain_adapter": domain_adapter_contract(),
        "fact_event_envelope": fact_event_envelope_contract(),
        "data_trust": data_trust_contract(),
        "operations": operations_contract(),
        "planned_contracts": [],
        "foundation_contracts_complete": True,
        "runtime_acceptance_boundary": {
            "required": True,
            "evaluated_by_platform_contract": False,
            "authoritative_checkpoint": "CN_M16_FINAL_CHECKPOINT_V1",
            "real_corpus_success_claimed": False,
            "release_promotion_allowed_without_runtime_acceptance": False,
        },
        "next_platformization_focus": "RUN_REAL_CN_RUNTIME_ACCEPTANCE_SEPARATELY",
        "compatibility": {
            "cn_inflight_checkpoint_schema_preserved": True,
            "cn_source_rank_semantics_unchanged": True,
            "storage_v2_semantics_unchanged": True,
            "integration_v1_read_only_boundary_unchanged": True,
            "cn_legacy_party_history_event_execution_unchanged": True,
            "cn_party_observed_event_semantics_unchanged": True,
            "cn_legacy_case_delta_events_except_case_facts_preliminary_registration_exclusive_mark_name_agent_code_unchanged": True,
            "cn_goods_scope_event_storage_v2_delta_rewrite_contract_preserved": True,
            "cn_derived_case_baseline_suppression_unchanged": True,
            "cn_agent_priority_madrid_case_current_case_party_current_close_case_scope_relation_scope_carve_out_case_facts_preliminary_registration_exclusive_mark_name_agent_code_goods_scope_party_superseded_party_observed_event_native_execution": True,
            "cn_preexisting_final_publish_checkpoint_aux_fallback_preserved": True,
            "cn_preexisting_final_publish_checkpoint_agent_fallback_preserved": True,
            "cn_preexisting_final_publish_checkpoint_case_current_fallback_preserved": True,
            "cn_preexisting_final_publish_checkpoint_case_party_current_fallback_preserved": True,
            "cn_preexisting_final_publish_checkpoint_case_party_close_fallback_preserved": True,
            "cn_preexisting_final_publish_checkpoint_case_scope_fallback_preserved": True,
            "cn_preexisting_final_publish_checkpoint_case_relation_fallback_preserved": True,
            "cn_preexisting_final_publish_checkpoint_scope_carve_out_fallback_preserved": True,
            "cn_preexisting_final_publish_checkpoint_case_facts_event_fallback_preserved": True,
            "cn_preexisting_final_publish_checkpoint_preliminary_publication_event_fallback_preserved": True,
            "cn_preexisting_final_publish_checkpoint_registration_publication_event_fallback_preserved": True,
            "cn_preexisting_final_publish_checkpoint_exclusive_term_event_fallback_preserved": True,
            "cn_preexisting_final_publish_checkpoint_mark_name_event_fallback_preserved": True,
            "cn_preexisting_final_publish_checkpoint_agent_code_event_fallback_preserved": True,
            "cn_preexisting_final_publish_checkpoint_goods_scope_event_fallback_preserved": True,
            "cn_preexisting_final_publish_checkpoint_party_superseded_event_fallback_preserved": True,
            "cn_preexisting_final_publish_checkpoint_party_observed_event_fallback_preserved": True,
            "existing_consumer_payloads_unchanged": True,
            "existing_acceptance_reports_unchanged": True,
            "existing_domain_mutation_gates_remain_authoritative": True,
        },
    }
