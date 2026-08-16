from __future__ import annotations

from typing import Any

from app.cn.publish_dag import cn_final_publish_dag_contract
from app.data_trust import data_trust_contract
from app.domain_adapter import domain_adapter_contract
from app.fact_event_envelope import fact_event_envelope_contract
from app.operations_v2 import operations_contract
from app.work_dag import work_dag_contract
from app.work_engine import work_engine_contract


PLATFORMIZATION_VERSION = "MARKORBIT_PLATFORMIZATION_M1.7"


def platform_contract() -> dict[str, Any]:
    return {
        "version": PLATFORMIZATION_VERSION,
        "status": "IN_PROGRESS",
        "goal": "GLOBAL_SOURCE_FACT_PLATFORM_BEFORE_NEXT_JURISDICTION",
        "work_engine": work_engine_contract(),
        "work_dag": work_dag_contract(),
        "active_publish_dags": {
            "cn_final_publish": cn_final_publish_dag_contract(),
        },
        "domain_adapter": domain_adapter_contract(),
        "fact_event_envelope": fact_event_envelope_contract(),
        "data_trust": data_trust_contract(),
        "operations": operations_contract(),
        "planned_contracts": [],
        "foundation_contracts_complete": True,
        "next_platformization_focus": "MIGRATE_PUBLISH_DAG_NODES_TO_NATIVE_EXECUTION_INCREMENTALLY",
        "compatibility": {
            "cn_inflight_checkpoint_schema_preserved": True,
            "cn_source_rank_semantics_unchanged": True,
            "storage_v2_semantics_unchanged": True,
            "integration_v1_read_only_boundary_unchanged": True,
            "cn_publish_sql_execution_unchanged": True,
            "existing_consumer_payloads_unchanged": True,
            "existing_acceptance_reports_unchanged": True,
            "existing_domain_mutation_gates_remain_authoritative": True,
        },
    }
