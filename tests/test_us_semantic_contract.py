from pathlib import Path


def test_semantic_schema_separates_reference_and_interpretation() -> None:
    source = Path("database/postgres/init/003_us_semantic_reference.sql").read_text(
        encoding="utf-8"
    )
    assert "reference.us_trademark_event_reference_version" in source
    assert "reference.us_trademark_event_code" in source
    assert "CREATE SCHEMA IF NOT EXISTS interpretation" in source
    assert "interpretation.us_status_ruleset_version" in source
    assert "interpretation.us_status_rule" in source
    assert "ON DELETE RESTRICT" in source
    assert "WHERE is_active" in source


def test_interpretation_is_unknown_first_and_evidence_bound() -> None:
    source = Path("app/us/status_interpretation.py").read_text(encoding="utf-8")
    assert '"result": "UNKNOWN"' in source
    assert "ruleset_evidence_not_verified" in source
    assert "official_reference_evidence_not_verified" in source
    assert "conflicting_top_priority_rules" in source
    assert "active_status_reference_version_mismatch" in source
    assert "active_event_reference_version_mismatch" in source


def test_ci_runs_one_combined_semantic_live_gate() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "python -m app.us.validate_status_reference_fixture" in workflow
    assert "US semantic reference and interpretation fixture" in workflow
