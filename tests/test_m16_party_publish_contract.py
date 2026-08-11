from pathlib import Path

import pytest

from app.cn.goods_lifecycle import ApplicationRange
from app.cn.ingest import _party_aggregate_sql
from app.cn.party_publish import (
    PARTY_PUBLISH_TARGET_BASIC_ROWS,
    PartyHistoryDeltaClient,
    bounded_party_aggregate_sql,
    party_publish_stage_sql,
)


PACKAGE = "00000000-0000-0000-0000-000000000001"


class _CommandDelegate:
    def __init__(self):
        self.commands: list[str] = []

    def command(self, sql: str, *args, **kwargs):
        self.commands.append(sql)
        return "ok"


def _legacy_observed_current_sql() -> str:
    return """
        INSERT INTO markorbit_facts.cn_case_party_relation_history
        SELECT
            generateUUIDv4(), incoming.relation_id, incoming.case_id,
            incoming.application_number, incoming.role, 'OBSERVED_CURRENT',
            incoming.relation_key
        FROM (SELECT * FROM markorbit_facts.cn_stage_party_publish) AS incoming
    """


def test_party_aggregate_filters_are_pushed_to_physical_stage_sources():
    sql = bounded_party_aggregate_sql(
        PACKAGE,
        ApplicationRange("2015001000", "2016001000"),
        _party_aggregate_sql,
    )

    assert "FROM markorbit_facts.cn_stage_applicant" in sql
    assert "FROM markorbit_facts.cn_stage_coowner" in sql
    assert sql.count("FROM markorbit_facts.cn_stage_basic") == 2
    assert "FROM markorbit_facts.cn_stage_agent" in sql
    assert sql.count("application_number >= '2015001000'") >= 5
    assert sql.count("application_number < '2016001000'") >= 5
    assert "co.application_number >= '2015001000'" in sql
    assert "b.application_number >= '2015001000'" in sql


def test_party_publish_snapshot_preserves_legacy_relation_shape():
    sql = party_publish_stage_sql(PACKAGE)
    wrapper = Path("app/cn/ingest_m16.py").read_text(encoding="utf-8")

    assert "FROM markorbit_facts.cn_stage_party_publish" in sql
    assert "case_id, application_number, role, relation_id, relation_key" in sql
    assert "party.materialize_party_publish_stage" in wrapper
    assert "legacy._party_aggregate_sql = lambda package: party.party_publish_stage_sql(package)" in wrapper
    assert "PartyHistoryDeltaClient" in wrapper
    assert 'metrics["party_history_policy"] = "DELTA_ONLY_V1"' in wrapper
    assert "PARTY_PUBLISH_TARGET_BASIC_ROWS = 250_000" in Path(
        "app/cn/party_publish.py"
    ).read_text(encoding="utf-8")
    assert PARTY_PUBLISH_TARGET_BASIC_ROWS == 250_000


def test_party_history_observed_current_is_delta_only():
    delegate = _CommandDelegate()
    client = PartyHistoryDeltaClient(delegate, source_rank=42)

    assert client.command(_legacy_observed_current_sql()) == "ok"
    client.assert_observed_current_rewritten()

    sql = delegate.commands[0]
    assert "LEFT JOIN markorbit_facts.cn_case_party_current AS cur FINAL" in sql
    assert "cur.application_number = incoming.application_number" in sql
    assert "cur.role = incoming.role" in sql
    assert "cur.relation_key = incoming.relation_key" in sql
    assert "cur.source_rank < 42" in sql
    assert "cur.is_current = 0" in sql
    assert "cur.record_hash != incoming.record_hash" in sql


def test_party_history_delta_adapter_passes_other_commands_through():
    delegate = _CommandDelegate()
    client = PartyHistoryDeltaClient(delegate, source_rank=42)
    sql = "SELECT 1"

    assert client.command(sql) == "ok"
    assert delegate.commands == [sql]
    assert client.rewrite_count == 0


def test_party_history_delta_adapter_fails_closed_on_legacy_shape_drift():
    delegate = _CommandDelegate()
    client = PartyHistoryDeltaClient(delegate, source_rank=42)
    malformed = _legacy_observed_current_sql().rstrip() + " WHERE 1 = 1"

    with pytest.raises(RuntimeError, match="SQL shape changed"):
        client.command(malformed)

    with pytest.raises(RuntimeError, match="expected exactly one"):
        client.assert_observed_current_rewritten()


def test_party_publish_stage_is_transient_and_in_init_schema():
    schema = Path("database/clickhouse/init/003_m16_goods_lifecycle.sql").read_text(
        encoding="utf-8"
    )
    module = Path("app/cn/party_publish.py").read_text(encoding="utf-8")

    assert "cn_stage_party_publish" in schema
    assert "ORDER BY (package_id, application_number, role, relation_key)" in schema
    assert "TTL toDateTime(ingested_at) + INTERVAL 7 DAY DELETE" in schema
    assert "cleanup_party_publish_stage" in module
    assert "Legacy party aggregate SQL shape changed" in module
