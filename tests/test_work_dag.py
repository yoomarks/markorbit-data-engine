import pytest

from app.work_dag import WorkDagDefinition, WorkDagNode, work_dag_contract


def test_work_dag_validates_and_orders_dependencies() -> None:
    dag = WorkDagDefinition(
        dag_id="TEST",
        version="V1",
        nodes=(
            WorkDagNode("A", "STAGE", "a", "FILE_PART"),
            WorkDagNode("B", "PUBLISH", "b", "FILE_PART", dependencies=("A",)),
            WorkDagNode("C", "AUDIT", "c", "CUSTOM", dependencies=("B",)),
        ),
    )

    assert dag.topological_order() == ("A", "B", "C")
    dag.assert_observed_order(("A", "B", "C"))
    dag.assert_observed_order(("B", "C"))


def test_work_dag_rejects_unknown_dependency_and_cycles() -> None:
    with pytest.raises(ValueError, match="unknown task MISSING"):
        WorkDagDefinition(
            dag_id="BROKEN",
            version="V1",
            nodes=(
                WorkDagNode(
                    "A", "PUBLISH", "a", "CUSTOM", dependencies=("MISSING",)
                ),
            ),
        )

    with pytest.raises(ValueError, match="cycle"):
        WorkDagDefinition(
            dag_id="CYCLE",
            version="V1",
            nodes=(
                WorkDagNode("A", "PUBLISH", "a", "CUSTOM", dependencies=("B",)),
                WorkDagNode("B", "PUBLISH", "b", "CUSTOM", dependencies=("A",)),
            ),
        )


def test_work_dag_observed_order_fails_closed() -> None:
    dag = WorkDagDefinition(
        dag_id="TEST",
        version="V1",
        nodes=(
            WorkDagNode("A", "PUBLISH", "a", "CUSTOM"),
            WorkDagNode("B", "AUDIT", "b", "CUSTOM", dependencies=("A",)),
        ),
    )

    with pytest.raises(RuntimeError, match="B ran before dependency A"):
        dag.assert_observed_order(("B", "A"))
    with pytest.raises(RuntimeError, match="unknown DAG task"):
        dag.assert_observed_order(("UNKNOWN",))


def test_work_dag_contract_freezes_incremental_native_migration() -> None:
    contract = work_dag_contract()

    assert contract["version"] == "MARKORBIT_WORK_DAG_V1"
    assert contract["compatibility_policy"]["legacy_execution_may_be_mapped_to_explicit_nodes"] is True
    assert contract["compatibility_policy"]["unknown_or_ambiguous_legacy_shape_fails_closed"] is True
    assert contract["compatibility_policy"]["native_execution_is_per_node_and_incremental"] is True
    assert contract["legal_conclusion"] is False
