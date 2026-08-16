from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any
import uuid

from app.cn.legacy_snapshot_persist import plan_application_ranges
from app.cn.publish_subtasks import PublishSubtaskStore


NATIVE_AUX_SNAPSHOT_VERSION = "CN_NATIVE_AUX_SNAPSHOT_V1"
NATIVE_AUX_TARGET_ROWS = 100_000
_NATIVE_NODES = {
    "PRIORITY_CURRENT": "cn_stage_priority",
    "MADRID_CURRENT": "cn_stage_madrid",
}


def _sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _range_predicate(lower: str | None, upper: str | None) -> str:
    parts: list[str] = []
    if lower is not None:
        parts.append(f"application_number >= {_sql_string(lower)}")
    if upper is not None:
        parts.append(f"application_number < {_sql_string(upper)}")
    return " AND ".join(parts)


def native_aux_operation_hash(node_id: str) -> str:
    if node_id not in _NATIVE_NODES:
        raise ValueError(f"unsupported native auxiliary node: {node_id}")
    payload = f"{NATIVE_AUX_SNAPSHOT_VERSION}|{node_id}|SEMANTIC_SQL_V1"
    return sha256(payload.encode("utf-8")).hexdigest()


def priority_current_sql(
    package_uuid: uuid.UUID | str,
    *,
    source_rank: int,
    lower: str | None,
    upper: str | None,
) -> str:
    package = str(package_uuid)
    predicate = _range_predicate(lower, upper)
    range_filter = f" AND {predicate}" if predicate else ""
    return f"""
        INSERT INTO markorbit_facts.cn_priority_current
        SELECT
            application_number, class_no, priority_number,
            argMax(priority_type, toUInt64(source_start_line)),
            argMax(priority_date, toUInt64(source_start_line)),
            argMax(priority_goods, toUInt64(source_start_line)),
            argMax(priority_country_region, toUInt64(source_start_line)),
            argMin(source_file, toUInt64(source_start_line)),
            min(toUInt64(source_start_line)),
            max(toUInt64(source_end_line)),
            hex(SHA256(arrayStringConcat(arraySort(groupArray(toString(row_hash))), '|'))),
            hex(SHA256(concat(
                application_number, '|', toString(class_no), '|', priority_number, '|',
                argMax(priority_goods, toUInt64(source_start_line))
            ))), toUUID('{package}'), {int(source_rank)}, now64(3), 0
        FROM markorbit_facts.cn_stage_priority
        WHERE package_id = toUUID('{package}'){range_filter}
        GROUP BY application_number, class_no, priority_number
    """


def madrid_current_sql(
    package_uuid: uuid.UUID | str,
    *,
    source_rank: int,
    lower: str | None,
    upper: str | None,
) -> str:
    package = str(package_uuid)
    predicate = _range_predicate(lower, upper)
    range_filter = f" AND {predicate}" if predicate else ""
    return f"""
        INSERT INTO markorbit_facts.cn_madrid_current
        SELECT
            application_number, international_registration_number,
            argMax(international_registration_date, toUInt64(source_start_line)),
            argMax(international_notification_date, toUInt64(source_start_line)),
            argMax(application_language, toUInt64(source_start_line)),
            argMax(application_type, toUInt64(source_start_line)),
            argMax(international_pub_issue, toUInt64(source_start_line)),
            argMax(international_pub_date, toUInt64(source_start_line)),
            argMax(subsequent_designation_date, toUInt64(source_start_line)),
            argMax(basic_registration_date, toUInt64(source_start_line)),
            argMin(source_file, toUInt64(source_start_line)),
            min(toUInt64(source_start_line)),
            max(toUInt64(source_end_line)),
            hex(SHA256(arrayStringConcat(arraySort(groupArray(toString(row_hash))), '|'))),
            hex(SHA256(concat(
                application_number, '|', international_registration_number, '|',
                ifNull(toString(argMax(
                    international_registration_date, toUInt64(source_start_line)
                )), '')
            ))), toUUID('{package}'), {int(source_rank)}, now64(3), 0
        FROM markorbit_facts.cn_stage_madrid
        WHERE package_id = toUUID('{package}'){range_filter}
        GROUP BY application_number, international_registration_number
    """


@dataclass(frozen=True)
class NativeAuxExecutionResult:
    node_id: str
    stage_table: str
    range_count: int
    executed: int
    skipped: int


