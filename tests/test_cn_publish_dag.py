from __future__ import annotations

from datetime import date
import uuid

import pytest

from app.cn import ingest as legacy
from app.cn import storage_v2_events as events
from app.cn.publish_dag import (
    CN_FINAL_PUBLISH_DAG,
    CN_FINAL_PUBLISH_DAG_VERSION,
    cn_final_publish_dag_contract,
    resolve_legacy_publish_command,
)


class _Result:
    result_rows = [(1, 1, 1, 0, 1, 0)]


class _CaptureClient:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def command(self, sql: str, *args, **kwargs):
        self.commands.append(sql)
        return None

    def query(self, sql: str, *args, **kwargs):
        return _Result()


def _stage_sql(table: str, package: str) -> str:
    return (
        f"SELECT * FROM markorbit_facts.{table} "
        f"WHERE package_id = toUUID('{package}')"
    )


def test_complete_m16_legacy_publisher_maps_one_to_one_to_explicit_dag(monkeypatch) -> None:
    package = uuid.uuid4()
    package_text = str(package)
    client = _CaptureClient()

    monkeypatch.setattr(legacy, "clickhouse_client", lambda: client)
    monkeypatch.setattr(
        legacy,
        "_case_aggregate_sql",
        lambda value: _stage_sql("cn_stage_case_publish", value),
    )
    monkeypatch.setattr(
        legacy,
        "_scope_aggregate_sql",
        lambda value: _stage_sql("cn_stage_scope_publish", value),
    )
    monkeypatch.setattr(
        legacy,
        "_party_aggregate_sql",
        lambda value: _stage_sql("cn_stage_party_publish", value),
    )
    monkeypatch.setattr(legacy, "_insert_case_events", events.insert_case_delta_events)

    legacy._publish(
        package,
        {
            "package_kind": "MONTHLY_PATCH",
            "source_rank": 123456,
            "source_period_end": date(2026, 1, 31),
        },
    )

    resolved = [resolve_legacy_publish_command(sql) for sql in client.commands]
    resolved_nodes = [node for node in resolved if node is not None]
    task_ids = tuple(node.task_id for node in resolved_nodes)

    assert len(task_ids) == len(CN_FINAL_PUBLISH_DAG.nodes)
    assert set(task_ids) == {node.task_id for node in CN_FINAL_PUBLISH_DAG.nodes}
    assert task_ids == CN_FINAL_PUBLISH_DAG.topological_order()
    CN_FINAL_PUBLISH_DAG.assert_observed_order(task_ids)
    assert package_text in "\n".join(client.commands)


def test_resolver_rejects_unknown_publish_shape_with_known_stage() -> None:
    sql = (
        "INSERT INTO markorbit_facts.cn_unknown_current "
        "SELECT * FROM markorbit_facts.cn_stage_case_publish"
    )
    with pytest.raises(RuntimeError, match="does not map to exactly one explicit DAG node"):
        resolve_legacy_publish_command(sql)


def test_resolver_rejects_mixed_stage_shape() -> None:
    sql = (
        "INSERT INTO markorbit_facts.cn_case_current "
        "SELECT * FROM markorbit_facts.cn_stage_case_publish "
        "JOIN markorbit_facts.cn_stage_party_publish USING application_number"
    )
    with pytest.raises(RuntimeError, match="mixes source tables"):
        resolve_legacy_publish_command(sql)


def test_cn_publish_dag_contract_marks_fifteen_native_nodes() -> None:
    contract = cn_final_publish_dag_contract()

    assert contract["dag_version"] == CN_FINAL_PUBLISH_DAG_VERSION
    assert contract["execution_mode"] == "HYBRID_NATIVE_WITH_INFLIGHT_LEGACY_COMPATIBILITY"
    assert contract["native_node_count"] == 15
    assert contract["compatibility_node_count"] == len(CN_FINAL_PUBLISH_DAG.nodes) - 15
    assert contract["legacy_rule_count"] == len(CN_FINAL_PUBLISH_DAG.nodes)

    nodes = {node["task_id"]: node for node in contract["nodes"]}
    assert nodes["CASE_FACTS_EVENT"]["native_execution"] is True
    assert nodes["PRELIMINARY_PUBLICATION_EVENT"]["native_execution"] is True
    assert nodes["REGISTRATION_PUBLICATION_EVENT"]["native_execution"] is True
    assert nodes["EXCLUSIVE_TERM_EVENT"]["native_execution"] is True
    assert nodes["MARK_NAME_EVENT"]["native_execution"] is True
    assert nodes["AGENT_CODE_EVENT"]["native_execution"] is True
    assert nodes["CASE_CURRENT"]["native_execution"] is True
    assert nodes["CASE_PARTY_CURRENT"]["native_execution"] is True
    assert nodes["CASE_PARTY_CURRENT_CLOSE"]["native_execution"] is True
    assert nodes["CASE_SCOPE_CURRENT"]["native_execution"] is True
    assert nodes["AGENT_CURRENT"]["native_execution"] is True
    assert nodes["PRIORITY_CURRENT"]["native_execution"] is True
    assert nodes["MADRID_CURRENT"]["native_execution"] is True
    assert nodes["CASE_RELATION_CURRENT"]["native_execution"] is True
    assert nodes["SCOPE_CARVE_OUT_CURRENT"]["native_execution"] is True
    assert (
        contract["inflight_compatibility_policy"]
        == "VERSIONED_PER_NODE_CUTOVER_MARKERS_PRESERVE_PREEXISTING_CHECKPOINT_EXECUTION"
    )
