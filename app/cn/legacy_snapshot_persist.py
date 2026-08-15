from __future__ import annotations

from typing import Any
import uuid


CN_AGENT_PERSIST_TARGET_ROWS = 100_000
CN_AGENT_PERSIST_MAX_CODES = 1_000
CN_AUX_PERSIST_TARGET_ROWS = 100_000
_AGENT_INSERT = "INSERT INTO markorbit_facts.cn_agent_current"
_PRIORITY_INSERT = "INSERT INTO markorbit_facts.cn_priority_current"
_MADRID_INSERT = "INSERT INTO markorbit_facts.cn_madrid_current"
_ALLOWED_APPLICATION_STAGE_TABLES = {
    "cn_stage_priority",
    "cn_stage_madrid",
}


def _sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _application_predicate(
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
    if not parts:
        return ""
    return " AND " + " AND ".join(parts)


def plan_agent_code_batches(
    package_uuid: uuid.UUID | str,
    *,
    client: Any,
    target_rows: int = CN_AGENT_PERSIST_TARGET_ROWS,
    max_codes: int = CN_AGENT_PERSIST_MAX_CODES,
) -> list[tuple[str, ...]]:
    """Plan whole-agent batches without ever splitting one agent_code.

    The legacy M1.5 snapshot publisher hashes every BASIC row associated with an
    agent via ``groupArray(row_hash)``. On very large M1.6 monthly packages that
    whole-package aggregate can retain several GiB of string state even though
    all earlier CASE/PARTY/GOODS work is already bounded. Counting rows per
    agent is cheap because the group cardinality is small; the returned batches
    then let the exact legacy aggregate run on disjoint complete agent groups.
    """
    if target_rows < 1:
        raise ValueError("target_rows must be positive")
    if max_codes < 1:
        raise ValueError("max_codes must be positive")

    package = str(package_uuid)
    rows = client.query(
        f"""
        SELECT agent_code, count()
        FROM markorbit_facts.cn_stage_basic
        WHERE package_id = toUUID('{package}')
          AND agent_code != ''
        GROUP BY agent_code
        ORDER BY agent_code
        """
    ).result_rows

    batches: list[tuple[str, ...]] = []
    current: list[str] = []
    current_rows = 0
    for agent_code, row_count in rows:
        code = str(agent_code)
        count = int(row_count or 0)
        if current and (
            current_rows + count > int(target_rows)
            or len(current) >= int(max_codes)
        ):
            batches.append(tuple(current))
            current = []
            current_rows = 0
        current.append(code)
        current_rows += count
    if current:
        batches.append(tuple(current))
    return batches


def plan_application_ranges(
    package_uuid: uuid.UUID | str,
    *,
    client: Any,
    stage_table: str,
    target_rows: int = CN_AUX_PERSIST_TARGET_ROWS,
) -> list[tuple[str | None, str | None]]:
    """Plan half-open application-number ranges without splitting an application.

    PRIORITY and MADRID legacy snapshots group by keys rooted at
    ``application_number`` and retain ``groupArray(row_hash)`` state. The old
    publisher ran each aggregate across the entire staged package. These ranges
    use the same whole-application boundary pattern as the bounded CASE/PARTY
    materializers: a boundary that lands inside a repeated application number is
    moved to the next distinct application so one semantic unit is never split.
    """
    if target_rows < 1:
        raise ValueError("target_rows must be positive")
    if stage_table not in _ALLOWED_APPLICATION_STAGE_TABLES:
        raise ValueError(f"unsupported application stage table: {stage_table}")

    package = str(package_uuid)
    table = f"markorbit_facts.{stage_table}"
    ranges: list[tuple[str | None, str | None]] = []
    lower: str | None = None
    while True:
        lower_sql = ""
        if lower is not None:
            lower_sql = f" AND application_number >= {_sql_string(lower)}"
        rows = client.query(
            f"""
            SELECT application_number
            FROM {table}
            WHERE package_id = toUUID('{package}'){lower_sql}
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
                FROM {table}
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


def _command_label(sql: str) -> str:
    markers = (
        ("cn_agent_current", "AGENT_CURRENT"),
        ("cn_priority_current", "PRIORITY_CURRENT"),
        ("cn_madrid_current", "MADRID_CURRENT"),
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
    return "OTHER"


class LegacySnapshotPersistClient:
    """Bound high-amplification legacy snapshot aggregates used by M1.6.

    M1.6 intentionally reuses the mature legacy snapshot writer after CASE,
    PARTY and GOODS have been materialized in bounded stages. The original
    remaining high-amplification aggregate was ``cn_agent_current``; once that
    is bounded, PRIORITY and MADRID are the next whole-package ``groupArray``
    statements in publisher order.

    Agent persistence is split by complete ``agent_code`` groups. PRIORITY and
    MADRID persistence are split by half-open application-number ranges, never
    splitting one application. Existing lineage hashes and current-row semantics
    therefore stay unchanged while individual aggregate queries remain bounded.
    Other legacy snapshot statements pass through untouched, with precise
    subphase labels preserved for the next real-corpus diagnosis.
    """

    def __init__(
        self,
        delegate: Any,
        *,
        package_uuid: uuid.UUID | str,
        agent_batches: list[tuple[str, ...]],
        priority_ranges: list[tuple[str | None, str | None]] | None = None,
        madrid_ranges: list[tuple[str | None, str | None]] | None = None,
    ) -> None:
        self._delegate = delegate
        self._package = str(package_uuid)
        self._agent_batches = list(agent_batches)
        self._priority_ranges = list(priority_ranges or [(None, None)])
        self._madrid_ranges = list(madrid_ranges or [(None, None)])
        self._agent_insert_seen = 0
        self._priority_insert_seen = 0
        self._madrid_insert_seen = 0
        self._physical_agent_commands = 0
        self._physical_priority_commands = 0
        self._physical_madrid_commands = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    @property
    def agent_chunk_count(self) -> int:
        return len(self._agent_batches)

    @property
    def agent_code_count(self) -> int:
        return sum(len(batch) for batch in self._agent_batches)

    @property
    def priority_chunk_count(self) -> int:
        return len(self._priority_ranges)

    @property
    def madrid_chunk_count(self) -> int:
        return len(self._madrid_ranges)

    @property
    def physical_agent_commands(self) -> int:
        return self._physical_agent_commands

    @property
    def physical_priority_commands(self) -> int:
        return self._physical_priority_commands

    @property
    def physical_madrid_commands(self) -> int:
        return self._physical_madrid_commands

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if _AGENT_INSERT in sql:
            return self._command_agent_current(sql, *args, **kwargs)
        if _PRIORITY_INSERT in sql:
            return self._command_application_current(
                sql,
                label="PRIORITY_CURRENT",
                stage_table="cn_stage_priority",
                ranges=self._priority_ranges,
                seen_attr="_priority_insert_seen",
                physical_attr="_physical_priority_commands",
                *args,
                **kwargs,
            )
        if _MADRID_INSERT in sql:
            return self._command_application_current(
                sql,
                label="MADRID_CURRENT",
                stage_table="cn_stage_madrid",
                ranges=self._madrid_ranges,
                seen_attr="_madrid_insert_seen",
                physical_attr="_physical_madrid_commands",
                *args,
                **kwargs,
            )
        try:
            return self._delegate.command(sql, *args, **kwargs)
        except Exception as exc:
            raise RuntimeError(
                f"legacy_snapshot_subphase={_command_label(sql)} failed: {exc}"
            ) from exc

    def query(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return self._delegate.query(sql, *args, **kwargs)
        except Exception as exc:
            raise RuntimeError("legacy_snapshot_subphase=METRICS failed: " f"{exc}") from exc

    def assert_agent_persist_complete(self) -> None:
        if self._agent_insert_seen != 1:
            raise RuntimeError(
                "Expected exactly one legacy cn_agent_current INSERT, saw "
                f"{self._agent_insert_seen}. Legacy publisher shape changed."
            )
        if self._physical_agent_commands != len(self._agent_batches):
            raise RuntimeError(
                "Bounded cn_agent_current command count mismatch: expected "
                f"{len(self._agent_batches)}, ran {self._physical_agent_commands}."
            )

    def assert_aux_persist_complete(self) -> None:
        checks = (
            (
                "cn_priority_current",
                self._priority_insert_seen,
                self._physical_priority_commands,
                len(self._priority_ranges),
            ),
            (
                "cn_madrid_current",
                self._madrid_insert_seen,
                self._physical_madrid_commands,
                len(self._madrid_ranges),
            ),
        )
        for table, seen, physical, expected in checks:
            if seen != 1:
                raise RuntimeError(
                    f"Expected exactly one legacy {table} INSERT, saw {seen}. "
                    "Legacy publisher shape changed."
                )
            if physical != expected:
                raise RuntimeError(
                    f"Bounded {table} command count mismatch: expected "
                    f"{expected}, ran {physical}."
                )

    def _command_agent_current(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if self._agent_insert_seen != 0:
            raise RuntimeError(
                "Legacy snapshot emitted cn_agent_current more than once; refusing "
                "ambiguous bounded persistence."
            )
        self._agent_insert_seen += 1

        # No staged agent observations means the original INSERT would emit no
        # rows. Avoid a whole-stage scan solely to rediscover that fact.
        if not self._agent_batches:
            return None

        result: Any = None
        total = len(self._agent_batches)
        for index, batch in enumerate(self._agent_batches, start=1):
            rewritten = self._rewrite_agent_insert(sql, batch)
            try:
                result = self._delegate.command(rewritten, *args, **kwargs)
            except Exception as exc:
                raise RuntimeError(
                    "legacy_snapshot_subphase=AGENT_CURRENT "
                    f"chunk={index}/{total} agent_codes={len(batch)} failed: {exc}"
                ) from exc
            self._physical_agent_commands += 1
        return result

    def _command_application_current(
        self,
        sql: str,
        *args: Any,
        label: str,
        stage_table: str,
        ranges: list[tuple[str | None, str | None]],
        seen_attr: str,
        physical_attr: str,
        **kwargs: Any,
    ) -> Any:
        seen = int(getattr(self, seen_attr))
        if seen != 0:
            raise RuntimeError(
                f"Legacy snapshot emitted {stage_table} target more than once; refusing "
                "ambiguous bounded persistence."
            )
        setattr(self, seen_attr, seen + 1)

        result: Any = None
        total = len(ranges)
        for index, application_range in enumerate(ranges, start=1):
            rewritten = self._rewrite_application_insert(
                sql,
                stage_table=stage_table,
                application_range=application_range,
            )
            try:
                result = self._delegate.command(rewritten, *args, **kwargs)
            except Exception as exc:
                lower, upper = application_range
                raise RuntimeError(
                    f"legacy_snapshot_subphase={label} chunk={index}/{total} "
                    f"range=[{lower or '-inf'},{upper or '+inf'}) failed: {exc}"
                ) from exc
            setattr(self, physical_attr, int(getattr(self, physical_attr)) + 1)
        return result

    def _rewrite_agent_insert(self, sql: str, batch: tuple[str, ...]) -> str:
        if not batch:
            raise RuntimeError("Agent persistence batch must not be empty.")

        basic_source = "FROM markorbit_facts.cn_stage_basic AS b"
        agent_join = "LEFT JOIN markorbit_facts.cn_stage_agent AS a"
        if sql.count(basic_source) != 1 or sql.count(agent_join) != 1:
            raise RuntimeError(
                "Legacy cn_agent_current SQL shape changed; expected one BASIC source "
                "and one AGENT join."
            )

        codes = ", ".join(_sql_string(code) for code in batch)
        batch_predicate = f"agent_code IN ({codes})"
        bounded_basic = f"""FROM (
            SELECT *
            FROM markorbit_facts.cn_stage_basic
            WHERE package_id = toUUID('{self._package}')
              AND {batch_predicate}
        ) AS b"""
        bounded_agent = f"""LEFT JOIN (
            SELECT *
            FROM markorbit_facts.cn_stage_agent
            WHERE package_id = toUUID('{self._package}')
              AND {batch_predicate}
        ) AS a"""

        return sql.replace(basic_source, bounded_basic, 1).replace(
            agent_join, bounded_agent, 1
        )

    def _rewrite_application_insert(
        self,
        sql: str,
        *,
        stage_table: str,
        application_range: tuple[str | None, str | None],
    ) -> str:
        if stage_table not in _ALLOWED_APPLICATION_STAGE_TABLES:
            raise RuntimeError(f"Unsupported bounded stage table: {stage_table}")
        source = f"FROM markorbit_facts.{stage_table}"
        if sql.count(source) != 1:
            raise RuntimeError(
                f"Legacy snapshot SQL shape changed; expected one {stage_table} source."
            )

        lower, upper = application_range
        predicate = _application_predicate(lower, upper)
        alias = stage_table.removeprefix("cn_stage_") + "_bounded"
        bounded_source = f"""FROM (
            SELECT *
            FROM markorbit_facts.{stage_table}
            WHERE package_id = toUUID('{self._package}'){predicate}
        ) AS {alias}"""
        return sql.replace(source, bounded_source, 1)
