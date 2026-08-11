from __future__ import annotations

import pytest

from app.cn.storage_v2_party_history import PartyHistorySuppressionClient


class _Delegate:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def command(self, sql: str, *args, **kwargs):
        self.commands.append(sql)
        return "delegated"


def test_party_history_suppression_skips_exact_legacy_actions() -> None:
    delegate = _Delegate()
    client = PartyHistorySuppressionClient(delegate)

    assert (
        client.command(
            "INSERT INTO markorbit_facts.cn_case_party_relation_history "
            "SELECT 'SUPERSEDED'"
        )
        is None
    )
    assert (
        client.command(
            "INSERT INTO markorbit_facts.cn_case_party_relation_history "
            "SELECT 'OBSERVED_CURRENT'"
        )
        is None
    )
    client.assert_suppression_complete()
    assert delegate.commands == []


def test_party_history_suppression_passes_other_commands_through() -> None:
    delegate = _Delegate()
    client = PartyHistorySuppressionClient(delegate)
    assert client.command("SELECT 1") == "delegated"
    assert delegate.commands == ["SELECT 1"]


def test_party_history_suppression_fails_closed_on_unknown_action() -> None:
    delegate = _Delegate()
    client = PartyHistorySuppressionClient(delegate)
    with pytest.raises(RuntimeError, match="unknown or ambiguous"):
        client.command(
            "INSERT INTO markorbit_facts.cn_case_party_relation_history "
            "SELECT 'SOMETHING_NEW'"
        )


def test_party_history_suppression_fails_closed_on_duplicate_action() -> None:
    delegate = _Delegate()
    client = PartyHistorySuppressionClient(delegate)
    client.command(
        "INSERT INTO markorbit_facts.cn_case_party_relation_history "
        "SELECT 'SUPERSEDED'"
    )
    with pytest.raises(RuntimeError, match="duplicate"):
        client.command(
            "INSERT INTO markorbit_facts.cn_case_party_relation_history "
            "SELECT 'SUPERSEDED'"
        )
