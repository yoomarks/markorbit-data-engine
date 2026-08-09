from pathlib import Path


def test_event_role_schema_is_interpretation_only_and_reference_bound() -> None:
    source = Path("database/postgres/init/004_us_event_roles.sql").read_text(
        encoding="utf-8"
    )
    assert "interpretation.us_event_role_ruleset_version" in source
    assert "interpretation.us_event_role_rule" in source
    assert "REFERENCES reference.us_trademark_event_reference_version" in source
    assert "ON DELETE RESTRICT" in source
    assert "UNIQUE (ruleset_version, event_code)" in source
    assert "WHERE is_active" in source
    assert "GUESS" not in source


def test_us_schema_apply_includes_event_role_schema() -> None:
    source = Path("scripts/apply-us-m1-schema.ps1").read_text(encoding="utf-8")
    assert "004_us_event_roles.sql" in source


def test_event_role_import_requires_reviewed_evidence_by_default() -> None:
    source = Path("app/us/import_event_roles.py").read_text(encoding="utf-8")
    assert "verify_source_file=not args.skip_source_file_verification" in source
    assert "Test-only escape hatch" in source


def test_repository_ships_no_production_event_role_payload() -> None:
    for path in Path(".").rglob("*.json"):
        if ".git" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert "MARKORBIT_US_EVENT_ROLE_RULESET_V1" not in text
