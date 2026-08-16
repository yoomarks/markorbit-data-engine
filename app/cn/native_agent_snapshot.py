from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any
import uuid

from app.cn.native_case_relation import NativeCaseRelationCutoverClient
from app.cn.publish_dag import resolve_legacy_publish_command
from app.cn.publish_subtasks import PublishSubtaskStore


NATIVE_AGENT_SNAPSHOT_VERSION = "CN_NATIVE_AGENT_SNAPSHOT_V1"
NATIVE_AGENT_STAGE = "cn_stage_basic+cn_stage_agent"
NATIVE_AGENT_CUTOVER_VERSION = "CN_NATIVE_AGENT_CUTOVER_V1"
NATIVE_AGENT_CUTOVER_STAGE = "__native_agent_cutover_v1__"
_NATIVE_AGENT_CUTOVER_HASH = sha256(
    NATIVE_AGENT_CUTOVER_VERSION.encode("utf-8")
).hexdigest()
_AGENT_INSERT = "INSERT INTO markorbit_facts.cn_agent_current"


def _sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def native_agent_operation_hash(agent_codes: tuple[str, ...]) -> str:
    if not agent_codes:
        raise ValueError("native Agent batch must not be empty")
    payload = (
        f"{NATIVE_AGENT_SNAPSHOT_VERSION}|AGENT_CURRENT|SEMANTIC_SQL_V1|"
        + "\x1f".join(agent_codes)
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def agent_current_sql(
    package_uuid: uuid.UUID | str,
    *,
    source_rank: int,
    agent_codes: tuple[str, ...],
) -> str:
    if not agent_codes:
        raise ValueError("native Agent batch must not be empty")
    package = str(package_uuid)
    codes = ", ".join(_sql_string(code) for code in agent_codes)
    return f"""
        INSERT INTO markorbit_facts.cn_agent_current
        SELECT
            b.agent_code, b.agent_mention_id, b.agent_entity_id,
            if(argMax(a.agent_name, toUInt64(a.source_start_line)) = '', b.agent_code,
               argMax(a.agent_name, toUInt64(a.source_start_line))),
            if(argMax(a.agent_name_norm, toUInt64(a.source_start_line)) = '', lowerUTF8(b.agent_code),
               argMax(a.agent_name_norm, toUInt64(a.source_start_line))),
            argMin(b.source_file, toUInt64(b.source_start_line)),
            min(toUInt64(b.source_start_line)),
            max(toUInt64(b.source_end_line)),
            hex(SHA256(arrayStringConcat(arraySort(groupArray(toString(b.row_hash))), '|'))),
            toUUID('{package}'), {int(source_rank)}, now64(3), 0
        FROM
        (
            SELECT *
            FROM markorbit_facts.cn_stage_basic
            WHERE package_id = toUUID('{package}')
              AND agent_code IN ({codes})
        ) AS b
        LEFT JOIN
        (
            SELECT *
            FROM markorbit_facts.cn_stage_agent
            WHERE package_id = toUUID('{package}')
              AND agent_code IN ({codes})
        ) AS a
          ON a.package_id = b.package_id AND a.agent_code = b.agent_code
        GROUP BY b.agent_code, b.agent_mention_id, b.agent_entity_id
    """


@dataclass(frozen=True)
class NativeAgentExecutionResult:
    batch_count: int
    agent_code_count: int
    executed: int
    skipped: int


class NativeAgentSnapshotExecutor:
    """Native whole-agent-code batch publisher with durable resume semantics."""

    def __init__(
        self,
        *,
        client: Any,
        package_uuid: uuid.UUID | str,
        source_rank: int,
        agent_batches: list[tuple[str, ...]],
        subtask_store: PublishSubtaskStore,
    ) -> None:
        self.client = client
        self.package_id = str(package_uuid)
        self.source_rank = int(source_rank)
        self.agent_batches = [tuple(batch) for batch in agent_batches]
        self.subtask_store = subtask_store
        self._result: NativeAgentExecutionResult | None = None

    def execute(self) -> NativeAgentExecutionResult:
        if self._result is not None:
            raise RuntimeError("native AGENT_CURRENT emitted more than once")

        executed = 0
        skipped = 0
        total = len(self.agent_batches)
        for index, batch in enumerate(self.agent_batches, start=1):
            if not batch:
                raise RuntimeError(f"native AGENT_CURRENT batch {index}/{total} is empty")
            operation_hash = native_agent_operation_hash(batch)
            task_key = self.subtask_store.task_key(
                sql_hash=operation_hash,
                stage_table=NATIVE_AGENT_STAGE,
                lower=batch[0],
                upper=batch[-1],
            )
            if self.subtask_store.is_success(task_key, operation_hash):
                skipped += 1
                continue

            self.subtask_store.mark_running(
                task_key=task_key,
                task_group="AGENT_CURRENT",
                task_index=index,
                task_total=total,
                stage_table=NATIVE_AGENT_STAGE,
                lower=batch[0],
                upper=batch[-1],
                sql_hash=operation_hash,
            )
            try:
                self.client.command(
                    agent_current_sql(
                        self.package_id,
                        source_rank=self.source_rank,
                        agent_codes=batch,
                    )
                )
            except Exception as exc:
                self.subtask_store.mark_failed(task_key, str(exc))
                raise RuntimeError(
                    "native_publish_subphase=AGENT_CURRENT "
                    f"batch={index}/{total} agent_codes={len(batch)} "
                    f"range=[{batch[0]},{batch[-1]}] failed: {exc}"
                ) from exc
            self.subtask_store.mark_success(task_key)
            executed += 1

        self._result = NativeAgentExecutionResult(
            batch_count=total,
            agent_code_count=sum(len(batch) for batch in self.agent_batches),
            executed=executed,
            skipped=skipped,
        )
        return self._result

    def assert_complete(self) -> dict[str, int]:
        if self._result is None:
            raise RuntimeError("native AGENT_CURRENT was enabled but never observed")
        return {
            "batches": self._result.batch_count,
            "agent_codes": self._result.agent_code_count,
            "executed": self._result.executed,
            "skipped": self._result.skipped,
        }


class NativeAgentCutoverClient:
    """Native Agent cutover plus downstream per-node native compatibility stack."""

    def __init__(
        self,
        delegate: Any,
        *,
        execution_client: Any,
        package_uuid: uuid.UUID | str,
        source_rank: int,
        agent_batches: list[tuple[str, ...]],
        subtask_store: PublishSubtaskStore,
        allow_new_cutover: bool,
    ) -> None:
        self._delegate = NativeCaseRelationCutoverClient(
            delegate,
            execution_client=execution_client,
            package_uuid=package_uuid,
            source_rank=source_rank,
            subtask_store=subtask_store,
            allow_new_cutover=allow_new_cutover,
        )
        self._execution_client = execution_client
        self._package_id = str(package_uuid)
        self._source_rank = int(source_rank)
        self._agent_batches = [tuple(batch) for batch in agent_batches]
        self._subtask_store = subtask_store
        self._executor: NativeAgentSnapshotExecutor | None = None
        self._native_agent_enabled = self._initialize_cutover(
            allow_new_cutover=bool(allow_new_cutover)
        )
        self._native_agent_executed = 0
        self._native_agent_skipped = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    @property
    def native_agent_enabled(self) -> bool:
        return self._native_agent_enabled

    @property
    def final_tasks_executed(self) -> int:
        return int(self._delegate.final_tasks_executed) + self._native_agent_executed

    @property
    def final_tasks_skipped(self) -> int:
        return int(self._delegate.final_tasks_skipped) + self._native_agent_skipped

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if _AGENT_INSERT not in sql or not self._native_agent_enabled:
            return self._delegate.command(sql, *args, **kwargs)

        node = resolve_legacy_publish_command(sql)
        if node is None or node.task_id != "AGENT_CURRENT":
            resolved = node.task_id if node is not None else "NONE"
            raise RuntimeError(
                "native Agent cutover received unexpected legacy sequencing shape: "
                f"expected=AGENT_CURRENT, resolved={resolved}"
            )
        if self._executor is not None:
            raise RuntimeError("native AGENT_CURRENT sequencing placeholder emitted twice")

        self._executor = NativeAgentSnapshotExecutor(
            client=self._execution_client,
            package_uuid=self._package_id,
            source_rank=self._source_rank,
            agent_batches=self._agent_batches,
            subtask_store=self._subtask_store,
        )
        result = self._executor.execute()
        self._native_agent_executed += result.executed
        self._native_agent_skipped += result.skipped
        return result

    def assert_agent_persist_complete(self) -> None:
        if not self._native_agent_enabled:
            self._delegate.assert_agent_persist_complete()
            return
        if self._executor is None:
            raise RuntimeError("native AGENT_CURRENT was enabled but never observed")
        self._executor.assert_complete()
        self._delegate.assert_aux_persist_complete()

    def _initialize_cutover(self, *, allow_new_cutover: bool) -> bool:
        marker_key = self._subtask_store.task_key(
            sql_hash=_NATIVE_AGENT_CUTOVER_HASH,
            stage_table=NATIVE_AGENT_CUTOVER_STAGE,
            lower=None,
            upper=None,
        )
        if self._subtask_store.is_success(marker_key, _NATIVE_AGENT_CUTOVER_HASH):
            return True

        task_status = getattr(self._subtask_store, "task_status", None)
        if callable(task_status):
            status = task_status(marker_key, _NATIVE_AGENT_CUTOVER_HASH)
            if status in {"RUNNING", "FAILED"}:
                self._write_cutover_marker(marker_key)
                return True

        if not allow_new_cutover:
            return False

        self._write_cutover_marker(marker_key)
        return True

    def _write_cutover_marker(self, marker_key: str) -> None:
        self._subtask_store.mark_running(
            task_key=marker_key,
            task_group="NATIVE_AGENT_CUTOVER",
            task_index=1,
            task_total=1,
            stage_table=NATIVE_AGENT_CUTOVER_STAGE,
            lower=None,
            upper=None,
            sql_hash=_NATIVE_AGENT_CUTOVER_HASH,
        )
        self._subtask_store.mark_success(marker_key)


def native_agent_snapshot_contract() -> dict[str, Any]:
    return {
        "version": NATIVE_AGENT_SNAPSHOT_VERSION,
        "cutover_version": NATIVE_AGENT_CUTOVER_VERSION,
        "native_node": "AGENT_CURRENT",
        "partition": "WHOLE_AGENT_CODE_BATCH",
        "durable_resume": True,
        "legacy_sql_rewrite": False,
        "native_execution_bypasses_legacy_interceptor": True,
        "source_rank_semantics": "UNCHANGED",
        "lineage_hash_semantics": "UNCHANGED",
        "batch_identity_includes_complete_agent_code_list": True,
        "preexisting_checkpoint_policy": "KEEP_LEGACY_AGENT_UNLESS_MARKER_ALREADY_PRESENT",
    }
