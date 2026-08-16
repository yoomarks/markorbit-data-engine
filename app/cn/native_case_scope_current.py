from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from typing import Any
import uuid

from app.cn.publish_dag import resolve_legacy_publish_command
from app.cn.publish_subtasks import PublishSubtaskStore
from app.repository import get_package


NATIVE_CASE_SCOPE_VERSION = "CN_NATIVE_CASE_SCOPE_CURRENT_V1"
NATIVE_CASE_SCOPE_CUTOVER_VERSION = "CN_NATIVE_CASE_SCOPE_CURRENT_CUTOVER_V1"
NATIVE_CASE_SCOPE_STAGE = "cn_stage_scope_publish"
NATIVE_CASE_SCOPE_CUTOVER_STAGE = "__native_case_scope_current_cutover_v1__"
NATIVE_CASE_SCOPE_TARGET_ROWS = 25_000
_SCOPE_INSERT = "INSERT INTO markorbit_facts.cn_case_scope_current"
_SCOPE_OPERATION_HASH = sha256(
    f"{NATIVE_CASE_SCOPE_VERSION}|CASE_SCOPE_CURRENT|SEMANTIC_SQL_V1".encode("utf-8")
).hexdigest()
_SCOPE_CUTOVER_HASH = sha256(
    NATIVE_CASE_SCOPE_CUTOVER_VERSION.encode("utf-8")
).hexdigest()


def _sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _nullable_date(value: Any) -> str:
    if value is None:
        return "CAST(NULL, 'Nullable(Date32)')"
    if isinstance(value, date):
        text = value.isoformat()
    else:
        text = str(value)
    return f"toDate32({_sql_string(text)})"


def _range_predicate(lower: str | None, upper: str | None, *, column: str) -> str:
    parts: list[str] = []
    if lower is not None:
        parts.append(f"{column} >= {_sql_string(lower)}")
    if upper is not None:
        parts.append(f"{column} < {_sql_string(upper)}")
    return " AND ".join(parts)


