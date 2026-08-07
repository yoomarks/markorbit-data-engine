from pathlib import Path

from app.cn.goods_lifecycle import (
    GOODS_ITEM_IDENTITY_VERSION,
    incoming_goods_sql,
    scope_from_current_items_sql,
)


def test_monthly_scope_is_rebuilt_from_durable_current_items():
    sql = scope_from_current_items_sql("00000000-0000-0000-0000-000000000001")
    assert "cn_goods_item_current AS item FINAL" in sql
    assert "cn_stage_goods" in sql  # touched-scope selector only
    assert "INNER JOIN" in sql
    assert "item.application_number" in sql
    assert "item.class_no" in sql


def test_goods_item_identity_uses_strict_source_fields_not_sequence_alone():
    sql = incoming_goods_sql("00000000-0000-0000-0000-000000000001")
    assert GOODS_ITEM_IDENTITY_VERSION == "CN_GOODS_ITEM_ID_V2_STRICT_SOURCE_FIELDS"
    assert "'|SEQ|', goods_sequence" in sql
    assert "'|GROUP|', similar_group" in sql
    assert "'|NAME|', lowerUTF8(goods_name)" in sql
    assert "goods_sequence != ''" not in sql


def test_ingest_entrypoint_routes_through_m16_wrapper():
    source = Path("app/jobs.py").read_text(encoding="utf-8")
    assert "from app.cn.ingest_m16 import ingest_cn_package" in source


def test_goods_codes_never_encode_legal_cause_in_mapping():
    source = Path("app/cn/status.py").read_text(encoding="utf-8")
    assert "EMPIRICAL_CODE_1_INACTIVE_HIGH_CONFIDENCE" in source
    assert "EMPIRICAL_CODE_2_FINAL_INACTIVE" in source
    assert "NONRENEWAL" not in source
    assert "PARTIAL_REFUSAL" not in source


def test_schema_contains_item_current_observation_and_scope_lifecycle():
    source = Path("database/clickhouse/init/003_m16_goods_lifecycle.sql").read_text(
        encoding="utf-8"
    )
    assert "cn_goods_item_current" in source
    assert "cn_goods_item_observation" in source
    assert "cn_goods_scope_lifecycle_current" in source
    assert "all_known_goods_inactive" in source
    assert "code_2_item_count" in source
