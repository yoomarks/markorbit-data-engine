from __future__ import annotations

from typing import Any

from app.domain_adapter import domain_adapter_contract
from app.work_engine import work_engine_contract


PLATFORMIZATION_VERSION = "MARKORBIT_PLATFORMIZATION_M1.7"


def platform_contract() -> dict[str, Any]:
    return {
        "version": PLATFORMIZATION_VERSION,
        "status": "IN_PROGRESS",
        "goal": "GLOBAL_SOURCE_FACT_PLATFORM_BEFORE_NEXT_JURISDICTION",
        "work_engine": work_engine_contract(),
        "domain_adapter": domain_adapter_contract(),
        "planned_contracts": [
            "GLOBAL_FACT_EVENT_ENVELOPE_V1",
            "DATA_TRUST_FRESHNESS_V1",
            "OPERATIONS_V2",
        ],
        "compatibility": {
            "cn_inflight_checkpoint_schema_preserved": True,
            "cn_source_rank_semantics_unchanged": True,
            "storage_v2_semantics_unchanged": True,
            "integration_v1_read_only_boundary_unchanged": True,
        },
    }
