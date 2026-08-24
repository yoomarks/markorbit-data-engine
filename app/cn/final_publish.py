from __future__ import annotations

from hashlib import sha256
import re
from typing import Any
import uuid

from app.cn.legacy_snapshot_persist import LegacySnapshotPersistClient
from app.cn.native_aux_snapshot import NativeAuxSnapshotExecutor
from app.cn.publish_dag import resolve_legacy_publish_command
from app.cn.publish_subtasks import PublishSubtaskStore
from app.repository import get_package


CN_FINAL_PERSIST_TARGET_ROWS = 25_000
_PUBLISH_STAGE_TABLES = (
    "cn_stage_case_publish",
    "cn_stage_party_publish",
    "cn_stage_scope_publish",
)
_CURRENT_JOIN_TABLES = (
    "cn_case_current",
    "cn_case_scope_current",
    "cn_case_party_current",
)
_NATIVE_AUX_TARGETS = {
    "INSERT INTO markorbit_facts.cn_priority_current": "PRIORITY_CURRENT",
    "INSERT INTO markorbit_facts.cn_madrid_current": "MADRID_CURRENT",
}
_NATIVE_AUX_CUTOVER_VERSION = "CN_NATIVE_AUX_CUTOVER_V1"
_NATIVE_AUX_CUTOVER_STAGE = "__native_aux_cutover_v1__"
_NATIVE_AUX_CUTOVER_HASH = sha256(_NATIVE_AUX_CUTOVER_VERSION.encode("utf-8")).hexdigest()


def _sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _range_predicate(
    lower: str | None,
    upper: str | None,
    *,
    column: str = "application_number",
) -> str:
    parts: list[str] = []
    if lower is not None:
        parts.append(f"{column} >= {_sql_string(lower)}")
    if upper is not None:
        parts.append(f"{column} < {_sql_string(upper)}")
    return " AND ".join(parts)


def plan_publish_ranges(
    package_uuid: uuid.UUID | str,
    *,
    client: Any,
    stage_table: str,
    target_rows: int = CN_FINAL_PERSIST_TARGET_ROWS,
) -> list[tuple[str | None, str | None]]:
    """Plan small whole-application ranges for final ClickHouse persistence."""
    if stage_table not in _PUBLISH_STAGE_TABLES:
        raise ValueError(f"unsupported final publish stage table: {stage_table}")
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
            FROM markorbit_facts.{stage_table}
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
                FROM markorbit_facts.{stage_table}
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


def _detect_publish_stage(sql: str) -> str | None:
    present = [
        table for table in _PUBLISH_STAGE_TABLES if f"markorbit_facts.{table}" in sql
    ]
    if len(present) > 1:
        raise RuntimeError(
            "Legacy final publish SQL unexpectedly mixes publish-stage tables: "
            + ", ".join(present)
        )
    return present[0] if present else None


def _command_label(sql: str) -> str:
    markers = (
        ("cn_case_party_relation_history", "PARTY_HISTORY"),
        ("cn_scope_carve_out_current", "SCOPE_CARVE_OUT_CURRENT"),
        ("cn_case_relation_current", "CASE_RELATION_CURRENT"),
        ("cn_case_scope_current", "CASE_SCOPE_CURRENT"),
        ("cn_case_party_current", "CASE_PARTY_CURRENT"),
        ("cn_case_current", "CASE_CURRENT"),
        ("cn_observed_event", "OBSERVED_EVENT"),
    )
    for marker, label in markers:
        if marker in sql:
            return label
    return "FINAL_PUBLISH"


