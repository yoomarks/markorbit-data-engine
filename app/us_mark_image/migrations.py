from __future__ import annotations

from app.db import postgres_conn


SCHEMA_SQL = r'''
CREATE SCHEMA IF NOT EXISTS visual;
CREATE SCHEMA IF NOT EXISTS acquisition;

CREATE TABLE IF NOT EXISTS visual.asset (
    asset_id uuid PRIMARY KEY,
    sha256 char(64) NOT NULL UNIQUE,
    mime_type text NOT NULL,
    file_extension text NOT NULL,
    byte_size bigint NOT NULL CHECK (byte_size >= 0),
    width integer NOT NULL CHECK (width > 0),
    height integer NOT NULL CHECK (height > 0),
    content_bbox jsonb,
    dhash64 char(16),
    storage_key text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (sha256 ~ '^[0-9a-fA-F]{64}$'),
    CHECK (dhash64 IS NULL OR dhash64 ~ '^[0-9a-fA-F]{16}$')
);

CREATE INDEX IF NOT EXISTS ix_visual_asset_dhash64
ON visual.asset (dhash64)
WHERE dhash64 IS NOT NULL;

CREATE TABLE IF NOT EXISTS visual.trademark_asset (
    jurisdiction text NOT NULL,
    serial_number text NOT NULL,
    asset_id uuid NOT NULL REFERENCES visual.asset(asset_id) ON DELETE RESTRICT,
    source_url text NOT NULL,
    source_rank bigint NOT NULL DEFAULT 0 CHECK (source_rank >= 0),
    first_observed_at timestamptz NOT NULL DEFAULT now(),
    last_observed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (jurisdiction, serial_number)
);

CREATE INDEX IF NOT EXISTS ix_visual_trademark_asset_asset
ON visual.trademark_asset (asset_id);

CREATE TABLE IF NOT EXISTS acquisition.us_mark_image_planner_state (
    state_key text PRIMARY KEY,
    backfill_serial_cursor text NOT NULL DEFAULT '',
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO acquisition.us_mark_image_planner_state (state_key)
VALUES ('US_MARK_IMAGE')
ON CONFLICT (state_key) DO NOTHING;

CREATE TABLE IF NOT EXISTS acquisition.us_mark_image_coverage (
    serial_number text PRIMARY KEY,
    source_url text NOT NULL,
    source_rank bigint NOT NULL DEFAULT 0 CHECK (source_rank >= 0),
    source_mark_fingerprint char(64) NOT NULL,
    standard_character_claimed boolean NOT NULL DEFAULT false,
    state text NOT NULL,
    priority integer NOT NULL DEFAULT 0,
    reason_codes text[] NOT NULL DEFAULT ARRAY[]::text[],
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at timestamptz,
    claimed_at timestamptz,
    completed_at timestamptz,
    last_http_status integer,
    last_error text,
    asset_id uuid REFERENCES visual.asset(asset_id) ON DELETE RESTRICT,
    first_fetched_at timestamptz,
    last_fetched_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (source_mark_fingerprint ~ '^[0-9a-fA-F]{64}$'),
    CHECK (state IN (
        'NOT_APPLICABLE', 'QUEUED', 'FETCHING', 'RETRYABLE',
        'NOT_FOUND', 'FETCHED'
    ))
);

CREATE INDEX IF NOT EXISTS ix_us_mark_image_pending
ON acquisition.us_mark_image_coverage (priority DESC, source_rank DESC, serial_number)
WHERE state IN ('QUEUED', 'RETRYABLE', 'NOT_FOUND');

CREATE INDEX IF NOT EXISTS ix_us_mark_image_retry_due
ON acquisition.us_mark_image_coverage (next_attempt_at)
WHERE state IN ('RETRYABLE', 'NOT_FOUND');
'''


def ensure_mark_image_schema() -> None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()
