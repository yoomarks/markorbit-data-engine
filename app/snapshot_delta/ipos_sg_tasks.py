from __future__ import annotations

from typing import Any

from app.work_dag import WorkDagDefinition, WorkDagNode


IPOS_SG_OPERATOR_DAG_VERSION = "IPOS_SG_OPERATOR_DAG_V1"

IPOS_SG_OPERATOR_DAG = WorkDagDefinition(
    dag_id="IPOS_SG_AUTHENTICATED_OPERATOR",
    version=IPOS_SG_OPERATOR_DAG_VERSION,
    nodes=(
        WorkDagNode(
            task_id="STATE_PREFLIGHT",
            operation_kind="AUDIT_STATE",
            target="IPOS_SG_LIFECYCLE_STATE",
            partition_kind="CUSTOM",
            audit_policy="FAST_POINTER_MANIFEST_SNAPSHOT_INTEGRITY",
            native_execution=True,
        ),
        WorkDagNode(
            task_id="RESOURCE_PREFLIGHT",
            operation_kind="AUDIT_RESOURCES",
            target="IPOS_SG_STATE_FILESYSTEM",
            partition_kind="CUSTOM",
            dependencies=("STATE_PREFLIGHT",),
            audit_policy="MINIMUM_8_GIB_AND_DOUBLE_RETAINED_SNAPSHOT_HEADROOM",
            native_execution=True,
        ),
        WorkDagNode(
            task_id="LIVE_SOURCE_AUTHENTICATION",
            operation_kind="PROBE_SOURCE",
            target="DATA_GOV_SG_IPOS_DATASTORE",
            partition_kind="CUSTOM",
            dependencies=("RESOURCE_PREFLIGHT",),
            audit_policy="AUTHENTICATED_AUTHORITATIVE_39_FIELD_SCHEMA",
            native_execution=True,
        ),
        WorkDagNode(
            task_id="FULL_CORPUS_LIFECYCLE",
            operation_kind="ACQUIRE_AND_COMMIT_SNAPSHOT",
            target="IPOS_SG_TRADEMARK_APPLICATIONS",
            partition_kind="CUSTOM",
            dependencies=("LIVE_SOURCE_AUTHENTICATION",),
            audit_policy=(
                "SINGLE_AUTHENTICATED_INITIATE_POLL_STREAM_SCHEMA_DELTA_NATIVE_EVIDENCE_POINTER"
            ),
            native_execution=True,
        ),
        WorkDagNode(
            task_id="STATE_POSTFLIGHT",
            operation_kind="AUDIT_STATE",
            target="IPOS_SG_LIFECYCLE_STATE",
            partition_kind="CUSTOM",
            dependencies=("FULL_CORPUS_LIFECYCLE",),
            audit_policy="STRICT_READY_SINGLE_RETAINED_FULL_SNAPSHOT",
            native_execution=True,
        ),
        WorkDagNode(
            task_id="ACCEPTANCE_RECEIPT",
            operation_kind="PERSIST_ACCEPTANCE",
            target="IPOS_SG_OPERATOR_ACCEPTANCE",
            partition_kind="CUSTOM",
            dependencies=("STATE_POSTFLIGHT",),
            audit_policy="ATOMIC_SECRET_FREE_MACHINE_READABLE_RECEIPT",
            native_execution=True,
        ),
    ),
)


def ipos_sg_operator_task_contract() -> dict[str, Any]:
    contract = IPOS_SG_OPERATOR_DAG.contract()
    contract.update(
        {
            "jurisdiction": "SG",
            "source": "IPOS_SG_TRADEMARK_APPLICATIONS",
            "execution_mode": "EXPLICIT_OPERATOR_ONE_SHOT",
            "recurring_schedule_enabled": False,
            "production_schedule_gate": (
                "REAL_AUTHENTICATED_TWO_CYCLE_ACCEPTANCE_AND_OPERATIONS_REVIEW_REQUIRED"
            ),
            "secret_transport": "PROCESS_ENVIRONMENT_ONLY",
            "whole_dataset_materializations_per_run": 1,
        }
    )
    return contract
