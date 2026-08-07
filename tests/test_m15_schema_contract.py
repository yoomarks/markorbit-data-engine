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
        "source_first_line",
        "source_last_line",
        "source_rank",
    ]
    for field in required:
        assert field in schema


def test_no_in_place_m14_semantics():
    schema = Path("database/clickhouse/init/001_fact_schema.sql").read_text(encoding="utf-8")
    assert "ReplacingMergeTree(source_rank" in schema
    assert "ReplacingMergeTree(version" not in schema


def test_current_and_history_contract_uses_lineage_fields():
    schema = Path("database/clickhouse/init/001_fact_schema.sql").read_text(encoding="utf-8")
    tables = [
        "cn_case_current",
        "cn_case_scope_current",
        "cn_case_party_current",
        "cn_case_party_relation_history",
        "cn_agent_current",
        "cn_priority_current",
        "cn_madrid_current",
        "cn_observed_event",
    ]

    for index, table_name in enumerate(tables):
        marker = f"CREATE TABLE IF NOT EXISTS markorbit_facts.{table_name}"
        start = schema.index(marker)
        next_marker = (
            schema.index(f"CREATE TABLE IF NOT EXISTS markorbit_facts.{tables[index + 1]}", start)
            if index + 1 < len(tables)
            else len(schema)
        )
        block = schema[start:next_marker]
        assert "source_first_line UInt64" in block
        assert "source_last_line UInt64" in block
        assert "source_start_line UInt64" not in block
        assert "source_end_line UInt64" not in block
