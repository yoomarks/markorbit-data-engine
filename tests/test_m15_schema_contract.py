from pathlib import Path


def test_m15_permanent_fields_are_present():
    schema = Path("database/clickhouse/init/001_fact_schema.sql").read_text(encoding="utf-8")
    required = [
        "exclusive_period_raw",
        "color_description",
        "exclusive_rights_disclaimer",
        "is_3d_mark",
        "is_co_application",
        "mark_form_raw",
        "geo_indication_info",
        "color_mark_flag",
        "is_well_known_mark",
        "unmapped_status_item_count",
        "interpretation_complete",
        "goods_status_mapping_version",
        "relation_key",
        "cn_case_relation_current",
        "cn_scope_carve_out_current",
        "source_file",
        "source_start_line",
        "source_rank",
    ]
    for field in required:
        assert field in schema


def test_no_in_place_m14_semantics():
    schema = Path("database/clickhouse/init/001_fact_schema.sql").read_text(encoding="utf-8")
    assert "ReplacingMergeTree(source_rank" in schema
    assert "ReplacingMergeTree(version" not in schema
