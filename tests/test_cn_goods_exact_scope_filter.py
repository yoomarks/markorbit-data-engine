from pathlib import Path

from app.cn import goods_lifecycle as goods
from app.cn.goods_scope_match import exact_touched_scope_sql


PACKAGE = "00000000-0000-0000-0000-000000000001"


def _rewrite(builder):
    lower = "2007001000"
    upper = "2008001000"
    touched = goods.touched_scope_sql(PACKAGE, lower, upper)
    return exact_touched_scope_sql(builder(PACKAGE, lower, upper), touched)


def test_scope_snapshot_prunes_durable_rows_by_exact_touched_scope():
    sql = _rewrite(goods.scope_from_current_items_sql)

    assert "FROM markorbit_facts.cn_goods_item_current AS item FINAL" in sql
    assert "PREWHERE (item.application_number, item.class_no) IN (" in sql
    assert "SELECT DISTINCT application_number, class_no" in sql
    assert "FROM markorbit_facts.cn_stage_goods" in sql
    assert f"package_id = toUUID('{PACKAGE}')" in sql
    assert "INNER JOIN" not in sql
    assert "item.application_number >= '2007001000'" in sql
    assert "item.application_number < '2008001000'" in sql


def test_lifecycle_scope_prunes_durable_rows_by_exact_touched_scope():
    sql = _rewrite(goods._lifecycle_scope_sql)

    assert "FROM markorbit_facts.cn_goods_item_current AS item FINAL" in sql
    assert "PREWHERE (item.application_number, item.class_no) IN (" in sql
    assert "SELECT DISTINCT application_number, class_no" in sql
    assert "INNER JOIN" not in sql
    assert "GROUP BY item.application_number, item.class_no" in sql


def test_scope_rewrite_fails_closed_on_unexpected_sql_shape():
    touched = goods.touched_scope_sql(PACKAGE, "100", "200")
    try:
        exact_touched_scope_sql("SELECT 1", touched)
    except RuntimeError as exc:
        assert "expected one touched-scope JOIN" in str(exc)
    else:
        raise AssertionError("unexpected SQL shape must fail closed")


def test_m16_wrapper_installs_and_restores_exact_scope_builders():
    wrapper = Path("app/cn/ingest_m16.py").read_text(encoding="utf-8")

    assert "from app.cn.goods_scope_match import exact_touched_scope_sql" in wrapper
    assert "original_scope_builder = goods.scope_from_current_items_sql" in wrapper
    assert "original_lifecycle_scope_builder = goods._lifecycle_scope_sql" in wrapper
    assert "goods.scope_from_current_items_sql = exact_scope_from_current_items" in wrapper
    assert "goods._lifecycle_scope_sql = exact_lifecycle_scope" in wrapper
    assert "goods._lifecycle_scope_sql = original_lifecycle_scope_builder" in wrapper
    assert "goods.scope_from_current_items_sql = original_scope_builder" in wrapper
