from __future__ import annotations

from typing import Any
import uuid


CN_AGENT_PERSIST_TARGET_ROWS = 100_000
CN_AGENT_PERSIST_MAX_CODES = 1_000
_AGENT_INSERT = "INSERT INTO markorbit_facts.cn_agent_current"


def _sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


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
    """Bound the remaining whole-package agent aggregate in legacy persistence.

    M1.6 intentionally reuses the mature legacy snapshot writer after CASE,
    PARTY and GOODS have been materialized in bounded stages. The one remaining
    high-amplification aggregate is ``cn_agent_current``: it groups all BASIC
    rows for the whole package and builds a sorted ``groupArray(row_hash)``.

    This adapter splits exactly that INSERT by complete ``agent_code`` groups.
    Every agent is emitted once and still sees all of its package rows, so the
    legacy lineage hash and current-row semantics stay unchanged. Other legacy
    snapshot statements pass through untouched, but failures receive a precise
    subphase label for the next real-corpus diagnosis.
    """

    def __init__(
        self,
        delegate: Any,
        *,
        package_uuid: uuid.UUID | str,
        agent_batches: list[tuple[str, ...]],
    ) -> None:
        self._delegate = delegate
        self._package = str(package_uuid)
        self._agent_batches = list(agent_batches)
        self._agent_insert_seen = 0
        self._physical_agent_commands = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    @property
    def agent_chunk_count(self) -> int:
        return len(self._agent_batches)

    @property
    def agent_code_count(self) -> int:
        return sum(len(batch) for batch in self._agent_batches)

    @property
    def physical_agent_commands(self) -> int:
        return self._physical_agent_commands

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if _AGENT_INSERT in sql:
            return self._command_agent_current(sql, *args, **kwargs)
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
