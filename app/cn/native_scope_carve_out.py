from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any
import uuid

from app.cn.publish_dag import resolve_legacy_publish_command
from app.cn.publish_subtasks import PublishSubtaskStore


NATIVE_SCOPE_CARVE_OUT_VERSION = "CN_NATIVE_SCOPE_CARVE_OUT_V1"
NATIVE_SCOPE_CARVE_OUT_CUTOVER_VERSION = "CN_NATIVE_SCOPE_CARVE_OUT_CUTOVER_V1"
NATIVE_SCOPE_CARVE_OUT_STAGE = "cn_stage_scope_publish"
NATIVE_SCOPE_CARVE_OUT_CUTOVER_STAGE = "__native_scope_carve_out_cutover_v1__"
NATIVE_SCOPE_CARVE_OUT_TARGET_ROWS = 25_000
_SCOPE_INSERT = "INSERT INTO markorbit_facts.cn_scope_carve_out_current"
_SCOPE_OPERATION_HASH = sha256(
    f"{NATIVE_SCOPE_CARVE_OUT_VERSION}|SCOPE_CARVE_OUT_CURRENT|SEMANTIC_SQL_V1".encode(
        "utf-8"
    )
).hexdigest()
_SCOPE_CUTOVER_HASH = sha256(
    NATIVE_SCOPE_CARVE_OUT_CUTOVER_VERSION.encode("utf-8")
).hexdigest()


def _sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _range_predicate(
    lower: str | None,
    upper: str | None,
    *,
    column: str,
) -> str:
    parts: list[str] = []
    if lower is not None:
        parts.append(f"{column} >= {_sql_string(lower)}")
    if upper is not None:
        parts.append(f"{column} < {_sql_string(upper)}")
    return " AND ".join(parts)


def plan_scope_carve_out_ranges(
    package_uuid: uuid.UUID | str,
    *,
    client: Any,
    target_rows: int = NATIVE_SCOPE_CARVE_OUT_TARGET_ROWS,
) -> list[tuple[str | None, str | None]]:
    """Plan half-open whole-application ranges over target scope stage rows."""
    if target_rows < 1:
        raise ValueError("target_rows must be positive")
    package = str(package_uuid)
    ranges: list[tuple[str | None, str | None]] = []
    lower: str | None = None
    while True:
        lower_filter = ""
        if lower is not None:
            lower_filter = f" AND application_number >= {_sql_string(lower)}"
        rows = client.query(
            f"""
            SELECT application_number
            FROM markorbit_facts.{NATIVE_SCOPE_CARVE_OUT_STAGE}
            WHERE package_id = toUUID('{package}'){lower_filter}
            ORDER BY application_number
            LIMIT 1 OFFSET {int(target_rows)}
            """
        ).result_rows
        if not rows:
            ranges.append((lower, None))
            break

        boundary = str(rows[0][0])
        if lower is not None and boundary <= lower:
            next_rows = client.query(
                f"""
                SELECT application_number
                FROM markorbit_facts.{NATIVE_SCOPE_CARVE_OUT_STAGE}
                WHERE package_id = toUUID('{package}')
                  AND application_number > {_sql_string(lower)}
                ORDER BY application_number
                LIMIT 1
                """
            ).result_rows
            if not next_rows:
                ranges.append((lower, None))
                break
            boundary = str(next_rows[0][0])
        ranges.append((lower, boundary))
        lower = boundary
    return ranges


def scope_carve_out_current_sql(
    package_uuid: uuid.UUID | str,
    *,
    source_rank: int,
    lower: str | None,
    upper: str | None,
) -> str:
    package = str(package_uuid)
    relation_range = _range_predicate(
        lower,
        upper,
        column="target_application_number",
    )
    target_range = _range_predicate(lower, upper, column="application_number")
    relation_filter = f" AND {relation_range}" if relation_range else ""
    target_filter = f" AND {target_range}" if target_range else ""
    return f"""
        INSERT INTO markorbit_facts.cn_scope_carve_out_current
        SELECT
            generateUUIDv4(), relation.relation_id,
            relation.source_application_number, relation.target_application_number,
            target.class_no, 'UNKNOWN', ifNull(source.scope_hash, ''),
            target.scope_hash,
            if(source.application_number = '', 'TARGET_SCOPE_ONLY',
               'ROOT_AND_TARGET_SCOPE_OBSERVED'),
            if(source.application_number = '', 0.55, 0.75),
            toUUID('{package}'), target.source_file, target.source_first_line,
            target.source_last_line, target.source_row_hash,
            hex(SHA256(concat(
                relation.source_application_number, '|',
                relation.target_application_number, '|', toString(target.class_no), '|',
                ifNull(source.scope_hash, ''), '|', target.scope_hash
            ))), {int(source_rank)}, now64(3), 0
        FROM
        (
            SELECT *
            FROM markorbit_facts.cn_case_relation_current FINAL
            WHERE source_package_id = toUUID('{package}'){relation_filter}
        ) AS relation
        INNER JOIN
        (
            SELECT *
            FROM markorbit_facts.cn_stage_scope_publish
            WHERE package_id = toUUID('{package}'){target_filter}
        ) AS target
          ON target.application_number = relation.target_application_number
        LEFT JOIN
        (
            SELECT *
            FROM markorbit_facts.cn_case_scope_current FINAL
            WHERE application_number IN
            (
                SELECT DISTINCT source_application_number
                FROM markorbit_facts.cn_case_relation_current FINAL
                WHERE source_package_id = toUUID('{package}'){relation_filter}
            )
        ) AS source
          ON source.application_number = relation.source_application_number
         AND source.class_no = target.class_no
    """


