from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import shutil
import threading
import time
from typing import Any

from app.config import get_settings
from app.contact_ingest.planner import build_plan
from app.contact_ingest.repository import apply_plan
from app.contact_ingest.task_migrations import ensure_contact_task_schema
from app.db import postgres_conn


SUPPORTED_CONTACT_SUFFIXES = {
    ".xlsx", ".xls", ".csv", ".tsv", ".json", ".josn", ".jsonl", ".ndjson", ".txt",
    ".html", ".htm", ".pdf", ".docx", ".doc", ".zip",
}
DISCOVERY_LOCK_NAME = "markorbit:contact:task-discovery"

_scanner_started = False
_scanner_start_lock = threading.Lock()


def ensure_contact_directories() -> tuple[Path, Path]:
    root = get_settings().raw_data_root
    incoming = root / "incoming" / "contacts"
    archive = root / "archive" / "contacts"
    incoming.mkdir(parents=True, exist_ok=True)
    archive.mkdir(parents=True, exist_ok=True)
    return incoming, archive


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _profile_from_summary(summary: dict[str, Any]) -> str:
    profiles = {
        str(table.get("profile") or "")
        for table in summary.get("tables", [])
        if table.get("profile")
    }
    if not profiles:
        return "UNKNOWN"
    if len(profiles) == 1:
        return next(iter(profiles))
    return "MIXED"


def _candidate_files(incoming: Path) -> list[Path]:
    if not incoming.exists():
        return []
    return sorted(
        (
            path
            for path in incoming.rglob("*")
            if path.is_file() and path.suffix.lower() in SUPPORTED_CONTACT_SUFFIXES
        ),
        key=lambda path: (path.stat().st_mtime, str(path).lower()),
    )


def _touch_known_task(cur, *, path: Path, source_sha256: str) -> str | None:
    """Return a durable existing status without reparsing unchanged source content."""
    cur.execute(
        "SELECT status FROM contact.ingest_task WHERE source_sha256 = %s",
        (source_sha256,),
    )
    row = cur.fetchone()
    if not row or row["status"] == "MISSING_FILE":
        return None

    status = str(row["status"])
    stat = path.stat()
    cur.execute(
        """
        UPDATE contact.ingest_task
        SET file_name = CASE WHEN status = 'SUCCESS' THEN file_name ELSE %s END,
            file_path = CASE WHEN status = 'SUCCESS' THEN file_path ELSE %s END,
            file_size = %s,
            file_modified_at = %s,
            last_seen_at = now()
        WHERE source_sha256 = %s
        """,
        (
            path.name,
            str(path),
            int(stat.st_size),
            datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            source_sha256,
        ),
    )
    return status


def _upsert_ready_task(
    cur,
    *,
    path: Path,
    source_sha256: str,
    summary: dict[str, Any],
) -> str:
    stat = path.stat()
    cur.execute(
        """
        INSERT INTO contact.ingest_task (
            source_sha256, file_name, file_path, file_size, file_modified_at,
            file_type, status, detected_profile, plan_summary, error_message
        )
        VALUES (%s, %s, %s, %s, %s, %s, 'READY', %s, %s::jsonb, NULL)
        ON CONFLICT (source_sha256)
        DO UPDATE SET
            file_name = CASE
                WHEN contact.ingest_task.status = 'SUCCESS'
                THEN contact.ingest_task.file_name ELSE EXCLUDED.file_name END,
            file_path = CASE
                WHEN contact.ingest_task.status = 'SUCCESS'
                THEN contact.ingest_task.file_path ELSE EXCLUDED.file_path END,
            file_size = EXCLUDED.file_size,
            file_modified_at = EXCLUDED.file_modified_at,
            file_type = EXCLUDED.file_type,
            detected_profile = EXCLUDED.detected_profile,
            plan_summary = EXCLUDED.plan_summary,
            error_message = CASE
                WHEN contact.ingest_task.status IN ('FAILED', 'SUCCESS', 'PROCESSING')
                THEN contact.ingest_task.error_message ELSE NULL END,
            status = CASE
                WHEN contact.ingest_task.status IN ('FAILED', 'SUCCESS', 'PROCESSING')
                THEN contact.ingest_task.status ELSE 'READY' END,
            last_seen_at = now()
        RETURNING status
        """,
        (
            source_sha256,
            path.name,
            str(path),
            int(stat.st_size),
            datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            path.suffix.lower().lstrip("."),
            _profile_from_summary(summary),
            _json(summary),
        ),
    )
    return str(cur.fetchone()["status"])


