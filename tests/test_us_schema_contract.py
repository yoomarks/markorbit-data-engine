from pathlib import Path


CORE_SCHEMA = Path("database/clickhouse/init/004_us_m1_core.sql")
M11_SCHEMA = Path("database/clickhouse/init/005_us_m11_real_tdxf.sql")
M12_SCHEMA = Path("database/clickhouse/init/006_us_m12_snapshot_semantics.sql")
M13_SCHEMA = Path("database/clickhouse/init/007_us_m13_official_fact_families.sql")
M14_SCHEMA = Path("database/clickhouse/init/008_us_m14_change_history.sql")


def test_us_schema_has_core_durable_tables_and_version_upgrades() -> None:
    core = CORE_SCHEMA.read_text(encoding="utf-8")
    m11 = M11_SCHEMA.read_text(encoding="utf-8")
    m12 = M12_SCHEMA.read_text(encoding="utf-8")
    m13 = M13_SCHEMA.read_text(encoding="utf-8")
    m14 = M14_SCHEMA.read_text(encoding="utf-8")
    for table in (
        "us_case_current",
        "us_owner_current",
        "us_classification_current",
        "us_event_history",
        "us_statement_current",
    ):
        assert f"markorbit_facts.{table}" in core
    for table in (
        "us_correspondent_current",
        "us_design_search_current",
        "us_prior_registration_current",
        "us_foreign_application_current",
        "us_madrid_filing_current",
        "us_madrid_event_history",
    ):
        assert f"markorbit_facts.{table}" in m13
    assert "markorbit_facts.us_case_observation_history" in m14
    assert "owner_set_hash FixedString(64)" in m14
    assert "owner_record_set_hash FixedString(64)" in m14
    assert "source_package_id UUID" in m14
    assert "source_rank UInt64" in m14
    assert "'US_CORE', 'US_M1.0'" in core
    assert "'US_CORE', 'US_M1.1'" in m11
    assert "'US_CORE', 'US_M1.2'" in m12
    assert "'US_CORE', 'US_M1.3'" in m13
    assert "'US_CORE', 'US_M1.4'" in m14


def test_us_m11_models_real_tdxf_fields() -> None:
    source = M11_SCHEMA.read_text(encoding="utf-8")
    for field in (
        "transaction_date",
        "use_1a_filed",
        "use_1a_current",
        "intent_to_use_1b_filed",
        "intent_to_use_1b_current",
        "madrid_66a_filed",
        "madrid_66a_current",
        "section_8_accepted",
        "international_registration_date",
        "entity_statement",
        "description_text",
    ):
        assert field in source


def test_us_m13_models_only_official_fact_fields() -> None:
    source = M13_SCHEMA.read_text(encoding="utf-8")
    for field in (
        "attorney_name",
        "attorney_docket_number",
        "domestic_representative_name",
        "foreign_priority_claimed",
        "reference_number",
        "original_filing_date_uspto",
        "filing_reference_number",
        "description_text",
    ):
        assert field in source
    for inferred in ("has_attorney", "is_pro_se", "role_inferred"):
        assert inferred not in source


def test_us_preserves_official_status_without_inferred_legal_status() -> None:
    core = CORE_SCHEMA.read_text(encoding="utf-8")
    upgrades = "".join(
        path.read_text(encoding="utf-8")
        for path in (M11_SCHEMA, M12_SCHEMA, M13_SCHEMA, M14_SCHEMA)
    )
    source = core + upgrades
    assert "status_code String" in core
    assert "status_date Nullable(Date32)" in core
    assert "legal_status" not in source
    assert "inferred_status" not in source


def test_us_preserves_partial_first_use_dates_raw() -> None:
    source = CORE_SCHEMA.read_text(encoding="utf-8")
    assert "first_use_anywhere_raw String" in source
    assert "first_use_commerce_raw String" in source
