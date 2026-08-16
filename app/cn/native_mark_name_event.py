from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any
import uuid

from app.cn.publish_dag import resolve_legacy_publish_command
from app.cn.publish_subtasks import PublishSubtaskStore
from app.repository import get_package


NATIVE_MARK_NAME_EVENT_VERSION = "CN_NATIVE_MARK_NAME_EVENT_V1"
NATIVE_MARK_NAME_EVENT_CUTOVER_VERSION = "CN_NATIVE_MARK_NAME_EVENT_CUTOVER_V1"
NATIVE_MARK_NAME_EVENT_STAGE = "cn_stage_case_publish"
NATIVE_MARK_NAME_EVENT_CUTOVER_STAGE = "__native_mark_name_event_cutover_v1__"
NATIVE_MARK_NAME_EVENT_TARGET_ROWS = 25_000
_EVENT_INSERT = "INSERT INTO markorbit_facts.cn_observed_event"
_EVENT_MARKER = "MARK_NAME_CHANGED_OBSERVED"
_EVENT_OPERATION_HASH = sha256(
    f"{NATIVE_MARK_NAME_EVENT_VERSION}|MARK_NAME_EVENT|SEMANTIC_SQL_V1".encode("utf-8")
).hexdigest()
_EVENT_CUTOVER_HASH = sha256(NATIVE_MARK_NAME_EVENT_CUTOVER_VERSION.encode("utf-8")).hexdigest()


def _sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _range_predicate(lower: str | None, upper: str | None, *, column: str) -> str:
    parts: list[str] = []
    if lower is not None:
        parts.append(f"{column} >= {_sql_string(lower)}")
    if upper is not None:
        parts.append(f"{column} < {_sql_string(upper)}")
    return " AND ".join(parts)


def plan_mark_name_event_ranges(
    package_uuid: uuid.UUID | str,
    *,
    client: Any,
    target_rows: int = NATIVE_MARK_NAME_EVENT_TARGET_ROWS,
) -> list[tuple[str | None, str | None]]:
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
            FROM markorbit_facts.{NATIVE_MARK_NAME_EVENT_STAGE}
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
                FROM markorbit_facts.{NATIVE_MARK_NAME_EVENT_STAGE}
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


def mark_name_event_sql(
    package_uuid: uuid.UUID | str,
    *,
    package_kind: str,
    source_rank: int,
    lower: str | None,
    upper: str | None,
) -> str:
    package = str(package_uuid)
    kind = package_kind.replace("\\", "\\\\").replace("'", "\\'")
    incoming_range = _range_predicate(lower, upper, column="application_number")
    incoming_filter = f" AND {incoming_range}" if incoming_range else ""
    return f"""
        INSERT INTO markorbit_facts.cn_observed_event
        SELECT
            generateUUIDv4(), incoming.case_id, incoming.application_number,
            'MARK_NAME_CHANGED_OBSERVED', CAST(NULL, 'Nullable(Date32)'), now64(3), 'CASE',
            CAST(NULL, 'Nullable(UInt8)'), 'mark_name',
            cur.mark_name_raw, incoming.mark_name_raw, 'OFFICIAL_FACT_OBSERVATION',
            'NOT_DETERMINED', 1.0, toUUID('{package}'), '{kind}',
            incoming.source_file, incoming.source_first_line, incoming.source_last_line,
            incoming.source_row_hash, {int(source_rank)},
            hex(SHA256(concat(
                incoming.application_number, '|', 'MARK_NAME_CHANGED_OBSERVED', '|',
                'mark_name', '|', cur.mark_name_raw, '|', incoming.mark_name_raw, '|',
                toString({int(source_rank)})
            )))
        FROM
        (
            SELECT *
            FROM markorbit_facts.cn_stage_case_publish
            WHERE package_id = toUUID('{package}'){incoming_filter}
        ) AS incoming
        INNER JOIN
        (
            SELECT *
            FROM markorbit_facts.cn_case_current FINAL
            WHERE application_number IN
            (
                SELECT application_number
                FROM markorbit_facts.cn_stage_case_publish
                WHERE package_id = toUUID('{package}'){incoming_filter}
            )
        ) AS cur
          ON cur.application_number = incoming.application_number
        WHERE cur.source_rank < {int(source_rank)}
          AND cur.mark_name_raw != incoming.mark_name_raw
    """


