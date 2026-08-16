from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from typing import Any
import uuid

from app.cn.publish_dag import resolve_legacy_publish_command
from app.cn.publish_subtasks import PublishSubtaskStore
from app.repository import get_package


NATIVE_PARTY_OBSERVED_EVENT_VERSION = "CN_NATIVE_PARTY_OBSERVED_EVENT_V1"
NATIVE_PARTY_OBSERVED_EVENT_CUTOVER_VERSION = "CN_NATIVE_PARTY_OBSERVED_EVENT_CUTOVER_V1"
NATIVE_PARTY_OBSERVED_EVENT_STAGE = "cn_stage_party_publish"
NATIVE_PARTY_OBSERVED_EVENT_CUTOVER_STAGE = "__native_party_observed_event_cutover_v1__"
NATIVE_PARTY_OBSERVED_EVENT_TARGET_ROWS = 25_000
_EVENT_INSERT = "INSERT INTO markorbit_facts.cn_observed_event"
_EVENT_MARKER = "_RELATION_OBSERVED"
_SUPERSEDED_MARKER = "_RELATION_SUPERSEDED_OBSERVED"
_EVENT_OPERATION_HASH = sha256(
    f"{NATIVE_PARTY_OBSERVED_EVENT_VERSION}|PARTY_OBSERVED_EVENT|SEMANTIC_SQL_V1".encode(
        "utf-8"
    )
).hexdigest()
_EVENT_CUTOVER_HASH = sha256(
    NATIVE_PARTY_OBSERVED_EVENT_CUTOVER_VERSION.encode("utf-8")
).hexdigest()


def _sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _nullable_date(value: Any) -> str:
    if value is None:
        return "CAST(NULL, 'Nullable(Date32)')"
    text = value.isoformat() if isinstance(value, date) else str(value)
    return f"toDate32({_sql_string(text)})"


def _range_predicate(lower: str | None, upper: str | None, *, column: str) -> str:
    parts: list[str] = []
    if lower is not None:
        parts.append(f"{column} >= {_sql_string(lower)}")
    if upper is not None:
        parts.append(f"{column} < {_sql_string(upper)}")
    return " AND ".join(parts)


