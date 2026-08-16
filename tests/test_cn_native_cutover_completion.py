from __future__ import annotations

from dataclasses import replace

import pytest

from app.cn import native_cutover_completion as completion
from app.cn.native_cutover_completion import (
    assert_cn_native_cutover_complete,
    cn_native_cutover_completion_contract,
)
from app.work_dag import WorkDagDefinition, WorkDagNode


def _dag_with(nodes) -> WorkDagDefinition:
    return WorkDagDefinition(
        dag_id=completion.CN_FINAL_PUBLISH_DAG.dag_id,
        version=completion.CN_FINAL_PUBLISH_DAG.version,
        nodes=nodes,
    )


def test_current_cn_final_publish_is_complete_native_cutover() -> None:
    contract = cn_native_cutover_completion_contract()

    assert contract["version"] == "CN_NATIVE_CUTOVER_COMPLETION_V1"
    assert contract["status"] == "COMPLETE"
    assert contract["total_node_count"] == 21
    assert contract["native_business_node_count"] == 18
    assert contract["intentional_compatibility_node_count"] == 3
    assert contract["native_business_node_set_frozen"] is True
    assert contract["no_executable_legacy_business_nodes_remaining"] is True
    assert contract["storage_v2_suppression_boundaries_frozen"] is True
    assert contract["intentional_compatibility_nodes"] == [
        "PARTY_HISTORY_SUPERSEDED",
        "PARTY_HISTORY_OBSERVED",
        "DERIVED_CASE_EVENT",
    ]
    assert contract["reasons"] == []
    assert assert_cn_native_cutover_complete()["status"] == "COMPLETE"


def test_completion_fails_if_expected_native_node_falls_back_to_compatibility(monkeypatch) -> None:
    nodes = [
        replace(node, native_execution=False)
        if node.task_id == "CASE_FACTS_EVENT"
        else node
        for node in completion.CN_FINAL_PUBLISH_DAG.nodes
    ]
    monkeypatch.setattr(completion, "CN_FINAL_PUBLISH_DAG", _dag_with(nodes))

    contract = cn_native_cutover_completion_contract()
    codes = {reason["code"] for reason in contract["reasons"]}
    assert contract["status"] == "INCOMPLETE"
    assert "EXPECTED_NATIVE_NODE_MISSING" in codes
    assert "UNEXPECTED_EXECUTABLE_COMPATIBILITY_NODE" in codes
    with pytest.raises(RuntimeError, match="CN native cutover completion contract failed"):
        assert_cn_native_cutover_complete()


def test_completion_fails_if_suppressed_placeholder_becomes_native(monkeypatch) -> None:
    nodes = [
        replace(node, native_execution=True)
        if node.task_id == "DERIVED_CASE_EVENT"
        else node
        for node in completion.CN_FINAL_PUBLISH_DAG.nodes
    ]
    monkeypatch.setattr(completion, "CN_FINAL_PUBLISH_DAG", _dag_with(nodes))

    contract = cn_native_cutover_completion_contract()
    codes = {reason["code"] for reason in contract["reasons"]}
    assert contract["status"] == "INCOMPLETE"
    assert "UNEXPECTED_NATIVE_NODE" in codes
    assert "INTENTIONAL_SUPPRESSION_BOUNDARY_CHANGED" in codes
    assert "SUPPRESSION_BOUNDARY_CONTRACT_DRIFT" in codes
    assert contract["storage_v2_suppression_boundaries_frozen"] is False


def test_completion_fails_if_new_legacy_business_node_is_added(monkeypatch) -> None:
    nodes = list(completion.CN_FINAL_PUBLISH_DAG.nodes)
    nodes.append(
        WorkDagNode(
            "UNEXPECTED_LEGACY_WRITE",
            "PUBLISH_CURRENT",
            "cn_unexpected_current",
            "APPLICATION_RANGE",
            audit_policy="LEGACY_UNKNOWN",
            native_execution=False,
        )
    )
    monkeypatch.setattr(completion, "CN_FINAL_PUBLISH_DAG", _dag_with(nodes))

    contract = cn_native_cutover_completion_contract()
    assert contract["status"] == "INCOMPLETE"
    assert any(
        reason["code"] == "UNEXPECTED_EXECUTABLE_COMPATIBILITY_NODE"
        and "UNEXPECTED_LEGACY_WRITE" in reason["nodes"]
        for reason in contract["reasons"]
    )