def _upsert_invalid_task(cur, *, path: Path, source_sha256: str, error: str) -> str:
    stat = path.stat()
    cur.execute(
        """
        INSERT INTO contact.ingest_task (
            source_sha256, file_name, file_path, file_size, file_modified_at,
            file_type, status, detected_profile, plan_summary, error_message
        )
        VALUES (%s, %s, %s, %s, %s, %s, 'INVALID', '', '{}'::jsonb, %s)
        ON CONFLICT (source_sha256)
        DO UPDATE SET
            file_name = EXCLUDED.file_name,
            file_path = EXCLUDED.file_path,
            file_size = EXCLUDED.file_size,
            file_modified_at = EXCLUDED.file_modified_at,
            file_type = EXCLUDED.file_type,
            error_message = CASE
                WHEN contact.ingest_task.status IN ('FAILED', 'SUCCESS', 'PROCESSING')
                THEN contact.ingest_task.error_message ELSE EXCLUDED.error_message END,
            status = CASE
                WHEN contact.ingest_task.status IN ('FAILED', 'SUCCESS', 'PROCESSING')
                THEN contact.ingest_task.status ELSE 'INVALID' END,
            last_seen_at = now()
        RETURNING status
        """,
        (
            source_sha256,
            path.name,
            str(path),
            int(stat.st_size),
            datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            path.suffix.lower().lstrip("."),
            error,
        ),
    )
    return str(cur.fetchone()["status"])


def _count_status(metrics: dict[str, Any], status: str) -> None:
    key = {
        "READY": "ready",
        "INVALID": "invalid",
        "SUCCESS": "existing_success",
        "FAILED": "existing_failed",
        "PROCESSING": "processing",
    }.get(status, "invalid")
    metrics[key] += 1


def scan_contact_incoming() -> dict[str, Any]:
    """Discover and classify contact files without importing contact data."""
    incoming, archive = ensure_contact_directories()
    ensure_contact_task_schema()
    metrics: dict[str, Any] = {
        "incoming": str(incoming),
        "archive": str(archive),
        "busy": False,
        "files_seen": 0,
        "ready": 0,
        "invalid": 0,
        "existing_success": 0,
        "existing_failed": 0,
        "processing": 0,
    }

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_try_advisory_lock(hashtext(%s)) AS acquired",
                (DISCOVERY_LOCK_NAME,),
            )
            acquired = bool(cur.fetchone()["acquired"])
            if not acquired:
                metrics["busy"] = True
                return metrics
            try:
                for path in _candidate_files(incoming):
                    metrics["files_seen"] += 1
                    source_sha256 = _file_sha256(path)
                    known_status = _touch_known_task(
                        cur,
                        path=path,
                        source_sha256=source_sha256,
                    )
                    if known_status is not None:
                        _count_status(metrics, known_status)
                        continue
                    try:
                        plan = build_plan(path, source_name=path.name)
                        if plan.source_sha256 != source_sha256:
                            raise ValueError("Contact source changed while it was being analyzed")
                        status = _upsert_ready_task(
                            cur,
                            path=path,
                            source_sha256=source_sha256,
                            summary=plan.summary(),
                        )
                    except Exception as exc:
                        status = _upsert_invalid_task(
                            cur,
                            path=path,
                            source_sha256=source_sha256,
                            error=f"{type(exc).__name__}: {exc}",
                        )
                    _count_status(metrics, status)
                conn.commit()
            finally:
                cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (DISCOVERY_LOCK_NAME,))
                conn.commit()
    return metrics


def list_contact_tasks(*, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    ensure_contact_task_schema()
    clauses = []
    params: list[Any] = []
    if status:
        clauses.append("status = %s")
        params.append(status.strip().upper())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(max(1, min(int(limit), 1000)))
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT task_id, source_sha256, file_name, file_path, file_size,
                       file_modified_at, file_type, status, detected_profile,
                       plan_summary, error_message, discovered_at, last_seen_at,
                       started_at, finished_at, archived_path
                FROM contact.ingest_task
                {where}
                ORDER BY discovered_at DESC, file_name
                LIMIT %s
                """,
                params,
            )
            return [dict(row) for row in cur.fetchall()]


def get_contact_task(task_id: str) -> dict[str, Any]:
    ensure_contact_task_schema()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM contact.ingest_task WHERE task_id = %s", (task_id,))
            row = cur.fetchone()
            if not row:
                raise KeyError(f"Unknown contact task: {task_id}")
            task = dict(row)
            cur.execute(
                """
                SELECT ir.run_id, ir.status, ir.metrics, ir.error_message,
                       ir.started_at, ir.finished_at
                FROM contact.import_run AS ir
                JOIN contact.source AS s ON s.source_id = ir.source_id
                WHERE s.source_sha256 = %s
                ORDER BY ir.started_at DESC
                LIMIT 20
                """,
                (task["source_sha256"],),
            )
            task["import_runs"] = [dict(item) for item in cur.fetchall()]
            return task


def contact_task_summary() -> dict[str, Any]:
    incoming, archive = ensure_contact_directories()
    ensure_contact_task_schema()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT status, count(*) AS count
                FROM contact.ingest_task
                GROUP BY status
                ORDER BY status
                """
            )
            statuses = {str(row["status"]): int(row["count"]) for row in cur.fetchall()}
            cur.execute(
                """
                SELECT
                    (
                        SELECT count(*) FROM (
                            SELECT entity_id FROM contact.channel WHERE entity_id IS NOT NULL
                            UNION
                            SELECT entity_id FROM contact.entity_person_relation
                        ) AS contact_entities
                    ) AS entities,
                    (SELECT count(*) FROM contact.person) AS people,
                    (SELECT count(*) FROM contact.channel) AS channels,
                    (SELECT count(*) FROM contact.channel_observation) AS observations,
                    (SELECT count(*) FROM contact.import_run WHERE status = 'SUCCESS') AS successful_runs
                """
            )
            totals = dict(cur.fetchone())
    return {
        "incoming_directory": str(incoming),
        "archive_directory": str(archive),
        "scan_interval_seconds": max(int(get_settings().contact_scan_interval_seconds), 15),
        "supported_suffixes": sorted(SUPPORTED_CONTACT_SUFFIXES),
        "statuses": statuses,
        "totals": {key: int(value or 0) for key, value in totals.items()},
    }


