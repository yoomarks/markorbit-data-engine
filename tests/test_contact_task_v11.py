from pathlib import Path

from app.contact_ingest import CONTACT_INGEST_VERSION
from app.contact_ingest.task_migrations import (
    CONTACT_TASK_CONTROL_VERSION,
    TASK_SCHEMA_SQL,
)


ROOT = Path(__file__).resolve().parents[1]


def test_task_schema_tracks_parser_version_for_re_evaluation() -> None:
    assert CONTACT_INGEST_VERSION == "CONTACT_INGEST_V1.1"
    assert CONTACT_TASK_CONTROL_VERSION == "CONTACT_TASK_CONTROL_V1.1"
    assert "ingest_version text NOT NULL" in TASK_SCHEMA_SQL
    assert "ADD COLUMN IF NOT EXISTS ingest_version" in TASK_SCHEMA_SQL

    source = (ROOT / "app" / "contact_ingest" / "task_migrations.py").read_text(
        encoding="utf-8"
    )
    assert "Parser upgraded; pending automatic re-evaluation" in source
    assert "status = 'MISSING_FILE'" in source
    assert "ingest_version <> %s" in source


def test_stale_processing_tasks_are_recovered_on_api_restart() -> None:
    source = (ROOT / "app" / "contact_ingest" / "task_migrations.py").read_text(
        encoding="utf-8"
    )
    assert "WHERE status = 'PROCESSING'" in source
    assert "Interrupted by API process restart; safe to retry" in source
    assert "status = 'FAILED'" in source


def test_admin_apply_and_scan_return_without_running_work_inline() -> None:
    source = (ROOT / "app" / "contact_ingest" / "admin_api.py").read_text(
        encoding="utf-8"
    )
    assert 'status_code=202' in source
    assert 'target=_apply_in_background' in source
    assert 'target=_scan_in_background' in source
    assert 'return {"status": "PROCESSING"' in source
    assert 'return {"status": "SCANNING"' in source


def test_bootstrap_schema_publishes_task_and_ingest_versions() -> None:
    sql = (
        ROOT / "database" / "postgres" / "init" / "006_contact_task_control.sql"
    ).read_text(encoding="utf-8")
    assert "CONTACT_TASK_CONTROL_V1.1" in sql
    assert "CONTACT_INGEST_V1.1" in sql
    assert "ingest_version" in sql
