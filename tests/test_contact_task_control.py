from pathlib import Path

from app.component_versions import component_versions
from app.contact_ingest.task_migrations import CONTACT_TASK_CONTROL_VERSION, TASK_SCHEMA_SQL
from app.contact_ingest.task_queue import (
    SUPPORTED_CONTACT_SUFFIXES,
    _candidate_files,
    _profile_from_summary,
)


ROOT = Path(__file__).resolve().parents[1]


def test_contact_task_component_version_is_published() -> None:
    contact = component_versions()["components"]["contact_ingestion"]
    assert contact["task_control_version"] == CONTACT_TASK_CONTROL_VERSION
    text = (ROOT / "docs" / "COMPONENT_VERSIONS.md").read_text(encoding="utf-8")
    assert CONTACT_TASK_CONTROL_VERSION in text


def test_contact_task_schema_has_idempotent_sha_and_explicit_statuses() -> None:
    assert "source_sha256 char(64) NOT NULL UNIQUE" in TASK_SCHEMA_SQL
    assert "'READY'" in TASK_SCHEMA_SQL
    assert "'PROCESSING'" in TASK_SCHEMA_SQL
    assert "'SUCCESS'" in TASK_SCHEMA_SQL
    assert "'FAILED'" in TASK_SCHEMA_SQL
    assert "'INVALID'" in TASK_SCHEMA_SQL
    assert "'MISSING_FILE'" in TASK_SCHEMA_SQL


def test_candidate_files_only_accept_supported_structured_contact_formats(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming" / "contacts"
    incoming.mkdir(parents=True)
    supported_names = (
        "qcc.xlsx",
        "legacy.xls",
        "agents.csv",
        "contacts.tsv",
        "data.json",
        "rows.jsonl",
        "rows.ndjson",
        "notes.txt",
        "directory.html",
        "directory.htm",
        "directory.pdf",
        "directory.docx",
        "directory.doc",
        "bundle.zip",
    )
    for name in supported_names:
        (incoming / name).write_text("x", encoding="utf-8")
    (incoming / "image.png").write_bytes(b"x")

    names = {path.name for path in _candidate_files(incoming)}
    assert names == set(supported_names)
    assert SUPPORTED_CONTACT_SUFFIXES == {
        ".xlsx",
        ".xls",
        ".csv",
        ".tsv",
        ".json",
        ".jsonl",
        ".ndjson",
        ".txt",
        ".html",
        ".htm",
        ".pdf",
        ".docx",
        ".doc",
        ".zip",
    }


def test_profile_summary_preserves_single_or_mixed_source_profile() -> None:
    assert _profile_from_summary({"tables": []}) == "UNKNOWN"
    assert _profile_from_summary({"tables": [{"profile": "QCC_COMPANY_EXPORT"}]}) == "QCC_COMPANY_EXPORT"
    assert _profile_from_summary(
        {"tables": [{"profile": "QCC_COMPANY_EXPORT"}, {"profile": "AGENT_CONTACT_LIST"}]}
    ) == "MIXED"


def test_contact_admin_routes_and_page_are_registered() -> None:
    import app.main as main

    routes = {route.path for route in main.app.routes}
    assert "/contacts" in routes
    assert "/api/admin/contacts/summary" in routes
    assert "/api/admin/contacts/tasks" in routes
    assert "/api/admin/contacts/tasks/{task_id}" in routes
    assert "/api/admin/contacts/scan" in routes
    assert "/api/admin/contacts/tasks/batch-apply" in routes
    assert "/api/admin/contacts/tasks/{task_id}/apply" in routes

    markup = (ROOT / "web" / "contacts.html").read_text(encoding="utf-8")
    assert "Contact Ingest 任务" in markup
    assert "incoming/contacts" in markup
    assert "执行导入" in markup
    assert "批量执行导入" in markup
    assert "/api/admin/contacts/scan" in markup
    assert "/api/admin/contacts/tasks/batch-apply" in markup
    assert "/api/admin/contacts/tasks/" in markup

    main_markup = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert 'href="/contacts"' in main_markup
    assert "Contacts 联系人" in main_markup


def test_contact_batch_apply_is_explicit_sequential_background_work() -> None:
    source = (ROOT / "app" / "contact_ingest" / "admin_api.py").read_text(encoding="utf-8")
    batch_worker = source[
        source.index("def _apply_batch_in_background") : source.index("@router.on_event")
    ]
    batch_route = source[
        source.index('def admin_contact_batch_apply()') : source.index(
            '@router.post("/api/admin/contacts/tasks/{task_id}/apply"'
        )
    ]
    assert "for task_id in task_ids" in batch_worker
    assert "_apply_in_background(task_id)" in batch_worker
    assert 'list_contact_tasks(status="READY", limit=1000)' in batch_route
    assert "reversed(ready_tasks)" in batch_route
    assert "target=_apply_batch_in_background" in batch_route
    assert '"accepted_count": len(task_ids)' in batch_route


def test_background_discovery_never_auto_applies_contact_data() -> None:
    source = (ROOT / "app" / "contact_ingest" / "task_queue.py").read_text(encoding="utf-8")
    scanner = source[source.index("def _scanner_loop") : source.index("def start_contact_task_scanner")]
    assert "scan_contact_incoming()" in scanner
    assert "apply_contact_task(" not in scanner
    assert "apply_plan(" not in scanner


def test_contact_task_bootstrap_sql_matches_runtime_version() -> None:
    sql = (ROOT / "database" / "postgres" / "init" / "006_contact_task_control.sql").read_text(
        encoding="utf-8"
    )
    assert CONTACT_TASK_CONTROL_VERSION in sql
    assert "CREATE TABLE IF NOT EXISTS contact.ingest_task" in sql