@dataclass(frozen=True)
class NativeMarkNameEventExecutionResult:
    range_count: int
    executed: int
    skipped: int


class NativeMarkNameEventExecutor:
    def __init__(
        self,
        *,
        client: Any,
        package_uuid: uuid.UUID | str,
        package_kind: str,
        source_rank: int,
        subtask_store: PublishSubtaskStore,
        target_rows: int = NATIVE_MARK_NAME_EVENT_TARGET_ROWS,
    ) -> None:
        if target_rows < 1:
            raise ValueError("target_rows must be positive")
        self.client = client
        self.package_id = str(package_uuid)
        self.package_kind = str(package_kind)
        self.source_rank = int(source_rank)
        self.subtask_store = subtask_store
        self.target_rows = int(target_rows)
        self._result: NativeMarkNameEventExecutionResult | None = None

    def execute(self) -> NativeMarkNameEventExecutionResult:
        if self._result is not None:
            raise RuntimeError("native MARK_NAME_EVENT emitted more than once")
        try:
            ranges = plan_mark_name_event_ranges(
                self.package_id,
                client=self.client,
                target_rows=self.target_rows,
            )
        except Exception as exc:
            raise RuntimeError(f"native_publish_subphase=MARK_NAME_EVENT_PLAN failed: {exc}") from exc

        executed = 0
        skipped = 0
        total = len(ranges)
        for index, (lower, upper) in enumerate(ranges, start=1):
            task_key = self.subtask_store.task_key(
                sql_hash=_EVENT_OPERATION_HASH,
                stage_table=NATIVE_MARK_NAME_EVENT_STAGE,
                lower=lower,
                upper=upper,
            )
            if self.subtask_store.is_success(task_key, _EVENT_OPERATION_HASH):
                skipped += 1
                continue
            self.subtask_store.mark_running(
                task_key=task_key,
                task_group="MARK_NAME_EVENT",
                task_index=index,
                task_total=total,
                stage_table=NATIVE_MARK_NAME_EVENT_STAGE,
                lower=lower,
                upper=upper,
                sql_hash=_EVENT_OPERATION_HASH,
            )
            try:
                self.client.command(
                    mark_name_event_sql(
                        self.package_id,
                        package_kind=self.package_kind,
                        source_rank=self.source_rank,
                        lower=lower,
                        upper=upper,
                    )
                )
            except Exception as exc:
                self.subtask_store.mark_failed(task_key, str(exc))
                raise RuntimeError(
                    "native_publish_subphase=MARK_NAME_EVENT "
                    f"task={index}/{total} range=[{lower or '-inf'},{upper or '+inf'}) "
                    f"failed: {exc}"
                ) from exc
            self.subtask_store.mark_success(task_key)
            executed += 1

        self._result = NativeMarkNameEventExecutionResult(total, executed, skipped)
        return self._result

    def assert_complete(self) -> dict[str, int]:
        if self._result is None:
            raise RuntimeError("native MARK_NAME_EVENT was enabled but never observed")
        return {
            "ranges": self._result.range_count,
            "executed": self._result.executed,
            "skipped": self._result.skipped,
        }


