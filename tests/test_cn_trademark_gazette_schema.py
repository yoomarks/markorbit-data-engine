from pathlib import Path


SCHEMA = Path("database/clickhouse/init/022_cn_trademark_gazette.sql")


def test_cn_trademark_gazette_schema_has_issue_catalog_and_minimal_announcement_events() -> None:
    sql = SCHEMA.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS markorbit_facts.cn_trademark_gazette_issue" in sql
    assert "announcement_issue UInt32" in sql
    assert "announcement_date Nullable(Date32)" in sql
    assert "record_count UInt64" in sql
    assert "page_count UInt64" in sql
    assert "capture_page_size UInt32" in sql
    assert "capture_completeness LowCardinality(String)" in sql
    assert "source_dataset_sha256 FixedString(64)" in sql
    assert "ORDER BY (announcement_issue, observation_fingerprint)" in sql

    assert "CREATE TABLE IF NOT EXISTS markorbit_facts.cn_trademark_gazette_announcement" in sql
    assert "source_row_id String" in sql
    assert "source_search_id String" in sql
    assert "registration_number String" in sql
    assert "announcement_type_code String" in sql
    assert "announcement_type_name String" in sql
    assert "detail_page_no Nullable(UInt32)" in sql
    assert "announcement_page_count Nullable(UInt32)" in sql
    assert "detail_file_id String" in sql
    assert "detail_asset_path String" in sql
    assert "announcement_detail_url String" in sql
    assert "detail_resolution_status LowCardinality(String)" in sql
    assert "source_row_fingerprint FixedString(64)" in sql
    assert "ORDER BY (announcement_issue, source_row_id)" in sql


def test_cn_trademark_gazette_schema_does_not_duplicate_trademark_entity_facts() -> None:
    sql = SCHEMA.read_text(encoding="utf-8")

    duplicated_fields = (
        "applicant_name",
        "applicant_address",
        "applicant_en_name",
        "applicant_en_address",
        "agent_name",
        "trademark_name",
        "nice_class",
        "application_date",
    )
    for field in duplicated_fields:
        assert field not in sql

    assert "registration_number String" in sql


def test_cn_trademark_gazette_schema_keeps_detail_as_source_reference_not_document_body() -> None:
    sql = SCHEMA.read_text(encoding="utf-8")

    assert "announcement_detail_url String" in sql
    assert "detail_asset_path String" in sql
    assert "detail_file_id String" in sql
    assert "document_body" not in sql
    assert "pdf_bytes" not in sql
    assert "image_bytes" not in sql
    assert "customer" not in sql.lower()
    assert "workspace" not in sql.lower()


def test_cn_trademark_gazette_schema_uses_official_row_id_not_registration_number_as_identity() -> None:
    sql = SCHEMA.read_text(encoding="utf-8")

    assert "ORDER BY (registration_number" not in sql
    assert "ORDER BY (announcement_issue, source_row_id)" in sql
    assert "source_row_fingerprint FixedString(64)" in sql
    assert "CN_TRADEMARK_GAZETTE_SCHEMA_V1" in sql
