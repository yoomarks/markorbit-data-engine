from pathlib import Path


def test_us_runtime_fixture_writes_checks_and_cleans_all_tables() -> None:
    source = Path("app/us/validate_fixture.py").read_text(encoding="utf-8")
    assert '"contract": "US_M1.1_RUNTIME_FIXTURE"' in source
    assert "DIRECT_SERIAL" in source
    assert "MADRID_SERIAL" in source
    assert "partial_first_use_date" in source
    assert "event_description" in source
    assert "use_1a_filed" in source
    assert "madrid_66a_current" in source
    assert "_cleanup_package_outputs(package_id)" in source
    assert "residual_rows" in source
    for table in (
        "us_case_current",
        "us_owner_current",
        "us_classification_current",
        "us_event_history",
        "us_statement_current",
    ):
        assert table in source


def test_us_runtime_fixture_script_applies_schema_and_runs_worker() -> None:
    source = Path("scripts/validate-us-m1-fixture.ps1").read_text(encoding="utf-8")
    assert "apply-us-m1-schema.ps1" in source
    assert "python -m app.us.validate_fixture" in source


def test_ci_runs_us_fixture_against_live_postgres_and_clickhouse() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "us-runtime-fixture:" in workflow
    assert "docker compose up -d --wait postgres clickhouse" in workflow
    assert "python -m app.us.validate_fixture" in workflow
    assert "docker compose down -v --remove-orphans" in workflow
