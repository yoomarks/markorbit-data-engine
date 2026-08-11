from pathlib import Path

from app.cn.goods_lifecycle import (
    GOODS_ITEM_IDENTITY_VERSION,
    ApplicationRange,
    _plan_goods_application_ranges,
    scope_from_current_items_sql,
    scope_publish_stage_sql,
)
from app.cn.goods_lifecycle_sql import incoming_goods_sql as runtime_incoming_goods_sql


class _Result:
    def __init__(self, rows):
        self.result_rows = rows


class _BoundaryClient:
    """Simulate a package where application B alone exceeds the row target."""

    def query(self, sql: str):
        if "application_number > 'B'" in sql:
            return _Result([("C",)])
        if "application_number >= 'C'" in sql:
            return _Result([])
        if "application_number >= 'B'" in sql:
            return _Result([("B",)])
        return _Result([("B",)])


def test_monthly_scope_is_rebuilt_from_durable_current_items():
    sql = scope_from_current_items_sql("00000000-0000-0000-0000-000000000001")
    assert "cn_goods_item_current AS item FINAL" in sql
    assert "cn_stage_goods" in sql  # touched-scope selector only
    assert "INNER JOIN" in sql
    assert "item.application_number" in sql
    assert "item.class_no" in sql


def test_runtime_goods_item_identity_uses_strict_source_fields_not_sequence_alone():
    sql = runtime_incoming_goods_sql("00000000-0000-0000-0000-000000000001")
    assert GOODS_ITEM_IDENTITY_VERSION == "CN_GOODS_ITEM_ID_V2_STRICT_SOURCE_FIELDS"
    assert "'|SEQ|', goods_sequence" in sql
    assert "'|GROUP|', similar_group" in sql
    assert "'|NAME|', lowerUTF8(goods_name)" in sql
    assert "goods_sequence != ''" not in sql


def test_large_goods_publish_range_is_applied_at_stage_source():
    sql = runtime_incoming_goods_sql(
        "00000000-0000-0000-0000-000000000001",
        "2007001000",
        "2008001000",
    )
    assert "FROM markorbit_facts.cn_stage_goods" in sql
    assert "application_number >= '2007001000'" in sql
    assert "application_number < '2008001000'" in sql

    application_range = ApplicationRange("2007001000", "2008001000")
    assert application_range.predicate("item.application_number") == (
        "item.application_number >= '2007001000' AND "
        "item.application_number < '2008001000'"
    )


def test_chunk_planner_never_splits_one_application_across_ranges():
    ranges = _plan_goods_application_ranges(
        "00000000-0000-0000-0000-000000000001",
        client=_BoundaryClient(),
        target_rows=3,
    )
    assert ranges == [
        ApplicationRange(None, "B"),
        ApplicationRange("B", "C"),
        ApplicationRange("C", None),
    ]


def test_compact_scope_snapshot_is_the_legacy_publish_source():
    sql = scope_publish_stage_sql("00000000-0000-0000-0000-000000000001")
    wrapper = Path("app/cn/ingest_m16.py").read_text(encoding="utf-8")
    lifecycle = Path("app/cn/goods_lifecycle.py").read_text(encoding="utf-8")

    assert "FROM markorbit_facts.cn_stage_scope_publish" in sql
    assert "goods.scope_publish_stage_sql" in wrapper
    assert "GOODS_PUBLISH_TARGET_STAGE_ROWS = 1_000_000" in lifecycle
    assert "for application_range in application_ranges" in lifecycle
    assert "_insert_scope_publish_stage" in lifecycle


def test_goods_observation_history_excludes_noop_reobservations():
    lifecycle = Path("app/cn/goods_lifecycle.py").read_text(encoding="utf-8")
    observation_sql = lifecycle.split(
        "INSERT INTO markorbit_facts.cn_goods_item_observation", 1
    )[1].split("INSERT INTO markorbit_facts.cn_goods_item_current", 1)[0]

    assert "'REOBSERVED'" not in observation_sql
    assert "cur.application_number = '', 'FIRST_OBSERVED'" in observation_sql
    assert "'STATUS_CHANGED'" in observation_sql
    assert "'ITEM_DETAILS_CHANGED'" in observation_sql
    assert "cur.goods_status_raw != incoming.goods_status_raw" in observation_sql
    assert "OR cur.record_hash != incoming.record_hash" in observation_sql


def test_ingest_entrypoint_routes_through_m16_wrapper_and_runtime_builder():
    jobs = Path("app/jobs.py").read_text(encoding="utf-8")
    wrapper = Path("app/cn/ingest_m16.py").read_text(encoding="utf-8")
    assert "from app.cn.ingest_m16 import ingest_cn_package" in jobs
    assert "from app.cn.goods_lifecycle_sql import (" in wrapper
    assert "incoming_goods_sql," in wrapper
    assert "goods.incoming_goods_sql = incoming_goods_sql" in wrapper


def test_goods_codes_never_encode_legal_cause_in_mapping():
    source = Path("app/cn/status.py").read_text(encoding="utf-8")
    assert "EMPIRICAL_CODE_1_INACTIVE_HIGH_CONFIDENCE" in source
    assert "EMPIRICAL_CODE_2_FINAL_INACTIVE" in source
    assert "NONRENEWAL" not in source
    assert "PARTIAL_REFUSAL" not in source


def test_schema_contains_item_current_observation_scope_lifecycle_and_publish_stage():
    source = Path("database/clickhouse/init/003_m16_goods_lifecycle.sql").read_text(
        encoding="utf-8"
    )
    assert "cn_goods_item_current" in source
    assert "cn_goods_item_observation" in source
    assert "cn_goods_scope_lifecycle_current" in source
    assert "cn_stage_scope_publish" in source
    assert "ORDER BY (package_id, application_number, class_no)" in source
    assert "all_known_goods_inactive" in source
    assert "code_2_item_count" in source