@dataclass(frozen=True)
class NativeScopeCarveOutExecutionResult:
    range_count: int
    executed: int
    skipped: int


class NativeScopeCarveOutExecutor:
    """Native bounded scope-carve-out publisher with durable resume semantics."""

    def __init__(
        self,
        *,
        client: Any,
        package_uuid: uuid.UUID | str,
        source_rank: int,
        subtask_store: PublishSubtaskStore,
        target_rows: int = NATIVE_SCOPE_CARVE_OUT_TARGET_ROWS,
    ) -> None:
        if target_rows < 1:
            raise ValueError("target_rows must be positive")
        self.client = client
        self.package_id = str(package_uuid)
        self.source_rank = int(source_rank)
        self.subtask_store = subtask_store
        self.target_rows = int(target_rows)
        self._result: NativeScopeCarveOutExecutionResult | None = None

    def execute(self) -> NativeScopeCarveOutExecutionResult:
        if self._result is not None:
            raise RuntimeError("native SCOPE_CARVE_OUT_CURRENT emitted more than once")
        try:
            ranges = plan_scope_carve_out_ranges(
                self.package_id,
                client=self.client,
                target_rows=self.target_rows,
            )
        except Exception as exc:
            raise RuntimeError(
                f"native_publish_subphase=SCOPE_CARVE_OUT_CURRENT_PLAN failed: {exc}"
            ) from exc

        executed = 0
        skipped = 0
        total = len(ranges)
        for index, (lower, upper) in enumerate(ranges, start=1):
            task_key = self.subtask_store.task_key(
                sql_hash=_SCOPE_OPERATION_HASH,
                stage_table=NATIVE_SCOPE_CARVE_OUT_STAGE,
                lower=lower,
                upper=upper,
            )
            if self.subtask_store.is_success(task_key, _SCOPE_OPERATION_HASH):
                skipped += 1
                continue

            self.subtask_store.mark_running(
                task_key=task_key,
                task_group="SCOPE_CARVE_OUT_CURRENT",
                task_index=index,
                task_total=total,
                stage_table=NATIVE_SCOPE_CARVE_OUT_STAGE,
                lower=lower,
                upper=upper,
                sql_hash=_SCOPE_OPERATION_HASH,
            )
            try:
                self.client.command(
                    scope_carve_out_current_sql(
                        self.package_id,
                        source_rank=self.source_rank,
                        lower=lower,
                        upper=upper,
                    )
                )
            except Exception as exc:
                self.subtask_store.mark_failed(task_key, str(exc))
                raise RuntimeError(
                    "native_publish_subphase=SCOPE_CARVE_OUT_CURRENT "
                    f"task={index}/{total} range=[{lower or '-inf'},{upper or '+inf'}) "
                    f"failed: {exc}"
                ) from exc
            self.subtask_store.mark_success(task_key)
            executed += 1

        self._result = NativeScopeCarveOutExecutionResult(
            range_count=total,
            executed=executed,
            skipped=skipped,
        )
        return self._result

    def assert_complete(self) -> dict[str, int]:
        if self._result is None:
            raise RuntimeError("native SCOPE_CARVE_OUT_CURRENT was enabled but never observed")
        return {
            "ranges": self._result.range_count,
            "executed": self._result.executed,
            "skipped": self._result.skipped,
        }


