from pathlib import Path


SCHEMA = Path("database/clickhouse/init/004_us_m1_core.sql")


def test_us_m1_schema_has_core_durable_tables() -> None:
    source = SCHEMA.read_text(encoding="utf-8")
    for table in (
        "us_case_current",
        "us_owner_current",
        "us_classification_current",
        "us_event_history",
        "us_statement_current",
    ):
        assert f"markorbit_facts.{table}" in source
    assert "'US_CORE', 'US_M1.0'" in source


def test_us_m1_preserves_official_status_without_inferred_legal_status() -> None:
    source = SCHEMA.read_text(encoding="utf-8")
    assert "status_code String" in source
    assert "status_date Nullable(Date32)" in source
    assert "legal_status" not in source
    assert "inferred_status" not in source


def test_us_m1_preserves_partial_first_use_dates_raw() -> None:
    source = SCHEMA.read_text(encoding="utf-8")
    assert "first_use_anywhere_raw String" in source
    assert "first_use_commerce_raw String" in source
