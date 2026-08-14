from pathlib import Path

from app.cn.goods_current_match import bounded_current_items_sql
from app.cn.goods_lifecycle import ApplicationRange


PACKAGE = "00000000-0000-0000-0000-000000000001"


def test_current_match_is_bounded_by_actual_incoming_goods_keys():
    sql = bounded_current_items_sql(
        PACKAGE,
        ApplicationRange("2007001000", "2008001000"),
    )

    assert "FROM markorbit_facts.cn_goods_item_current AS cur FINAL" in sql
    assert "ANY INNER JOIN" in sql
    assert "SELECT application_number, class_no, goods_item_key" in sql
    assert "FROM markorbit_facts.cn_stage_goods" in sql
    assert f"package_id = toUUID('{PACKAGE}')" in sql
    assert "application_number >= '2007001000'" in sql
    assert "application_number < '2008001000'" in sql
    assert "cur.application_number >= '2007001000'" in sql
    assert "cur.application_number < '2008001000'" in sql
    assert "incoming_keys.application_number = cur.application_number" in sql
    assert "incoming_keys.class_no = cur.class_no" in sql
    assert "incoming_keys.goods_item_key = cur.goods_item_key" in sql


def test_m16_wrapper_installs_and_restores_bounded_current_match_builder():
    wrapper = Path("app/cn/ingest_m16.py").read_text(encoding="utf-8")

    assert "from app.cn.goods_current_match import bounded_current_items_sql" in wrapper
    assert "original_current_items_builder = goods._current_items_for_range_sql" in wrapper
    assert "goods._current_items_for_range_sql = bounded_current_items" in wrapper
    assert (
        "goods._current_items_for_range_sql = original_current_items_builder" in wrapper
    )


def test_bounded_match_keeps_authoritative_goods_identity_builder():
    source = Path("app/cn/goods_current_match.py").read_text(encoding="utf-8")

    assert "from app.cn.goods_lifecycle_sql import incoming_goods_sql" in source
    assert "incoming = incoming_goods_sql(" in source
    assert "goods_item_key" in source
    assert "goods_sequence" not in source
    assert "lowerUTF8(goods_name)" not in source
