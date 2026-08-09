from pathlib import Path

import app.main as main


SEMANTIC_PATHS = {
    "/api/us/references/status",
    "/api/us/references/status/{status_code}",
    "/api/us/references/events",
    "/api/us/references/events/{event_code}",
    "/api/us/references/acceptance",
    "/api/us/semantic-readiness",
    "/api/us/interpretation/ruleset",
    "/api/us/status-interpretation/{serial_number}",
    "/api/us/maintenance/rules",
    "/api/us/maintenance/{serial_number}",
    "/api/us/application-deadlines/rules",
    "/api/us/application-deadlines/{serial_number}",
}


def test_main_registers_us_semantic_router_without_replacing_case_api() -> None:
    wrapper = Path("app/main.py").read_text(encoding="utf-8")
    core = Path("app/main_core.py").read_text(encoding="utf-8")
    assert "from app.us.semantic_api import router as us_semantic_router" in core
    assert "app.include_router(us_semantic_router)" in core
    assert '@app.get("/api/us/cases/{serial_number}")' in core
    assert "sys.modules[__name__] = _core" in wrapper
    assert "app.us.deadline_docket_api" in wrapper
    assert "app.us.change_history_api" in wrapper


def test_main_exposes_semantic_maintenance_and_application_deadline_routes() -> None:
    paths = {route.path for route in main.app.routes}
    assert SEMANTIC_PATHS.issubset(paths)


def test_new_semantic_routes_are_get_only() -> None:
    for route in main.app.routes:
        if route.path in SEMANTIC_PATHS:
            assert route.methods == {"GET"}


def test_ci_runs_combined_maintenance_and_reference_pack_fixture() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "Run US maintenance and reference-pack fixture" in workflow
    assert "python -m app.us.validate_maintenance_fixture" in workflow


def test_ci_runs_application_deadline_fixture_without_new_job() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "Run US application deadline fixture" in workflow
    assert "python -m app.us.validate_application_deadlines_fixture" in workflow
    assert workflow.count("us-runtime-fixture:") == 1
