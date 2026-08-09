from pathlib import Path


def test_reference_schema_is_separate_versioned_and_single_active() -> None:
    source = Path("database/postgres/init/002_us_status_reference.sql").read_text(
        encoding="utf-8"
    )
    assert "CREATE SCHEMA IF NOT EXISTS reference" in source
    assert "reference.us_trademark_status_reference_version" in source
    assert "reference.us_trademark_status_code" in source
    assert "source_document_sha256 char(64)" in source
    assert "normalized_payload_sha256 char(64)" in source
    assert "record_count integer" in source
    assert "WHERE is_active" in source
    assert "PRIMARY KEY (reference_version, raw_code)" in source
    assert "ON DELETE RESTRICT" in source


def test_reference_module_preserves_official_reference_vs_legal_conclusion_boundary() -> None:
    source = Path("app/us/status_reference.py").read_text(encoding="utf-8")
    assert 'REFERENCE_PAYLOAD_SCHEMA = "MARKORBIT_USPTO_STATUS_REFERENCE_V1"' in source
    assert 'REFERENCE_KIND = "TRADEMARK_STATUS_CODES"' in source
    assert 'AUTHORITY = "USPTO"' in source
    assert 'CURRENT_OFFICIAL_DOCUMENT_NAME = "Table1TrademarkStatusCodes_20250813.doc"' in source
    assert "source.url must be an HTTPS USPTO domain URL" in source
    assert "different source/payload evidence" in source
    assert "USPTO_OFFICIAL_REFERENCE_NOT_MARKORBIT_LEGAL_CONCLUSION" in source

    forbidden = (
        "is_active_trademark",
        "legal_status",
        "ACTIVE =",
        "DEAD =",
        "REGISTERED =",
        "maintenance_due",
    )
    for token in forbidden:
        assert token not in source


def test_reference_import_script_uses_raw_reference_mount_and_no_fact_mutation() -> None:
    source = Path("scripts/import-us-status-reference.ps1").read_text(encoding="utf-8")
    assert "/data/raw/reference/us/$ReferenceFileName" in source
    assert "app.us.import_status_reference" in source
    assert "--no-activate" in source
    assert "reports" in source
    forbidden = (
        "ingest_us_package",
        "run-us.ps1",
        "retry-us.ps1",
        "reset-us-clean-rebuild.ps1",
        "TRUNCATE",
    )
    for token in forbidden:
        assert token not in source


def test_reference_inventory_is_read_only_and_reports_unknown_codes() -> None:
    source = Path("app/us/status_reference_inventory.py").read_text(encoding="utf-8")
    assert "us_case_current FINAL" in source
    assert "unmapped_status_codes" in source
    assert "USPTO_OFFICIAL_REFERENCE_NOT_MARKORBIT_LEGAL_CONCLUSION" in source
    forbidden = ("INSERT INTO", "UPDATE ", "DELETE FROM", "TRUNCATE")
    for token in forbidden:
        assert token not in source


def test_apply_us_schema_also_applies_reference_schema_additively() -> None:
    source = Path("scripts/apply-us-m1-schema.ps1").read_text(encoding="utf-8")
    assert "002_us_status_reference.sql" in source
    assert "psql -v ON_ERROR_STOP=1" in source
    assert "004_us_m1_core.sql" in source
    assert "app.us.migrations" in source


def test_ci_runs_status_reference_live_fixture() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "python -m app.us.validate_status_reference_fixture" in workflow
