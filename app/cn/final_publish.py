from __future__ import annotations

from hashlib import sha256
import re
from typing import Any
import uuid

from app.cn.legacy_snapshot_persist import LegacySnapshotPersistClient
from app.cn.publish_subtasks import PublishSubtaskStore


CN_FINAL_PERSIST_TARGET_ROWS = 25_000
_PUBLISH_STAGE_TABLES = (
    "cn_stage_case_publish",
    "cn_stage_party_publish",
    "cn_stage_scope_publish",
)


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
    """Turn legacy publish-stage commands into durable resumable subtasks.

    CASE, PARTY and SCOPE are already materialized into bounded temporary
    ClickHouse tables by M1.6. The legacy publisher used to read each complete
    table back into one large JOIN/INSERT, recreating whole-package memory
    pressure. This wrapper executes every such statement over small disjoint
    application ranges and persists SUCCESS after each range in Postgres.
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

    @property
    def final_tasks_executed(self) -> int:
        return self._final_tasks_executed

    @property
    def final_tasks_skipped(self) -> int:
        return self._final_tasks_skipped

    @property
    def final_task_count(self) -> int:
        return self._final_tasks_executed + self._final_tasks_skipped

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        stage_table = _detect_publish_stage(sql)
        if stage_table is not None:
            return self._command_publish_stage(
                sql,
                *args,
                stage_table=stage_table,
                **kwargs,
            )
        return super().command(sql, *args, **kwargs)

    def assert_final_publish_complete(self) -> dict[str, int]:
        missing = [
            table for table, seen in self._stage_commands_seen.items() if seen == 0
        ]
        if missing:
            raise RuntimeError(
                "Legacy publisher shape changed; no final publish command observed for: "
                + ", ".join(missing)
            )
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
                predicate = _range_predicate(
                    lower,
                    upper,
                    column="incoming.application_number",
                )
                range_filter = f" AND {predicate}" if predicate else ""
                rows = self._delegate.query(
                    f"""
                    SELECT count()
                    FROM markorbit_facts.{stage_table} AS incoming
                    LEFT JOIN markorbit_facts.{current_table} AS cur FINAL
                      ON {join_condition}
                    WHERE incoming.package_id = toUUID('{self._final_package}')
                      {range_filter}
                      AND (cur.application_number = '' OR cur.source_rank < {int(source_rank)})
                    """.replace("\n                       AND", "\n                      AND")
                ).result_rows
                total_violations += int(rows[0][0] or 0) if rows else 0
            violations[stage_table] = total_violations
        failed = {table: count for table, count in violations.items() if count}
        if failed:
            raise RuntimeError(f"CN final publish current-coverage audit failed: {failed}")
        return violations

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

    def _rewrite_publish_stage(
        self,
        sql: str,
        *,
        stage_table: str,
        lower: str | None,
        upper: str | None,
    ) -> str:
        predicate = _range_predicate(lower, upper)
        if not predicate:
            return sql

        table_pattern = re.escape(f"markorbit_facts.{stage_table}")
        package_pattern = re.escape(self._final_package)
        pattern = re.compile(
            rf"(FROM\s+{table_pattern}\s+WHERE\s+package_id\s*=\s*"
            rf"toUUID\('{package_pattern}'\))",
            flags=re.IGNORECASE,
        )
        rewritten, count = pattern.subn(
            lambda match: match.group(1) + f" AND {predicate}",
            sql,
        )
        source_count = sql.count(f"markorbit_facts.{stage_table}")
        if count < 1 or count != source_count:
            raise RuntimeError(
                f"Legacy final publish SQL shape changed for {stage_table}: "
                f"sources={source_count}, bounded_sources={count}."
            )
        return rewritten