class ResumableFinalPublishClient(LegacySnapshotPersistClient):
    """Bound final persistence and incrementally replace legacy nodes with native work.

    CASE, PARTY and SCOPE are materialized into bounded temporary ClickHouse tables
    and persisted as durable application-range subtasks. PRIORITY_CURRENT and
    MADRID_CURRENT are the first native DAG cutover: the legacy publisher still
    provides their sequencing position, but its SQL text is never rewritten or
    executed for new final-publish checkpoints. Native SQL builders own those
    queries and use the same durable work-unit ledger.

    Existing in-flight checkpoints created before the native cutover keep the old
    bounded compatibility path. A durable cutover marker is written only when the
    subtask ledger is empty, so a package that already committed compatibility work
    is never silently switched mid-run.
    """

    def __init__(
        self,
        delegate: Any,
        *,
        package_uuid: uuid.UUID | str,
        agent_batches: list[tuple[str, ...]],
        subtask_store: PublishSubtaskStore,
    ) -> None:
        super().__init__(
            delegate,
            package_uuid=package_uuid,
            agent_batches=agent_batches,
        )
        self._final_package = str(package_uuid)
        self._subtask_store = subtask_store
        self._final_ranges: dict[str, list[tuple[str | None, str | None]]] = {}
        self._stage_commands_seen = {table: 0 for table in _PUBLISH_STAGE_TABLES}
        self._final_tasks_executed = 0
        self._final_tasks_skipped = 0
        self._native_aux_executor: NativeAuxSnapshotExecutor | None = None
        self._native_aux_enabled = self._initialize_native_aux_cutover()

    @property
    def final_tasks_executed(self) -> int:
        return self._final_tasks_executed

    @property
    def final_tasks_skipped(self) -> int:
        return self._final_tasks_skipped

    @property
    def final_task_count(self) -> int:
        return self._final_tasks_executed + self._final_tasks_skipped

    @property
    def native_aux_enabled(self) -> bool:
        return self._native_aux_enabled

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        for marker, node_id in _NATIVE_AUX_TARGETS.items():
            if marker in sql and self._native_aux_enabled:
                return self._command_native_aux(sql, node_id=node_id)

        stage_table = _detect_publish_stage(sql)
        if stage_table is not None:
            return self._command_publish_stage(
                sql,
                *args,
                stage_table=stage_table,
                **kwargs,
            )
        return super().command(sql, *args, **kwargs)

    def assert_aux_persist_complete(self) -> None:
        if not self._native_aux_enabled:
            super().assert_aux_persist_complete()
            return
        if self._native_aux_executor is None:
            raise RuntimeError("native auxiliary publisher was enabled but never executed")
        self._native_aux_executor.assert_complete()

    def assert_final_publish_complete(self) -> dict[str, int]:
        missing = [
            table for table, seen in self._stage_commands_seen.items() if seen == 0
        ]
        if missing:
            # On a retry, an earlier attempt may already have committed every bounded
            # stage range while a newer legacy publisher no longer emits the old SQL
            # placeholders. The durable ledger is authoritative only when every
            # persisted group for all three publish stages is structurally complete
            # and SUCCESS. Fresh/partial shape drift still fails closed here.
            summary = self._subtask_store.assert_complete()
            self._subtask_store.assert_stage_groups_complete(_PUBLISH_STAGE_TABLES)
            return summary
        return self._subtask_store.assert_complete()

    def audit_current_coverage(self, *, source_rank: int) -> dict[str, int]:
        """Bounded post-publish audit: every staged incoming key reached equal/newer current."""
        checks = {
            "cn_stage_case_publish": (
                "cn_case_current",
                "cur.application_number = incoming.application_number",
            ),
            "cn_stage_scope_publish": (
                "cn_case_scope_current",
                "cur.application_number = incoming.application_number "
                "AND cur.class_no = incoming.class_no",
            ),
            "cn_stage_party_publish": (
                "cn_case_party_current",
                "cur.application_number = incoming.application_number "
                "AND cur.role = incoming.role AND cur.relation_key = incoming.relation_key",
            ),
        }
        violations: dict[str, int] = {}
        for stage_table, (current_table, join_condition) in checks.items():
            total_violations = 0
            for lower, upper in self._ranges(stage_table):
                incoming_predicate = _range_predicate(
                    lower,
                    upper,
                    column="incoming.application_number",
                )
                incoming_filter = (
                    f" AND {incoming_predicate}" if incoming_predicate else ""
                )
                current_source = self._bounded_current_source(
                    current_table,
                    "cur",
                    stage_table=stage_table,
                    lower=lower,
                    upper=upper,
                )
                rows = self._delegate.query(
                    f"""
                    SELECT count()
                    FROM markorbit_facts.{stage_table} AS incoming
                    LEFT JOIN {current_source}
                      ON {join_condition}
                    WHERE incoming.package_id = toUUID('{self._final_package}')
                      {incoming_filter}
                      AND (cur.application_number = '' OR cur.source_rank < {int(source_rank)})
                    """.replace("\n                       AND", "\n                      AND")
                ).result_rows
                total_violations += int(rows[0][0] or 0) if rows else 0
            violations[stage_table] = total_violations
        failed = {table: count for table, count in violations.items() if count}
        if failed:
            raise RuntimeError(f"CN final publish current-coverage audit failed: {failed}")
        return violations

    def _initialize_native_aux_cutover(self) -> bool:
        marker_key = self._subtask_store.task_key(
            sql_hash=_NATIVE_AUX_CUTOVER_HASH,
            stage_table=_NATIVE_AUX_CUTOVER_STAGE,
            lower=None,
            upper=None,
        )
        if self._subtask_store.is_success(marker_key, _NATIVE_AUX_CUTOVER_HASH):
            return True

        summary_method = getattr(self._subtask_store, "summary", None)
        if not callable(summary_method):
            # Lightweight test stores do not necessarily implement summary(). They
            # have no persisted in-flight compatibility state, so native execution
            # is safe without a durable migration marker.
            return True

        summary = dict(summary_method() or {})
        if sum(int(value or 0) for value in summary.values()) != 0:
            return False

        self._subtask_store.mark_running(
            task_key=marker_key,
            task_group="NATIVE_AUX_CUTOVER",
            task_index=1,
            task_total=1,
            stage_table=_NATIVE_AUX_CUTOVER_STAGE,
            lower=None,
            upper=None,
            sql_hash=_NATIVE_AUX_CUTOVER_HASH,
        )
        self._subtask_store.mark_success(marker_key)
        return True

    def _command_native_aux(self, sql: str, *, node_id: str) -> Any:
        node = resolve_legacy_publish_command(sql)
        if node is None or node.task_id != node_id:
            resolved = node.task_id if node is not None else "NONE"
            raise RuntimeError(
                "native auxiliary cutover received unexpected legacy sequencing shape: "
                f"expected={node_id}, resolved={resolved}"
            )

        if self._native_aux_executor is None:
            package = get_package(self._final_package)
            self._native_aux_executor = NativeAuxSnapshotExecutor(
                client=self._delegate,
                package_uuid=self._final_package,
                source_rank=int(package["source_rank"]),
                subtask_store=self._subtask_store,
            )
        execution = self._native_aux_executor.execute(node_id)
        self._final_tasks_executed += execution.executed
        self._final_tasks_skipped += execution.skipped
        return execution

    def _ranges(self, stage_table: str) -> list[tuple[str | None, str | None]]:
        existing = self._final_ranges.get(stage_table)
        if existing is not None:
            return existing
        try:
            ranges = plan_publish_ranges(
                self._final_package,
                client=self._delegate,
                stage_table=stage_table,
            )
        except Exception as exc:
            raise RuntimeError(
                f"legacy_snapshot_subphase={stage_table.upper()}_PLAN failed: {exc}"
            ) from exc
        self._final_ranges[stage_table] = ranges
        return ranges

    def _command_publish_stage(
        self,
        sql: str,
        *args: Any,
        stage_table: str,
        **kwargs: Any,
    ) -> Any:
        self._stage_commands_seen[stage_table] += 1
        ranges = self._ranges(stage_table)
        sql_hash = sha256(sql.encode("utf-8")).hexdigest()
        label = _command_label(sql)
        total = len(ranges)
        result: Any = None

        for index, (lower, upper) in enumerate(ranges, start=1):
            task_key = self._subtask_store.task_key(
                sql_hash=sql_hash,
                stage_table=stage_table,
                lower=lower,
                upper=upper,
            )
            if self._subtask_store.is_success(task_key, sql_hash):
                self._final_tasks_skipped += 1
                continue

            rewritten = self._rewrite_publish_stage(
                sql,
                stage_table=stage_table,
                lower=lower,
                upper=upper,
            )
            self._subtask_store.mark_running(
                task_key=task_key,
                task_group=label,
                task_index=index,
                task_total=total,
                stage_table=stage_table,
                lower=lower,
                upper=upper,
                sql_hash=sql_hash,
            )
            try:
                result = self._delegate.command(rewritten, *args, **kwargs)
            except Exception as exc:
                self._subtask_store.mark_failed(task_key, str(exc))
                raise RuntimeError(
                    f"legacy_snapshot_subphase={label} stage={stage_table} "
                    f"task={index}/{total} range=[{lower or '-inf'},{upper or '+inf'}) "
                    f"failed: {exc}"
                ) from exc
            self._subtask_store.mark_success(task_key)
            self._final_tasks_executed += 1
        return result

    def _stage_application_filter(
        self,
        stage_table: str,
        *,
        lower: str | None,
        upper: str | None,
    ) -> str:
        predicate = _range_predicate(lower, upper)
        range_filter = f" AND {predicate}" if predicate else ""
        return f"""
            application_number IN
            (
                SELECT DISTINCT application_number
                FROM markorbit_facts.{stage_table}
                WHERE package_id = toUUID('{self._final_package}'){range_filter}
            )
        """.strip()

    def _bounded_current_source(
        self,
        current_table: str,
        alias: str,
        *,
        stage_table: str,
        lower: str | None,
        upper: str | None,
    ) -> str:
        application_filter = self._stage_application_filter(
            stage_table,
            lower=lower,
            upper=upper,
        )
        return f"""(
            SELECT *
            FROM markorbit_facts.{current_table} FINAL
            WHERE {application_filter}
        ) AS {alias}"""

    def _bound_current_join_sources(
        self,
        sql: str,
        *,
        stage_table: str,
        lower: str | None,
        upper: str | None,
    ) -> str:
        current_tables = "|".join(re.escape(table) for table in _CURRENT_JOIN_TABLES)
        pattern = re.compile(
            rf"(?P<join>(?:LEFT|INNER)\s+JOIN)\s+markorbit_facts\."
            rf"(?P<table>{current_tables})\s+AS\s+"
            rf"(?P<alias>cur|case_current)\s+FINAL",
            flags=re.IGNORECASE,
        )

        def replace(match: re.Match[str]) -> str:
            table = match.group("table")
            alias = match.group("alias")
            bounded = self._bounded_current_source(
                table,
                alias,
                stage_table=stage_table,
                lower=lower,
                upper=upper,
            )
            return f"{match.group('join')} {bounded}"

        return pattern.sub(replace, sql)

    def _rewrite_publish_stage(
        self,
        sql: str,
        *,
        stage_table: str,
        lower: str | None,
        upper: str | None,
    ) -> str:
        predicate = _range_predicate(lower, upper)
        table_pattern = re.escape(f"markorbit_facts.{stage_table}")
        package_pattern = re.escape(self._final_package)
        pattern = re.compile(
            rf"(FROM\s+{table_pattern}\s+WHERE\s+package_id\s*=\s*"
            rf"toUUID\('{package_pattern}'\))",
            flags=re.IGNORECASE,
        )
        rewritten, count = pattern.subn(
            lambda match: match.group(1) + (f" AND {predicate}" if predicate else ""),
            sql,
        )
        source_count = sql.count(f"markorbit_facts.{stage_table}")
        if count < 1 or count != source_count:
            raise RuntimeError(
                f"Legacy final publish SQL shape changed for {stage_table}: "
                f"sources={source_count}, bounded_sources={count}."
            )
        return self._bound_current_join_sources(
            rewritten,
            stage_table=stage_table,
            lower=lower,
            upper=upper,
        )