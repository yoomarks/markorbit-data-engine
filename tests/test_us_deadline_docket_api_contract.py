from pathlib import Path

import app.main as main


DEADLINE_PATHS = {
    "/api/us/event-roles/ruleset",
    "/api/us/deadline-evidence/{serial_number}",
    "/api/us/application-deadlines-resolved/{serial_number}",
    "/api/us/deadlines/candidates",
}


def test_main_exposes_deadline_docket_routes() -> None:
    paths = {route.path for route in main.app.routes}
    assert DEADLINE_PATHS.issubset(paths)


def test_deadline_docket_routes_are_get_only() -> None:
    for route in main.app.routes:
        if route.path in DEADLINE_PATHS:
            assert route.methods == {"GET"}


def test_reviewed_event_queries_use_official_event_type_code_column() -> None:
    evidence = Path("app/us/deadline_evidence.py").read_text(encoding="utf-8")
    portfolio = Path("app/us/deadline_portfolio.py").read_text(encoding="utf-8")
    assert "event_type_code AS event_type" in evidence
    assert "event_type_code AS event_type" in portfolio
    assert "SELECT event_code, event_date, event_sequence, event_type," not in evidence
    assert "event_sequence,\n               event_type, description_text" not in portfolio


def test_ci_runs_reviewed_event_deadline_fixture_in_existing_us_job() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "Run reviewed US event-role deadline evidence fixture" in workflow
    assert "python -m app.us.validate_deadline_evidence_fixture" in workflow
    assert workflow.count("us-runtime-fixture:") == 1


def test_local_validation_runs_reviewed_event_deadline_fixture() -> None:
    source = Path("scripts/validate-us-m1-fixture.ps1").read_text(encoding="utf-8")
    assert "python -m app.us.validate_deadline_evidence_fixture" in source
