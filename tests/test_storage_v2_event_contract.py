from pathlib import Path

import pytest

from app.cn.storage_v2_event_compaction import (
    BASELINE_ONLY_EVENT_TYPES,
    BASELINE_WHEN_OLD_EMPTY_EVENT_TYPES,
    KNOWN_EVENT_TYPES,
    _baseline_predicate,
)
from app.cn.storage_v2_events import EventBaselineDeltaClient, insert_case_delta_events


class FakeClient:
    def __init__(self):
        self.commands: list[str] = []

    def command(self, sql: str, *args, **kwargs):
        self.commands.append(sql)
        return None


def test_baseline_policy_is_narrow_and_party_events_are_known_but_kept():
    assert BASELINE_ONLY_EVENT_TYPES == {
        "APPLICATION_OBSERVED",
        "GOODS_SCOPE_OBSERVED",
        "DERIVED_CASE_OBSERVED",
    }
    assert BASELINE_WHEN_OLD_EMPTY_EVENT_TYPES == {
        "PRELIMINARY_PUBLICATION_OBSERVED",
        "REGISTRATION_PUBLICATION_OBSERVED",
        "EXCLUSIVE_TERM_OBSERVED",
    }
    assert "OWNER_RELATION_OBSERVED" in KNOWN_EVENT_TYPES
    assert "AGENT_RELATION_OBSERVED" in KNOWN_EVENT_TYPES
    assert "CO_OWNER_RELATION_OBSERVED" in KNOWN_EVENT_TYPES
    predicate = _baseline_predicate()
    assert "old_value_compact = ''" in predicate
    assert "RELATION_OBSERVED" not in predicate


def test_goods_scope_first_observation_is_rewritten_to_delta_only():
    delegate = FakeClient()
    client = EventBaselineDeltaClient(delegate)
    sql = """
        INSERT INTO markorbit_facts.cn_observed_event
        SELECT if(cur.application_number = '', 'GOODS_SCOPE_OBSERVED',
                  'GOODS_SCOPE_CHANGED_OBSERVED')
        FROM source AS incoming
        LEFT JOIN current AS cur ON 1
        WHERE (cur.application_number = '' OR cur.source_rank < 123)
          AND (cur.application_number = '' OR cur.scope_hash != incoming.scope_hash)
    """
    client.command(sql)
    assert len(delegate.commands) == 1
    rewritten = delegate.commands[0]
    assert "WHERE cur.application_number != ''" in rewritten
    assert "AND cur.source_rank < 123" in rewritten
    assert "AND cur.scope_hash != incoming.scope_hash" in rewritten
    assert client.goods_rewrite_count == 1


def test_derived_case_baseline_event_is_suppressed_but_party_event_passes():
    delegate = FakeClient()
    client = EventBaselineDeltaClient(delegate)
    client.command(
        "INSERT INTO markorbit_facts.cn_observed_event SELECT 'DERIVED_CASE_OBSERVED'"
    )
    client.command(
        "INSERT INTO markorbit_facts.cn_observed_event SELECT 'OWNER_RELATION_OBSERVED'"
    )
    assert client.derived_skip_count == 1
    assert len(delegate.commands) == 1
    assert "OWNER_RELATION_OBSERVED" in delegate.commands[0]


def test_event_adapter_fails_closed_if_goods_sql_shape_drifts():
    client = EventBaselineDeltaClient(FakeClient())
    with pytest.raises(RuntimeError, match="SQL shape changed"):
        client.command(
            "INSERT INTO markorbit_facts.cn_observed_event "
            "SELECT 'GOODS_SCOPE_OBSERVED', 'GOODS_SCOPE_CHANGED_OBSERVED'"
        )


def test_case_event_publisher_emits_only_prior_state_deltas():
    client = FakeClient()
    insert_case_delta_events(
        client,
        package="11111111-1111-4111-8111-111111111111",
        package_kind="BASE",
        source_rank=123,
        case_agg="SELECT * FROM fixture_cases",
    )
    sql = "\n".join(client.commands)
    assert len(client.commands) == 6
    assert "APPLICATION_OBSERVED" not in sql
    assert "CASE_FACTS_CHANGED_OBSERVED" in sql
    assert "PRELIMINARY_PUBLICATION_OBSERVED" in sql
    assert "REGISTRATION_PUBLICATION_OBSERVED" in sql
    assert "EXCLUSIVE_TERM_OBSERVED" in sql
    assert "TERM_EXTENDED_OBSERVED" in sql
    assert sql.count("WHERE cur.application_number != ''") == 6
    assert "if(cur.application_number = '', ''" not in sql


def test_m16_wrapper_installs_and_restores_event_policy_hooks():
    source = Path("app/cn/ingest_m16.py").read_text(encoding="utf-8")
    assert "EventBaselineDeltaClient" in source
    assert "legacy._insert_case_events = events.insert_case_delta_events" in source
    assert "legacy._insert_case_events = original_case_events" in source
    assert "event_delta_client.assert_rewrite_counts()" in source
    assert 'metrics["observed_event_history_policy"] = "TRUE_DELTA_PLUS_PARTY_V2"' in source


def test_powershell_wrapper_never_starts_persistent_worker():
    source = Path("scripts/compact-cn-observed-event.ps1").read_text(encoding="utf-8")
    lowered = source.lower()
    assert "docker compose run --rm --no-deps" in lowered
    assert "docker compose up -d worker" not in lowered
    assert "docker compose start worker" not in lowered
    assert "docker compose stop worker" in lowered
    assert "app.cn.storage_v2_event_compaction" in source