def plan_case_scope_ranges(
    package_uuid: uuid.UUID | str,
    *,
    client: Any,
    target_rows: int = NATIVE_CASE_SCOPE_TARGET_ROWS,
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
            FROM markorbit_facts.{NATIVE_CASE_SCOPE_STAGE}
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
                FROM markorbit_facts.{NATIVE_CASE_SCOPE_STAGE}
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


def case_scope_current_sql(
    package_uuid: uuid.UUID | str,
    *,
    package_kind: str,
    source_effective_date: Any,
    source_rank: int,
    lower: str | None,
    upper: str | None,
) -> str:
    package = str(package_uuid)
    kind = package_kind.replace("\\", "\\\\").replace("'", "\\'")
    incoming_range = _range_predicate(lower, upper, column="application_number")
    incoming_filter = f" AND {incoming_range}" if incoming_range else ""
    effective_expr = _nullable_date(source_effective_date)
    return f"""
        INSERT INTO markorbit_facts.cn_case_scope_current
        SELECT
            incoming.case_id, incoming.application_number, incoming.class_no,
            incoming.source_item_count, incoming.interpreted_active_item_count,
            incoming.interpreted_inactive_item_count, incoming.unmapped_status_item_count,
            incoming.effective_item_count, incoming.interpretation_complete,
            incoming.scope_interpretation_status,
            incoming.goods_status_mapping_version, incoming.observed_status_codes,
            incoming.goods_items_compact, incoming.goods_text_search,
            incoming.similar_groups, incoming.active_similar_groups,
            incoming.scope_hash, incoming.effective_scope_hash, '{kind}',
            {effective_expr}, incoming.source_file, incoming.source_first_line,
            incoming.source_last_line, incoming.source_row_hash, toUUID('{package}'),
            {int(source_rank)}, now64(3), 0
        FROM
        (
            SELECT *
            FROM markorbit_facts.cn_stage_scope_publish
            WHERE package_id = toUUID('{package}'){incoming_filter}
        ) AS incoming
        LEFT JOIN
        (
            SELECT *
            FROM markorbit_facts.cn_case_scope_current FINAL
            WHERE (application_number, class_no) IN
            (
                SELECT application_number, class_no
                FROM markorbit_facts.cn_stage_scope_publish
                WHERE package_id = toUUID('{package}'){incoming_filter}
            )
        ) AS cur
          ON cur.application_number = incoming.application_number
         AND cur.class_no = incoming.class_no
        WHERE cur.application_number = '' OR cur.source_rank <= {int(source_rank)}
    """


@dataclass(frozen=True)
class NativeCaseScopeExecutionResult:
    range_count: int
    executed: int
    skipped: int


class NativeCaseScopeExecutor:
    def __init__(
        self,
        *,
        client: Any,
        package_uuid: uuid.UUID | str,
        package_kind: str,
        source_effective_date: Any,
        source_rank: int,
        subtask_store: PublishSubtaskStore,
        target_rows: int = NATIVE_CASE_SCOPE_TARGET_ROWS,
    ) -> None:
        if target_rows < 1:
            raise ValueError("target_rows must be positive")
        self.client = client
        self.package_id = str(package_uuid)
        self.package_kind = str(package_kind)
        self.source_effective_date = source_effective_date
        self.source_rank = int(source_rank)
        self.subtask_store = subtask_store
        self.target_rows = int(target_rows)
        self._result: NativeCaseScopeExecutionResult | None = None

    def execute(self) -> NativeCaseScopeExecutionResult:
        if self._result is not None:
            raise RuntimeError("native CASE_SCOPE_CURRENT emitted more than once")
        try:
            ranges = plan_case_scope_ranges(
                self.package_id,
                client=self.client,
                target_rows=self.target_rows,
            )
        except Exception as exc:
            raise RuntimeError(f"native_publish_subphase=CASE_SCOPE_CURRENT_PLAN failed: {exc}") from exc

        executed = 0
        skipped = 0
        total = len(ranges)
        for index, (lower, upper) in enumerate(ranges, start=1):
            task_key = self.subtask_store.task_key(
                sql_hash=_SCOPE_OPERATION_HASH,
                stage_table=NATIVE_CASE_SCOPE_STAGE,
                lower=lower,
                upper=upper,
            )
            if self.subtask_store.is_success(task_key, _SCOPE_OPERATION_HASH):
                skipped += 1
                continue
            self.subtask_store.mark_running(
                task_key=task_key,
                task_group="CASE_SCOPE_CURRENT",
                task_index=index,
                task_total=total,
                stage_table=NATIVE_CASE_SCOPE_STAGE,
                lower=lower,
                upper=upper,
                sql_hash=_SCOPE_OPERATION_HASH,
            )
            try:
                self.client.command(
                    case_scope_current_sql(
                        self.package_id,
                        package_kind=self.package_kind,
                        source_effective_date=self.source_effective_date,
                        source_rank=self.source_rank,
                        lower=lower,
                        upper=upper,
                    )
                )
            except Exception as exc:
                self.subtask_store.mark_failed(task_key, str(exc))
                raise RuntimeError(
                    "native_publish_subphase=CASE_SCOPE_CURRENT "
                    f"task={index}/{total} range=[{lower or '-inf'},{upper or '+inf'}) "
                    f"failed: {exc}"
                ) from exc
            self.subtask_store.mark_success(task_key)
            executed += 1

        self._result = NativeCaseScopeExecutionResult(total, executed, skipped)
        return self._result

    def assert_complete(self) -> dict[str, int]:
        if self._result is None:
            raise RuntimeError("native CASE_SCOPE_CURRENT was enabled but never observed")
        return {
            "ranges": self._result.range_count,
            "executed": self._result.executed,
            "skipped": self._result.skipped,
        }


class NativeCaseScopeCutoverClient:
    """Versioned native cutover for CASE_SCOPE_CURRENT."""

    def __init__(
        self,
        delegate: Any,
        *,
        execution_client: Any,
        package_uuid: uuid.UUID | str,
        source_rank: int,
        subtask_store: PublishSubtaskStore,
        allow_new_cutover: bool,
        target_rows: int = NATIVE_CASE_SCOPE_TARGET_ROWS,
    ) -> None:
        self._delegate = delegate
        self._execution_client = execution_client
        self._package_id = str(package_uuid)
        self._source_rank = int(source_rank)
        self._subtask_store = subtask_store
        self._target_rows = int(target_rows)
        if self._target_rows < 1:
            raise ValueError("target_rows must be positive")
        self._executor: NativeCaseScopeExecutor | None = None
        self._native_enabled = self._initialize_cutover(bool(allow_new_cutover))
        self._executed = 0
        self._skipped = 0
        self._package_meta: dict[str, Any] | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    @property
    def native_case_scope_enabled(self) -> bool:
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
        if node is None or node.task_id != "CASE_SCOPE_CURRENT":
            resolved = node.task_id if node is not None else "NONE"
            raise RuntimeError(
                "native case-scope cutover received unexpected sequencing shape: "
                f"expected=CASE_SCOPE_CURRENT, resolved={resolved}"
            )
        if self._executor is not None:
            raise RuntimeError("native CASE_SCOPE_CURRENT placeholder emitted twice")
        if self._package_meta is None:
            self._package_meta = get_package(self._package_id)
        self._executor = NativeCaseScopeExecutor(
            client=self._execution_client,
            package_uuid=self._package_id,
            package_kind=str(self._package_meta["package_kind"]),
            source_effective_date=self._package_meta.get("source_period_end"),
            source_rank=self._source_rank,
            subtask_store=self._subtask_store,
            target_rows=self._target_rows,
        )
        result = self._executor.execute()
        self._executed += result.executed
        self._skipped += result.skipped
        return result

    def assert_case_scope_persist_complete(self) -> None:
        if not self._native_enabled:
            return
        if self._executor is None:
            raise RuntimeError("native CASE_SCOPE_CURRENT was enabled but never observed")
        self._executor.assert_complete()

    def assert_final_publish_complete(self) -> dict[str, int]:
        self.assert_case_scope_persist_complete()
        return self._delegate.assert_final_publish_complete()

    def _initialize_cutover(self, allow_new_cutover: bool) -> bool:
        marker_key = self._subtask_store.task_key(
            sql_hash=_SCOPE_CUTOVER_HASH,
            stage_table=NATIVE_CASE_SCOPE_CUTOVER_STAGE,
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
            task_group="NATIVE_CASE_SCOPE_CURRENT_CUTOVER",
            task_index=1,
            task_total=1,
            stage_table=NATIVE_CASE_SCOPE_CUTOVER_STAGE,
            lower=None,
            upper=None,
            sql_hash=_SCOPE_CUTOVER_HASH,
        )
        self._subtask_store.mark_success(marker_key)


def native_case_scope_contract() -> dict[str, Any]:
    return {
        "version": NATIVE_CASE_SCOPE_VERSION,
        "cutover_version": NATIVE_CASE_SCOPE_CUTOVER_VERSION,
        "native_node": "CASE_SCOPE_CURRENT",
        "partition": "WHOLE_APPLICATION_HALF_OPEN_RANGE",
        "target_rows": NATIVE_CASE_SCOPE_TARGET_ROWS,
        "durable_resume": True,
        "legacy_sql_rewrite": False,
        "current_side_filter": "EXACT_STAGE_APPLICATION_CLASS_KEY_SET",
        "source_rank_semantics": "UNCHANGED_LESS_THAN_OR_EQUAL",
        "source_effective_date_semantics": "SOURCE_PACKAGE_PERIOD_END",
        "preexisting_checkpoint_policy": "KEEP_LEGACY_CASE_SCOPE_UNLESS_MARKER_PRESENT",
    }
