from pathlib import Path

from app.main import app
from app.us.migrations import REQUIRED_TABLES, US_SCHEMA_VERSION
from app.us.reset_rebuild import RESET_CONFIRMATION, RESET_VERSION


def test_us_m14_schema_contract_and_reset_version() -> None:
    assert US_SCHEMA_VERSION == "US_M1.4"
    assert "us_case_observation_history" in REQUIRED_TABLES
    assert RESET_VERSION == "US_CLEAN_REBUILD_RESET_V2"
    assert RESET_CONFIRMATION == "RESET-US-M1.4"


def test_us_change_and_deadline_routers_are_mounted() -> None:
    paths = {route.path for route in app.routes}
    assert "/api/us/event-roles/ruleset" in paths
    assert "/api/us/deadline-evidence/{serial_number}" in paths
    assert "/api/us/application-deadlines-resolved/{serial_number}" in paths
    assert "/api/us/deadlines/candidates" in paths
    assert "/api/us/history/{serial_number}" in paths
    assert "/api/us/changes" in paths


def test_us_m14_schema_and_ci_are_wired() -> None:
    schema = Path("database/clickhouse/init/008_us_m14_change_history.sql").read_text(
        encoding="utf-8"
    )
    apply_script = Path("scripts/apply-us-m1-schema.ps1").read_text(encoding="utf-8")
    ci = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "us_case_observation_history" in schema
    assert "US_M1.4" in schema
    assert "008_us_m14_change_history.sql" in apply_script
    assert "004_us_event_roles.sql" in apply_script
    assert "validate_deadline_evidence_fixture" in ci
    assert "validate_change_history_fixture" in ci