def plan_party_observed_event_ranges(
    package_uuid: uuid.UUID | str,
    *,
    client: Any,
    target_rows: int = NATIVE_PARTY_OBSERVED_EVENT_TARGET_ROWS,
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
            FROM markorbit_facts.{NATIVE_PARTY_OBSERVED_EVENT_STAGE}
            WHERE package_id = toUUID('{package}'){lower_filter}
            ORDER BY application_number, role, relation_key
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
                FROM markorbit_facts.{NATIVE_PARTY_OBSERVED_EVENT_STAGE}
                WHERE package_id = toUUID('{package}')
                  AND application_number > {_sql_string(lower)}
                ORDER BY application_number, role, relation_key
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


def party_observed_event_sql(
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
        INSERT INTO markorbit_facts.cn_observed_event
        SELECT
            generateUUIDv4(), incoming.case_id, incoming.application_number,
            concat(incoming.role, '_RELATION_OBSERVED'),
            {effective_expr}, now64(3), 'PARTY', CAST(NULL, 'Nullable(UInt8)'),
            lowerUTF8(incoming.role), '',
            toJSONString(map(
                'name', incoming.raw_name,
                'address', incoming.raw_address,
                'relation_key', incoming.relation_key,
                'entity_id', ifNull(toString(incoming.entity_id), '')
            )),
            'OFFICIAL_FACT_OBSERVATION', 'NOT_DETERMINED', 1.0,
            toUUID('{package}'), '{kind}', incoming.source_file,
            incoming.source_first_line, incoming.source_last_line,
            incoming.source_row_hash, {int(source_rank)},
            hex(SHA256(concat(
                incoming.application_number, '|', incoming.role, '|OBSERVED|',
                incoming.relation_key, '|', toString({int(source_rank)})
            )))
        FROM
        (
            SELECT *
            FROM markorbit_facts.cn_stage_party_publish
            WHERE package_id = toUUID('{package}'){incoming_filter}
        ) AS incoming
        LEFT JOIN
        (
            SELECT *
            FROM markorbit_facts.cn_case_party_current FINAL
            WHERE (application_number, role, relation_key) IN
            (
                SELECT application_number, role, relation_key
                FROM markorbit_facts.cn_stage_party_publish
                WHERE package_id = toUUID('{package}'){incoming_filter}
            )
        ) AS cur
          ON cur.application_number = incoming.application_number
         AND cur.role = incoming.role
         AND cur.relation_key = incoming.relation_key
        WHERE (cur.application_number = '' OR cur.source_rank < {int(source_rank)})
          AND (
              cur.application_number = ''
              OR cur.is_current = 0
              OR cur.record_hash != incoming.record_hash
          )
    """


@dataclass(frozen=True)
class NativePartyObservedEventExecutionResult:
    range_count: int
    executed: int
    skipped: int


class NativePartyObservedEventExecutor:
    def __init__(
        self,
        *,
        client: Any,
        package_uuid: uuid.UUID | str,
        package_kind: str,
        source_effective_date: Any,
        source_rank: int,
        subtask_store: PublishSubtaskStore,
        target_rows: int = NATIVE_PARTY_OBSERVED_EVENT_TARGET_ROWS,
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
        self._result: NativePartyObservedEventExecutionResult | None = None

    def execute(self) -> NativePartyObservedEventExecutionResult:
        if self._result is not None:
            raise RuntimeError("native PARTY_OBSERVED_EVENT emitted more than once")
        try:
            ranges = plan_party_observed_event_ranges(
                self.package_id,
                client=self.client,
                target_rows=self.target_rows,
            )
        except Exception as exc:
            raise RuntimeError(
                f"native_publish_subphase=PARTY_OBSERVED_EVENT_PLAN failed: {exc}"
            ) from exc

        executed = 0
        skipped = 0
        total = len(ranges)
        for index, (lower, upper) in enumerate(ranges, start=1):
            task_key = self.subtask_store.task_key(
                sql_hash=_EVENT_OPERATION_HASH,
                stage_table=NATIVE_PARTY_OBSERVED_EVENT_STAGE,
                lower=lower,
                upper=upper,
            )
            if self.subtask_store.is_success(task_key, _EVENT_OPERATION_HASH):
                skipped += 1
                continue
            self.subtask_store.mark_running(
                task_key=task_key,
                task_group="PARTY_OBSERVED_EVENT",
                task_index=index,
                task_total=total,
                stage_table=NATIVE_PARTY_OBSERVED_EVENT_STAGE,
                lower=lower,
                upper=upper,
                sql_hash=_EVENT_OPERATION_HASH,
            )
            try:
                self.client.command(
                    party_observed_event_sql(
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
                    "native_publish_subphase=PARTY_OBSERVED_EVENT "
                    f"task={index}/{total} range=[{lower or '-inf'},{upper or '+inf'}) "
                    f"failed: {exc}"
                ) from exc
            self.subtask_store.mark_success(task_key)
            executed += 1

        self._result = NativePartyObservedEventExecutionResult(total, executed, skipped)
        return self._result

    def assert_complete(self) -> dict[str, int]:
        if self._result is None:
            raise RuntimeError("native PARTY_OBSERVED_EVENT was enabled but never observed")
        return {
            "ranges": self._result.range_count,
            "executed": self._result.executed,
            "skipped": self._result.skipped,
        }


class NativePartyObservedEventCutoverClient:
    """Versioned native cutover for Party relation observations."""

    def __init__(
        self,
        delegate: Any,
        *,
        execution_client: Any,
        package_uuid: uuid.UUID | str,
        source_rank: int,
        subtask_store: PublishSubtaskStore,
        allow_new_cutover: bool,
        target_rows: int = NATIVE_PARTY_OBSERVED_EVENT_TARGET_ROWS,
    ) -> None:
        self._delegate = delegate
        self._execution_client = execution_client
        self._package_id = str(package_uuid)
        self._source_rank = int(source_rank)
        self._subtask_store = subtask_store
        self._target_rows = int(target_rows)
        if self._target_rows < 1:
            raise ValueError("target_rows must be positive")
        self._executor: NativePartyObservedEventExecutor | None = None
        self._native_enabled = self._initialize_cutover(bool(allow_new_cutover))
        self._executed = 0
        self._skipped = 0
        self._package_meta: dict[str, Any] | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    @property
    def native_party_observed_event_enabled(self) -> bool:
        return self._native_enabled

    @property
    def final_tasks_executed(self) -> int:
        return int(self._delegate.final_tasks_executed) + self._executed

    @property
    def final_tasks_skipped(self) -> int:
        return int(self._delegate.final_tasks_skipped) + self._skipped

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if (
            _EVENT_INSERT not in sql
            or _EVENT_MARKER not in sql
            or _SUPERSEDED_MARKER in sql
            or not self._native_enabled
        ):
            return self._delegate.command(sql, *args, **kwargs)
        node = resolve_legacy_publish_command(sql)
        if node is None or node.task_id != "PARTY_OBSERVED_EVENT":
            resolved = node.task_id if node is not None else "NONE"
            raise RuntimeError(
                "native party-observed-event cutover received unexpected sequencing shape: "
                f"expected=PARTY_OBSERVED_EVENT, resolved={resolved}"
            )
        if self._executor is not None:
            raise RuntimeError("native PARTY_OBSERVED_EVENT placeholder emitted twice")
        if self._package_meta is None:
            self._package_meta = get_package(self._package_id)
        self._executor = NativePartyObservedEventExecutor(
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

    def assert_party_observed_event_complete(self) -> None:
        if not self._native_enabled:
            return
        if self._executor is None:
            raise RuntimeError("native PARTY_OBSERVED_EVENT was enabled but never observed")
        self._executor.assert_complete()

    def assert_final_publish_complete(self) -> dict[str, int]:
        self.assert_party_observed_event_complete()
        return self._delegate.assert_final_publish_complete()

    def _initialize_cutover(self, allow_new_cutover: bool) -> bool:
        marker_key = self._subtask_store.task_key(
            sql_hash=_EVENT_CUTOVER_HASH,
            stage_table=NATIVE_PARTY_OBSERVED_EVENT_CUTOVER_STAGE,
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
            task_group="NATIVE_PARTY_OBSERVED_EVENT_CUTOVER",
            task_index=1,
            task_total=1,
            stage_table=NATIVE_PARTY_OBSERVED_EVENT_CUTOVER_STAGE,
            lower=None,
            upper=None,
            sql_hash=_EVENT_CUTOVER_HASH,
        )
        self._subtask_store.mark_success(marker_key)


def native_party_observed_event_contract() -> dict[str, Any]:
    return {
        "version": NATIVE_PARTY_OBSERVED_EVENT_VERSION,
        "cutover_version": NATIVE_PARTY_OBSERVED_EVENT_CUTOVER_VERSION,
        "native_node": "PARTY_OBSERVED_EVENT",
        "partition": "WHOLE_APPLICATION_HALF_OPEN_RANGE",
        "target_rows": NATIVE_PARTY_OBSERVED_EVENT_TARGET_ROWS,
        "durable_resume": True,
        "current_side_filter": "EXACT_STAGE_APPLICATION_ROLE_RELATION_KEY_SET",
        "source_rank_semantics": "MISSING_OR_STRICT_PRIOR_CURRENT_ONLY",
        "observation_semantics": "MISSING_OR_NONCURRENT_OR_RECORD_HASH_CHANGED",
        "lineage_semantics": "INCOMING_RELATION_SOURCE_LINEAGE",
        "event_hash_semantics": "LEGACY_APPLICATION_ROLE_OBSERVED_RELATION_RANK",
        "history_policy": "CN_OBSERVED_EVENT_IS_CANONICAL_DURABLE_PARTY_HISTORY",
        "preexisting_checkpoint_policy": "KEEP_LEGACY_PARTY_OBSERVED_EVENT_UNLESS_MARKER_PRESENT",
    }
