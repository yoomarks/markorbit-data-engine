from __future__ import annotations

from app.db import postgres_conn


SCHEMA_SQL = r'''
CREATE SCHEMA IF NOT EXISTS acquisition;

CREATE TABLE IF NOT EXISTS acquisition.us_tsdr_planner_state (
    state_key text PRIMARY KEY,
    source_rank_watermark bigint NOT NULL DEFAULT 0 CHECK (source_rank_watermark >= 0),
    source_serial_watermark text NOT NULL DEFAULT '',
    last_completed_batch_id uuid,
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO acquisition.us_tsdr_planner_state (state_key)
VALUES ('US_TSDR_WEEKLY')
ON CONFLICT (state_key) DO NOTHING;

CREATE TABLE IF NOT EXISTS acquisition.us_tsdr_case_coverage (
    serial_number text PRIMARY KEY,
    first_fetched_at timestamptz,
    last_fetched_at timestamptz,
    last_result_status text NOT NULL DEFAULT 'NEVER_FETCHED',
    last_snapshot_hash char(64),
    last_source_attorney_fingerprint char(64),
    last_source_attorney_present boolean,
    last_changed_at timestamptz,
    refresh_due_at timestamptz,
    lifecycle_state text NOT NULL DEFAULT 'UNKNOWN',
    terminal_complete boolean NOT NULL DEFAULT false,
    last_batch_id uuid,
    last_task_type text,
    successful_fetch_count integer NOT NULL DEFAULT 0 CHECK (successful_fetch_count >= 0),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (last_snapshot_hash IS NULL OR last_snapshot_hash ~ '^[0-9a-fA-F]{64}$'),
    CHECK (last_source_attorney_fingerprint IS NULL OR last_source_attorney_fingerprint ~ '^[0-9a-fA-F]{64}$'),
    CHECK (lifecycle_state IN ('UNKNOWN', 'REFRESHABLE', 'TERMINAL_INVALID'))
);

CREATE INDEX IF NOT EXISTS ix_us_tsdr_coverage_refresh_due
ON acquisition.us_tsdr_case_coverage (refresh_due_at)
WHERE terminal_complete = false;

CREATE TABLE IF NOT EXISTS acquisition.us_tsdr_batch (
    batch_id uuid PRIMARY KEY,
    batch_key text NOT NULL UNIQUE,
    policy_version text NOT NULL,
    backfill_bucket integer NOT NULL DEFAULT -1 CHECK (backfill_bucket >= -1 AND backfill_bucket < 52),
    status text NOT NULL,
    target_capacity integer NOT NULL CHECK (target_capacity > 0),
    task_count integer NOT NULL DEFAULT 0 CHECK (task_count >= 0),
    source_rank_from bigint NOT NULL DEFAULT 0 CHECK (source_rank_from >= 0),
    source_serial_from text NOT NULL DEFAULT '',
    source_rank_to bigint NOT NULL DEFAULT 0 CHECK (source_rank_to >= 0),
    source_serial_to text NOT NULL DEFAULT '',
    planned_at timestamptz NOT NULL DEFAULT now(),
    exported_at timestamptz,
    result_received_at timestamptz,
    completed_at timestamptz,
    export_path text,
    export_sha256 char(64),
    result_path text,
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (status IN ('PLANNED', 'EXPORTED', 'RESULT_RECEIVED', 'COMPLETED', 'FAILED')),
    CHECK (export_sha256 IS NULL OR export_sha256 ~ '^[0-9a-fA-F]{64}$')
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_us_tsdr_one_open_batch
ON acquisition.us_tsdr_batch ((1))
WHERE status IN ('PLANNED', 'EXPORTED', 'RESULT_RECEIVED');

CREATE TABLE IF NOT EXISTS acquisition.us_tsdr_task (
    task_id uuid PRIMARY KEY,
    batch_id uuid NOT NULL REFERENCES acquisition.us_tsdr_batch(batch_id) ON DELETE RESTRICT,
    serial_number text NOT NULL,
    task_type text NOT NULL,
    priority_score integer NOT NULL,
    reason_codes text[] NOT NULL DEFAULT ARRAY[]::text[],
    applicant_country text NOT NULL DEFAULT '',
    source_rank bigint NOT NULL DEFAULT 0 CHECK (source_rank >= 0),
    lifecycle_state text NOT NULL DEFAULT 'UNKNOWN',
    source_attorney_fingerprint char(64),
    source_attorney_present boolean NOT NULL DEFAULT false,
    state text NOT NULL DEFAULT 'PLANNED',
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    result_status text,
    fetched_at timestamptz,
    snapshot_hash char(64),
    raw_relative_path text,
    error_message text,
    UNIQUE (batch_id, serial_number),
    CHECK (task_type IN ('INITIAL_FETCH', 'REFRESH', 'FINAL_FETCH', 'TERMINAL_INITIAL_FETCH')),
    CHECK (lifecycle_state IN ('UNKNOWN', 'REFRESHABLE', 'TERMINAL_INVALID')),
    CHECK (state IN ('PLANNED', 'EXPORTED', 'SUCCESS', 'NOT_FOUND', 'FAILED', 'UNATTEMPTED')),
    CHECK (snapshot_hash IS NULL OR snapshot_hash ~ '^[0-9a-fA-F]{64}$'),
    CHECK (source_attorney_fingerprint IS NULL OR source_attorney_fingerprint ~ '^[0-9a-fA-F]{64}$')
);

CREATE INDEX IF NOT EXISTS ix_us_tsdr_task_batch_state
ON acquisition.us_tsdr_task (batch_id, state, priority_score DESC);

CREATE INDEX IF NOT EXISTS ix_us_tsdr_task_serial
ON acquisition.us_tsdr_task (serial_number, created_at DESC);
'''


def ensure_tsdr_schema() -> None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()