class NativeAuxSnapshotExecutor:
    """Native bounded + resumable executor for auxiliary Current snapshots.

    Unlike the legacy compatibility client, this executor never receives or rewrites
    legacy SQL. The node identity selects a native SQL builder, ranges are planned
    from the source Stage table, and every range is recorded in the existing durable
    CN final-publish Work Engine ledger.
    """

    def __init__(
        self,
        *,
        client: Any,
        package_uuid: uuid.UUID | str,
        source_rank: int,
        subtask_store: PublishSubtaskStore,
        target_rows: int = NATIVE_AUX_TARGET_ROWS,
    ) -> None:
        if target_rows < 1:
            raise ValueError("target_rows must be positive")
        self.client = client
        self.package_id = str(package_uuid)
        self.source_rank = int(source_rank)
        self.subtask_store = subtask_store
        self.target_rows = int(target_rows)
        self._results: dict[str, NativeAuxExecutionResult] = {}

    def execute(self, node_id: str) -> NativeAuxExecutionResult:
        if node_id not in _NATIVE_NODES:
            raise ValueError(f"unsupported native auxiliary node: {node_id}")
        if node_id in self._results:
            raise RuntimeError(f"native auxiliary node emitted more than once: {node_id}")

        stage_table = _NATIVE_NODES[node_id]
        try:
            ranges = plan_application_ranges(
                self.package_id,
                client=self.client,
                stage_table=stage_table,
                target_rows=self.target_rows,
            )
        except Exception as exc:
            raise RuntimeError(f"native_publish_subphase={node_id}_PLAN failed: {exc}") from exc

        operation_hash = native_aux_operation_hash(node_id)
        executed = 0
        skipped = 0
        total = len(ranges)
        for index, (lower, upper) in enumerate(ranges, start=1):
            task_key = self.subtask_store.task_key(
                sql_hash=operation_hash,
                stage_table=stage_table,
                lower=lower,
                upper=upper,
            )
            if self.subtask_store.is_success(task_key, operation_hash):
                skipped += 1
                continue

            sql = self._sql(node_id, lower=lower, upper=upper)
            self.subtask_store.mark_running(
                task_key=task_key,
                task_group=node_id,
                task_index=index,
                task_total=total,
                stage_table=stage_table,
                lower=lower,
                upper=upper,
                sql_hash=operation_hash,
            )
            try:
                self.client.command(sql)
            except Exception as exc:
                self.subtask_store.mark_failed(task_key, str(exc))
                raise RuntimeError(
                    f"native_publish_subphase={node_id} task={index}/{total} "
                    f"range=[{lower or '-inf'},{upper or '+inf'}) failed: {exc}"
                ) from exc
            self.subtask_store.mark_success(task_key)
            executed += 1

        execution = NativeAuxExecutionResult(
            node_id=node_id,
            stage_table=stage_table,
            range_count=total,
            executed=executed,
            skipped=skipped,
        )
        self._results[node_id] = execution
        return execution

    def result(self, node_id: str) -> NativeAuxExecutionResult | None:
        return self._results.get(node_id)

    def assert_complete(self) -> dict[str, dict[str, int]]:
        missing = [node_id for node_id in _NATIVE_NODES if node_id not in self._results]
        if missing:
            raise RuntimeError(
                "native auxiliary publisher did not observe required nodes: "
                + ", ".join(missing)
            )
        return {
            node_id: {
                "ranges": result.range_count,
                "executed": result.executed,
                "skipped": result.skipped,
            }
            for node_id, result in self._results.items()
        }

    def _sql(self, node_id: str, *, lower: str | None, upper: str | None) -> str:
        if node_id == "PRIORITY_CURRENT":
            return priority_current_sql(
                self.package_id,
                source_rank=self.source_rank,
                lower=lower,
                upper=upper,
            )
        if node_id == "MADRID_CURRENT":
            return madrid_current_sql(
                self.package_id,
                source_rank=self.source_rank,
                lower=lower,
                upper=upper,
            )
        raise AssertionError(node_id)


def native_aux_snapshot_contract() -> dict[str, Any]:
    return {
        "version": NATIVE_AUX_SNAPSHOT_VERSION,
        "native_nodes": list(_NATIVE_NODES),
        "partition": "WHOLE_APPLICATION_HALF_OPEN_RANGE",
        "target_rows": NATIVE_AUX_TARGET_ROWS,
        "durable_resume": True,
        "legacy_sql_rewrite": False,
        "source_rank_semantics": "UNCHANGED",
        "lineage_hash_semantics": "UNCHANGED",
    }
