from __future__ import annotations

from typing import Any


class PartyHistorySuppressionClient:
    """Suppress duplicate permanent PARTY relation-history writes in M1.6.

    ``cn_observed_event`` already receives canonical OWNER/CO_OWNER/AGENT
    relation observations and supersessions before the legacy publisher writes
    the parallel ``cn_case_party_relation_history`` row. Storage V2 keeps the
    event stream as the durable PARTY history and stops persisting the duplicate
    wide relation-history copy.

    The adapter is deliberately narrow and fail-closed: exactly the two legacy
    history INSERTs (SUPERSEDED and OBSERVED_CURRENT) must be encountered once
    per publish. All other ClickHouse commands and queries pass through.
    """

    _TARGET_TABLE = "INSERT INTO markorbit_facts.cn_case_party_relation_history"
    _KNOWN_ACTIONS = ("'SUPERSEDED'", "'OBSERVED_CURRENT'")

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self._skipped_actions: list[str] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    @property
    def skipped_actions(self) -> tuple[str, ...]:
        return tuple(self._skipped_actions)

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if self._TARGET_TABLE not in sql:
            return self._delegate.command(sql, *args, **kwargs)

        matched = [action for action in self._KNOWN_ACTIONS if action in sql]
        if len(matched) != 1:
            raise RuntimeError(
                "Storage V2 found an unknown or ambiguous CN party-history INSERT; "
                "refusing to suppress it."
            )
        action = matched[0]
        if action in self._skipped_actions:
            raise RuntimeError(
                f"Storage V2 saw duplicate CN party-history action {action} in one publish."
            )
        self._skipped_actions.append(action)
        return None

    def assert_suppression_complete(self) -> None:
        expected = set(self._KNOWN_ACTIONS)
        actual = set(self._skipped_actions)
        if actual != expected or len(self._skipped_actions) != 2:
            raise RuntimeError(
                "Storage V2 expected exactly the SUPERSEDED and OBSERVED_CURRENT "
                "CN party-history INSERTs to be suppressed; got "
                f"{self._skipped_actions!r}. Legacy publisher shape changed."
            )
