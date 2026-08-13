from app.cn import case_publish
from app.cn.goods_lifecycle import ApplicationRange
from app.cn.ingest import _case_aggregate_sql


def test_bounded_case_aggregate_pushes_range_to_basic_source() -> None:
    package = "11111111-1111-1111-1111-111111111111"
    sql = case_publish.bounded_case_aggregate_sql(
        package,
        ApplicationRange(lower="100", upper="200"),
        _case_aggregate_sql,
    )

    assert "FROM markorbit_facts.cn_stage_basic" in sql
    assert f"WHERE package_id = toUUID('{package}')" in sql
    assert "application_number >= '100'" in sql
    assert "application_number < '200'" in sql
    assert sql.count("GROUP BY case_id, application_number") == 1


def test_case_publish_stage_reuses_compact_snapshot() -> None:
    package = "11111111-1111-1111-1111-111111111111"
    sql = case_publish.case_publish_stage_sql(package)

    assert "FROM markorbit_facts.cn_stage_case_publish" in sql
    assert f"WHERE package_id = toUUID('{package}')" in sql
    assert "GROUP BY" not in sql
    assert "cn_stage_basic" not in sql


def test_m16_wires_case_snapshot_into_legacy_publisher() -> None:
    source = open("app/cn/ingest_m16.py", encoding="utf-8").read()

    assert "_LEGACY_CASE_AGG = legacy._case_aggregate_sql" in source
    assert "case.materialize_case_publish_stage" in source
    assert "legacy._case_aggregate_sql = lambda package: case.case_publish_stage_sql(package)" in source
    assert "case.cleanup_case_publish_stage(package_uuid)" in source
