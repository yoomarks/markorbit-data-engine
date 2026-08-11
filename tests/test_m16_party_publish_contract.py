from pathlib import Path

from app.cn.goods_lifecycle import ApplicationRange
from app.cn.ingest import _party_aggregate_sql
from app.cn.party_publish import (
    PARTY_PUBLISH_TARGET_BASIC_ROWS,
    bounded_party_aggregate_sql,
    party_publish_stage_sql,
)


PACKAGE = "00000000-0000-0000-0000-000000000001"


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
    assert "PARTY_PUBLISH_TARGET_BASIC_ROWS = 250_000" in Path(
        "app/cn/party_publish.py"
    ).read_text(encoding="utf-8")
    assert PARTY_PUBLISH_TARGET_BASIC_ROWS == 250_000


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
