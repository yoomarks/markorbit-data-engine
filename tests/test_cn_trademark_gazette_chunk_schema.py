from pathlib import Path


SQL = Path(
    "database/clickhouse/init/023_cn_trademark_gazette_chunk_admission.sql"
).read_text(encoding="utf-8")


def test_chunk_stage_table_is_range_scoped_and_versioned():
    assert "cn_trademark_gazette_admission_chunk" in SQL
    for field in (
        "announcement_issue UInt32",
        "announcement_date Nullable(Date32)",
        "source_dataset_sha256 FixedString(64)",
        "range_start_page UInt32",
        "range_end_page UInt32",
        "source_record_count UInt64",
        "source_page_count UInt64",
        "page_size UInt32",
        "chunk_row_count UInt64",
        "query_scope LowCardinality(String)",
        "chunk_fingerprint FixedString(64)",
    ):
        assert field in SQL
    assert "CN_TRADEMARK_GAZETTE_SCHEMA_V2" in SQL


def test_chunk_stage_order_key_uses_dataset_and_page_range_not_registration_number():
    assert (
        "announcement_issue,\n    source_dataset_sha256,\n"
        "    range_start_page,\n    range_end_page"
    ) in SQL
    assert "registration_number" not in SQL
    assert "source_row_id" not in SQL


def test_chunk_stage_has_no_business_workspace_or_duplicated_trademark_entity_fields():
    lowered = SQL.lower()
    for forbidden in (
        "workspace_id",
        "customer_id",
        "client_id",
        "matter_id",
        "opportunity",
        "applicant_name",
        "trademark_name",
        "nice_class",
        "application_date",
        "agent_name",
    ):
        assert forbidden not in lowered