class NativeScopeCarveOutCutoverClient:
    """Versioned per-node cutover for SCOPE_CARVE_OUT_CURRENT."""

    def __init__(
        self,
        delegate: Any,
        *,
        execution_client: Any,
        package_uuid: uuid.UUID | str,
        source_rank: int,
        subtask_store: PublishSubtaskStore,
        allow_new_cutover: bool,
        target_rows: int = NATIVE_SCOPE_CARVE_OUT_TARGET_ROWS,
    ) -> None:
        self._delegate = delegate
        self._execution_client = execution_client
        self._package_id = str(package_uuid)
        self._source_rank = int(source_rank)
        self._subtask_store = subtask_store
        self._target_rows = int(target_rows)
        if self._target_rows < 1:
            raise ValueError("target_rows must be positive")
        self._executor: NativeScopeCarveOutExecutor | None = None
        self._native_enabled = self._initialize_cutover(
            allow_new_cutover=bool(allow_new_cutover)
        )
        self._executed = 0
        self._skipped = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    @property
    def native_scope_carve_out_enabled(self) -> bool:
        return self._native_enabled

    @property
    def final_tasks_executed(self) -> int:
        return int(self._delegate.final_tasks_executed) + self._executed

    @property
    def final_tasks_skipped(self) -> int:
        return int(self._delegate.final_tasks_skipped) + self._skipped

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if _SCOPE_INSERT not in sql or not self._native_enabled:
            return self._delegate.command(sql, *args, **kwargs)

        node = resolve_legacy_publish_command(sql)
        if node is None or node.task_id != "SCOPE_CARVE_OUT_CURRENT":
            resolved = node.task_id if node is not None else "NONE"
            raise RuntimeError(
                "native scope-carve-out cutover received unexpected sequencing shape: "
                f"expected=SCOPE_CARVE_OUT_CURRENT, resolved={resolved}"
            )
        if self._executor is not None:
            raise RuntimeError("native SCOPE_CARVE_OUT_CURRENT placeholder emitted twice")

        self._executor = NativeScopeCarveOutExecutor(
            client=self._execution_client,
            package_uuid=self._package_id,
            source_rank=self._source_rank,
            subtask_store=self._subtask_store,
            target_rows=self._target_rows,
        )
        result = self._executor.execute()
        self._executed += result.executed
        self._skipped += result.skipped
        return result

    def assert_scope_carve_out_persist_complete(self) -> None:
        if not self._native_enabled:
            return
        if self._executor is None:
            raise RuntimeError("native SCOPE_CARVE_OUT_CURRENT was enabled but never observed")
        self._executor.assert_complete()

    def assert_final_publish_complete(self) -> dict[str, int]:
        self.assert_scope_carve_out_persist_complete()
        return self._delegate.assert_final_publish_complete()

    def _initialize_cutover(self, *, allow_new_cutover: bool) -> bool:
        marker_key = self._subtask_store.task_key(
            sql_hash=_SCOPE_CUTOVER_HASH,
            stage_table=NATIVE_SCOPE_CARVE_OUT_CUTOVER_STAGE,
            lower=None,
            upper=None,
        )
        if self._subtask_store.is_success(marker_key, _SCOPE_CUTOVER_HASH):
            return True

        task_status = getattr(self._subtask_store, "task_status", None)
        if callable(task_status):
            status = task_status(marker_key, _SCOPE_CUTOVER_HASH)
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
            task_group="NATIVE_SCOPE_CARVE_OUT_CUTOVER",
            task_index=1,
            task_total=1,
            stage_table=NATIVE_SCOPE_CARVE_OUT_CUTOVER_STAGE,
            lower=None,
            upper=None,
            sql_hash=_SCOPE_CUTOVER_HASH,
        )
        self._subtask_store.mark_success(marker_key)


def native_scope_carve_out_contract() -> dict[str, Any]:
    return {
        "version": NATIVE_SCOPE_CARVE_OUT_VERSION,
        "cutover_version": NATIVE_SCOPE_CARVE_OUT_CUTOVER_VERSION,
        "native_node": "SCOPE_CARVE_OUT_CURRENT",
        "partition": "WHOLE_TARGET_APPLICATION_HALF_OPEN_RANGE",
        "target_rows": NATIVE_SCOPE_CARVE_OUT_TARGET_ROWS,
        "durable_resume": True,
        "legacy_sql_rewrite": False,
        "native_execution_bypasses_legacy_interceptor": True,
        "relation_current_filter": "PACKAGE_AND_TARGET_RANGE",
        "source_scope_filter": "EXACT_RELATION_ROOT_APPLICATION_SET",
        "source_rank_semantics": "UNCHANGED",
        "lineage_hash_semantics": "UNCHANGED",
        "preexisting_checkpoint_policy": "KEEP_LEGACY_SCOPE_CARVE_OUT_UNLESS_MARKER_PRESENT",
    }
