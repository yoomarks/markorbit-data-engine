from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


WORK_DAG_VERSION = "MARKORBIT_WORK_DAG_V1"


@dataclass(frozen=True)
class WorkDagNode:
    task_id: str
    operation_kind: str
    target: str
    partition_kind: str
    dependencies: tuple[str, ...] = ()
    stage_table: str = ""
    audit_policy: str = ""
    native_execution: bool = False


class WorkDagDefinition:
    def __init__(self, *, dag_id: str, version: str, nodes: Iterable[WorkDagNode]) -> None:
        self.dag_id = dag_id
        self.version = version
        self.nodes = tuple(nodes)
        self._by_id = {node.task_id: node for node in self.nodes}
        self.validate()

    def node(self, task_id: str) -> WorkDagNode:
        try:
            return self._by_id[task_id]
        except KeyError as exc:
            raise KeyError(f"unknown DAG task: {task_id}") from exc

    def validate(self) -> None:
        if not self.dag_id.strip() or not self.version.strip():
            raise ValueError("DAG id and version are required")
        if len(self._by_id) != len(self.nodes):
            raise ValueError("DAG task_id values must be unique")

        for node in self.nodes:
            if not node.task_id.strip():
                raise ValueError("DAG task_id is required")
            if not node.operation_kind.strip() or not node.target.strip():
                raise ValueError(f"DAG task {node.task_id} is missing operation metadata")
            for dependency in node.dependencies:
                if dependency == node.task_id:
                    raise ValueError(f"DAG task {node.task_id} cannot depend on itself")
                if dependency not in self._by_id:
                    raise ValueError(
                        f"DAG task {node.task_id} depends on unknown task {dependency}"
                    )

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visited:
                return
            if task_id in visiting:
                raise ValueError(f"DAG dependency cycle detected at {task_id}")
            visiting.add(task_id)
            for dependency in self._by_id[task_id].dependencies:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for node in self.nodes:
            visit(node.task_id)

    def topological_order(self) -> tuple[str, ...]:
        ordered: list[str] = []
        visited: set[str] = set()

        def append(task_id: str) -> None:
            if task_id in visited:
                return
            for dependency in self._by_id[task_id].dependencies:
                append(dependency)
            visited.add(task_id)
            ordered.append(task_id)

        for node in self.nodes:
            append(node.task_id)
        return tuple(ordered)

    def assert_observed_order(self, task_ids: Iterable[str]) -> None:
        """Fail closed if an observed task runs before an observed dependency.

        Missing dependencies are allowed because compatibility adapters may suppress
        a legacy operation entirely (for example a deprecated history sink). Once
        both nodes are observed, however, dependency order is mandatory.
        """
        positions: dict[str, int] = {}
        for index, task_id in enumerate(task_ids):
            if task_id not in self._by_id:
                raise RuntimeError(f"execution observed unknown DAG task: {task_id}")
            positions.setdefault(task_id, index)
        for task_id, position in positions.items():
            for dependency in self._by_id[task_id].dependencies:
                dependency_position = positions.get(dependency)
                if dependency_position is not None and dependency_position > position:
                    raise RuntimeError(
                        f"DAG order violation: {task_id} ran before dependency {dependency}"
                    )

    def contract(self) -> dict[str, Any]:
        return {
            "work_dag_version": WORK_DAG_VERSION,
            "dag_id": self.dag_id,
            "dag_version": self.version,
            "nodes": [
                {
                    "task_id": node.task_id,
                    "operation_kind": node.operation_kind,
                    "target": node.target,
                    "partition_kind": node.partition_kind,
                    "dependencies": list(node.dependencies),
                    "stage_table": node.stage_table,
                    "audit_policy": node.audit_policy,
                    "native_execution": node.native_execution,
                }
                for node in self.nodes
            ],
        }


def work_dag_contract() -> dict[str, Any]:
    return {
        "version": WORK_DAG_VERSION,
        "role": "EXPLICIT_DEPENDENCY_GRAPH_FOR_DURABLE_ENGINE_WORK",
        "required_node_fields": [
            "task_id",
            "operation_kind",
            "target",
            "partition_kind",
            "dependencies",
            "audit_policy",
        ],
        "compatibility_policy": {
            "legacy_execution_may_be_mapped_to_explicit_nodes": True,
            "unknown_or_ambiguous_legacy_shape_fails_closed": True,
            "native_execution_is_per_node_and_incremental": True,
        },
        "legal_conclusion": False,
    }
