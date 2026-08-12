from __future__ import annotations

import threading

from app.contact_ingest import CONTACT_INGEST_VERSION


CONTACT_TASK_CONTROL_VERSION = "CONTACT_TASK_CONTROL_V1.1"


TASK_SCHEMA_SQL = rf"""
CREATE SCHEMA IF NOT EXISTS contact;

CREATE TABLE IF NOT EXISTS contact.ingest_task (
    task_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_sha256 char(64) NOT NULL UNIQUE,
    file_name text NOT NULL,
    file_path text NOT NULL,
    file_size bigint NOT NULL DEFAULT 0,
    file_modified_at timestamptz,
    file_type text NOT NULL DEFAULT '',
    ingest_version text NOT NULL DEFAULT '{CONTACT_INGEST_VERSION}',
    status text NOT NULL,
    detected_profile text NOT NULL DEFAULT '',
    plan_summary jsonb NOT NULL DEFAULT '{{}}'::jsonb,
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

-- Existing V1 task tables did not have this column. Adding it with a blank
-- default lets runtime migration distinguish old plans from fresh V1.1 plans.
ALTER TABLE contact.ingest_task
ADD COLUMN IF NOT EXISTS ingest_version text NOT NULL DEFAULT '';

ALTER TABLE contact.ingest_task
ALTER COLUMN ingest_version SET DEFAULT '{CONTACT_INGEST_VERSION}';

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

        # A process that died during an explicit import leaves PROCESSING behind.
        # No contact import is auto-resumed: make the interruption visible and
        # retryable from the Control Center.
        cur.execute(
            """
            UPDATE contact.ingest_task
            SET status = 'FAILED',
                finished_at = now(),
                error_message = 'Interrupted by API process restart; safe to retry',
                last_seen_at = now()
            WHERE status = 'PROCESSING'
            """
        )

        # Parser/profile upgrades get one re-evaluation pass. MISSING_FILE is used
        # as the scanner's existing re-resolve/reparse state; no contact data is
        # written by this migration.
        cur.execute(
            """
            UPDATE contact.ingest_task
            SET status = 'MISSING_FILE',
                ingest_version = %s,
                plan_summary = '{}'::jsonb,
                detected_profile = '',
                error_message = 'Parser upgraded; pending automatic re-evaluation',
                started_at = NULL,
                finished_at = NULL,
                last_seen_at = now()
            WHERE ingest_version <> %s
              AND status IN ('READY', 'FAILED', 'INVALID', 'MISSING_FILE')
            """,
            (CONTACT_INGEST_VERSION, CONTACT_INGEST_VERSION),
        )
        cur.execute(
            """
            UPDATE contact.ingest_task
            SET ingest_version = %s
            WHERE ingest_version <> %s
              AND status = 'SUCCESS'
            """,
            (CONTACT_INGEST_VERSION, CONTACT_INGEST_VERSION),
        )

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
