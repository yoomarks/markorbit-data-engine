from app.platform_contract import platform_contract
from app.snapshot_delta.ipos_sg_tasks import (
    IPOS_SG_OPERATOR_DAG,
    ipos_sg_operator_task_contract,
)


def test_singapore_operator_dag_is_explicit_and_ordered():
    assert IPOS_SG_OPERATOR_DAG.topological_order() == (
        "STATE_PREFLIGHT",
        "RESOURCE_PREFLIGHT",
        "LIVE_SOURCE_AUTHENTICATION",
        "FULL_CORPUS_LIFECYCLE",
        "STATE_POSTFLIGHT",
        "ACCEPTANCE_RECEIPT",
    )
    IPOS_SG_OPERATOR_DAG.assert_observed_order(IPOS_SG_OPERATOR_DAG.topological_order())


def test_singapore_operator_task_contract_keeps_schedule_disabled_before_live_acceptance():
    contract = ipos_sg_operator_task_contract()

    assert contract["dag_id"] == "IPOS_SG_AUTHENTICATED_OPERATOR"
    assert contract["jurisdiction"] == "SG"
    assert contract["recurring_schedule_enabled"] is False
    assert contract["whole_dataset_materializations_per_run"] == 1
    assert all(node["native_execution"] for node in contract["nodes"])


def test_platform_contract_registers_singapore_source_dag_without_promoting_release():
    contract = platform_contract()
    source_dag = contract["active_source_dags"]["sg_ipos_authenticated_operator"]

    assert source_dag["dag_version"] == "IPOS_SG_OPERATOR_DAG_V1"
    assert source_dag["recurring_schedule_enabled"] is False
    assert contract["status"] == "CODE_READY_PENDING_RUNTIME_ACCEPTANCE"
    assert contract["runtime_acceptance_boundary"]["real_corpus_success_claimed"] is False
