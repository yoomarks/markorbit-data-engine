from app.cn.goods_lifecycle_sql import incoming_goods_sql


def test_m16_goods_query_uses_private_aggregate_alias_boundary():
    sql = incoming_goods_sql("00000000-0000-0000-0000-000000000001")

    assert "AS agg_goods_name" in sql
    assert "aggregated.agg_goods_name AS goods_name" in sql
    assert "lowerUTF8(aggregated.agg_goods_name) AS goods_name_norm" in sql
    assert "argMax(goods_name, toUInt64(stage_source_start_line)) AS goods_name" not in sql
    assert "argMax(source_file, toUInt64(stage_source_start_line)) AS source_file" not in sql