class NativeMarkNameEventCutoverClient:
    """Versioned native cutover for MARK_NAME_CHANGED_OBSERVED delta events."""

    def __init__(
        self,
        delegate: Any,
        *,
        execution_client: Any,
        package_uuid: uuid.UUID | str,
        source_rank: int,
        subtask_store: PublishSubtaskStore,
        allow_new_cutover: bool,
        target_rows: int = NATIVE_MARK_NAME_EVENT_TARGET_ROWS,
    ) -> None:
        self._delegate = delegate
        self._execution_client = execution_client
        self._package_id = str(package_uuid)
        self._source_rank = int(source_rank)
        self._subtask_store = subtask_store
        self._target_rows = int(target_rows)
        if self._target_rows < 1:
            raise ValueError("target_rows must be positive")
        self._executor: NativeMarkNameEventExecutor | None = None
        self._native_enabled = self._initialize_cutover(bool(allow_new_cutover))
        self._executed = 0
        self._skipped = 0
        self._package_kind: str | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    @property
    def native_mark_name_event_enabled(self) -> bool:
        return self._native_enabled

    @property
    def final_tasks_executed(self) -> int:
        return int(self._delegate.final_tasks_executed) + self._executed

    @property
    def final_tasks_skipped(self) -> int:
        return int(self._delegate.final_tasks_skipped) + self._skipped

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if _EVENT_INSERT not in sql or _EVENT_MARKER not in sql or not self._native_enabled:
            return self._delegate.command(sql, *args, **kwargs)
        node = resolve_legacy_publish_command(sql)
        if node is None or node.task_id != "MARK_NAME_EVENT":
            resolved = node.task_id if node is not None else "NONE"
            raise RuntimeError(
                "native mark-name-event cutover received unexpected sequencing shape: "
                f"expected=MARK_NAME_EVENT, resolved={resolved}"
            )
        if self._executor is not None:
            raise RuntimeError("native MARK_NAME_EVENT placeholder emitted twice")
        if self._package_kind is None:
            self._package_kind = str(get_package(self._package_id)["package_kind"])
        self._executor = NativeMarkNameEventExecutor(
            client=self._execution_client,
            package_uuid=self._package_id,
            package_kind=self._package_kind,
            source_rank=self._source_rank,
            subtask_store=self._subtask_store,
            target_rows=self._target_rows,
        )
        result = self._executor.execute()
        self._executed += result.executed
        self._skipped += result.skipped
        return result

    def assert_mark_name_event_complete(self) -> None:
        if not self._native_enabled:
            return
        if self._executor is None:
            raise RuntimeError("native MARK_NAME_EVENT was enabled but never observed")
        self._executor.assert_complete()

    def assert_final_publish_complete(self) -> dict[str, int]:
        self.assert_mark_name_event_complete()
        return self._delegate.assert_final_publish_complete()

    def _initialize_cutover(self, allow_new_cutover: bool) -> bool:
        marker_key = self._subtask_store.task_key(
            sql_hash=_EVENT_CUTOVER_HASH,
            stage_table=NATIVE_MARK_NAME_EVENT_CUTOVER_STAGE,
            lower=None,
            upper=None,
        )
        if self._subtask_store.is_success(marker_key, _EVENT_CUTOVER_HASH):
            return True
        task_status = getattr(self._subtask_store, "task_status", None)
        if callable(task_status):
            status = task_status(marker_key, _EVENT_CUTOVER_HASH)
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
            task_group="NATIVE_MARK_NAME_EVENT_CUTOVER",
            task_index=1,
            task_total=1,
            stage_table=NATIVE_MARK_NAME_EVENT_CUTOVER_STAGE,
            lower=None,
            upper=None,
            sql_hash=_EVENT_CUTOVER_HASH,
        )
        self._subtask_store.mark_success(marker_key)


def native_mark_name_event_contract() -> dict[str, Any]:
    return {
        "version": NATIVE_MARK_NAME_EVENT_VERSION,
        "cutover_version": NATIVE_MARK_NAME_EVENT_CUTOVER_VERSION,
        "native_node": "MARK_NAME_EVENT",
        "partition": "WHOLE_APPLICATION_HALF_OPEN_RANGE",
        "target_rows": NATIVE_MARK_NAME_EVENT_TARGET_ROWS,
        "durable_resume": True,
        "event_identity": "DETERMINISTIC_EVENT_HASH_REPLACING_MERGE_TREE",
        "current_side_filter": "EXACT_STAGE_APPLICATION_SET",
        "source_rank_semantics": "STRICT_PRIOR_CURRENT_ONLY",
        "baseline_policy": "NO_FIRST_OBSERVATION_EVENT",
        "storage_v2_semantics": "DELTA_ONLY_UNCHANGED",
        "preexisting_checkpoint_policy": "KEEP_LEGACY_MARK_NAME_EVENT_UNLESS_MARKER_PRESENT",
    }
