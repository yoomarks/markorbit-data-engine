from __future__ import annotations

import threading


CONTACT_TASK_CONTROL_VERSION = "CONTACT_TASK_CONTROL_V1.1"


TASK_SCHEMA_SQL = r"""
CREATE SCHEMA IF NOT EXISTS contact;

CREATE TABLE IF NOT EXISTS contact.ingest_task (
    task_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_sha256 char(64) NOT NULL UNIQUE,
    file_name text NOT NULL,
    file_path text NOT NULL,
    file_size bigint NOT NULL DEFAULT 0,
    file_modified_at timestamptz,
    file_type text NOT NULL DEFAULT '',
    ingest_version text NOT NULL DEFAULT '',
    status text NOT NULL,
    detected_profile text NOT NULL DEFAULT '',
    plan_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_message text,
    discovered_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    archived_path text,
    CHECK (status IN (
        'READY', 'PROCESSING', 'SUCCESS', 'FAILED', 'INVALID', 'MISSING_FILE'
    ))
);

ALTER TABLE contact.ingest_task
ADD COLUMN IF NOT EXISTS ingest_version text NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS ix_contact_ingest_task_status
ON contact.ingest_task(status, discovered_at DESC);

CREATE INDEX IF NOT EXISTS ix_contact_ingest_task_seen
ON contact.ingest_task(last_seen_at DESC);
"""

_schema_ready = False
_schema_lock = threading.Lock()


def _apply_task_schema(conn) -> None:
    from app.contact_ingest.migrations import ensure_contact_schema

    ensure_contact_schema(conn)
    with conn.cursor() as cur:
        cur.execute(TASK_SCHEMA_SQL)
        cur.execute(
            """
            INSERT INTO control.schema_version(component, version)
            VALUES ('CONTACT_TASK_CONTROL', %s)
            ON CONFLICT (component)
            DO UPDATE SET version = EXCLUDED.version, applied_at = now()
            """,
            (CONTACT_TASK_CONTROL_VERSION,),
        )


def ensure_contact_task_schema(conn=None) -> None:
    """Install base contact + additive task-control schema."""
    global _schema_ready

    if conn is not None:
        _apply_task_schema(conn)
        return
    if _schema_ready:
        return

    from app.db import postgres_conn

    with _schema_lock:
        if _schema_ready:
            return
        with postgres_conn() as owned_conn:
            _apply_task_schema(owned_conn)
            owned_conn.commit()
        _schema_ready = True