def _claim_task(task_id: str) -> dict[str, Any]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE contact.ingest_task
                SET status = 'PROCESSING', started_at = now(), finished_at = NULL,
                    error_message = NULL
                WHERE task_id = %s AND status IN ('READY', 'FAILED')
                RETURNING *
                """,
                (task_id,),
            )
            row = cur.fetchone()
            if not row:
                cur.execute("SELECT * FROM contact.ingest_task WHERE task_id = %s", (task_id,))
                current = cur.fetchone()
                if not current:
                    raise KeyError(f"Unknown contact task: {task_id}")
                if current["status"] == "SUCCESS":
                    return dict(current)
                raise RuntimeError(f"Contact task is not executable from status {current['status']}")
            conn.commit()
            return dict(row)


def _finish_task(
    task_id: str,
    status: str,
    *,
    archived_path: str | None = None,
    error: str | None = None,
) -> None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE contact.ingest_task
                SET status = %s, finished_at = now(), archived_path = COALESCE(%s, archived_path),
                    error_message = %s, last_seen_at = now()
                WHERE task_id = %s
                """,
                (status, archived_path, error, task_id),
            )
        conn.commit()


def _archive_source(task_id: str, source_sha256: str, path: Path) -> Path:
    _, archive = ensure_contact_directories()
    target = archive / f"{task_id}__{path.name}"
    if target.exists():
        if _file_sha256(target) != source_sha256:
            target = archive / f"{task_id}__{int(time.time())}__{path.name}"
        else:
            path.unlink(missing_ok=True)
            return target
    shutil.move(str(path), str(target))
    return target


def apply_contact_task(task_id: str) -> dict[str, Any]:
    """Explicitly import one READY/FAILED task, then archive its source file."""
    ensure_contact_task_schema()
    task = _claim_task(task_id)
    if task["status"] == "SUCCESS":
        return {"status": "SUCCESS", "already_completed": True, "task": task}

    path = Path(str(task["file_path"]))
    if not path.is_file():
        error = f"Source file is missing: {path}"
        _finish_task(task_id, "MISSING_FILE", error=error)
        raise FileNotFoundError(error)

    try:
        source_sha256 = _file_sha256(path)
        if source_sha256 != str(task["source_sha256"]):
            raise ValueError("Contact source file changed after discovery; rescan it as a new task")
        plan = build_plan(path, source_name=task["file_name"])
        result = apply_plan(plan)
        archived = _archive_source(task_id, source_sha256, path)
        _finish_task(task_id, "SUCCESS", archived_path=str(archived))
        return {
            "status": "SUCCESS",
            "task_id": task_id,
            "archived_path": str(archived),
            "import": result,
        }
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        _finish_task(task_id, "FAILED", error=error)
        raise


def _scanner_loop() -> None:
    logger = logging.getLogger("markorbit.contact-task-scanner")
    interval = max(int(get_settings().contact_scan_interval_seconds), 15)
    while True:
        try:
            result = scan_contact_incoming()
            logger.info("Contact task discovery completed: %s", result)
        except Exception:
            logger.exception("Contact task discovery failed")
        time.sleep(interval)


def start_contact_task_scanner() -> bool:
    """Start one daemon discovery loop per API process. It never auto-applies tasks."""
    global _scanner_started
    with _scanner_start_lock:
        if _scanner_started:
            return False
        ensure_contact_directories()
        thread = threading.Thread(
            target=_scanner_loop,
            name="contact-task-discovery",
            daemon=True,
        )
        thread.start()
        _scanner_started = True
        return True
