from __future__ import annotations

from typing import Any


class GoodsObservationDeltaClient:
    """Make M1.6 goods observation history true-delta-only.

    ``cn_goods_item_current`` already carries durable ``first_source_*`` provenance
    for every goods item. Persisting a second full-width ``FIRST_OBSERVED`` row for
    every item therefore duplicates the baseline at corpus scale. This narrow
    adapter rewrites only the M1.6 goods-observation INSERT so first observations
    stay in current-state provenance and only real status/detail changes enter the
    permanent observation table.

    All non-target ClickHouse calls pass through untouched. The adapter fails
    closed if the expected SQL shape changes.
    """

    _TARGET = "INSERT INTO markorbit_facts.cn_goods_item_observation"
    _OLD_PREDICATE = "WHERE cur.application_number = ''\n               OR ("
    _NEW_PREDICATE = "WHERE cur.application_number != ''\n               AND ("

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self._rewrite_count = 0

    @property
    def rewrite_count(self) -> int:
        return self._rewrite_count

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if self._TARGET in sql:
            sql = self._rewrite_observation_insert(sql)
        return self._delegate.command(sql, *args, **kwargs)

    def assert_rewrite_count(self, expected: int) -> None:
        if self._rewrite_count != int(expected):
            raise RuntimeError(
                "Storage V2 expected one CN goods observation rewrite per publish "
                f"chunk: expected {expected}, rewrote {self._rewrite_count}. "
                "M1.6 publisher SQL shape changed."
            )

    def _rewrite_observation_insert(self, sql: str) -> str:
        if self._OLD_PREDICATE not in sql:
            raise RuntimeError(
                "Storage V2 could not find the expected CN goods baseline predicate; "
                "refusing to guess at a changed publisher SQL shape."
            )
        if self._NEW_PREDICATE in sql:
            raise RuntimeError(
                "Storage V2 CN goods observation predicate appears to be rewritten twice."
            )
        self._rewrite_count += 1
        return sql.replace(self._OLD_PREDICATE, self._NEW_PREDICATE, 1)
