from app.db import postgres_conn
from app.global_trademarks.ca_rich_schema import ensure_ca_rich_observation_schema
from app.global_trademarks.legacy_upgrade import upgrade_pre_ingest_country_schemas
from app.global_trademarks.schema import ensure_country_trademark_schemas


INGEST_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS acquisition.global_trademark_record_source (
    jurisdiction text NOT NULL,
    application_number text NOT NULL,
    source_record_key text NOT NULL,
    source_object_id uuid NOT NULL REFERENCES acquisition.global_trademark_source_object(object_id),
    source_record_role text NOT NULL,
    observed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (
        jurisdiction,
        source_record_key,
        source_object_id,
        source_record_role
    )
);
CREATE INDEX IF NOT EXISTS idx_global_trademark_record_source_application
    ON acquisition.global_trademark_record_source (jurisdiction, application_number);

CREATE TABLE IF NOT EXISTS acquisition.global_trademark_ingest_run (
    run_id uuid PRIMARY KEY,
    source_object_id uuid NOT NULL REFERENCES acquisition.global_trademark_source_object(object_id),
    jurisdiction text NOT NULL,
    pipeline_id text NOT NULL,
    status text NOT NULL DEFAULT 'RUNNING',
    checkpoint bigint NOT NULL DEFAULT 0,
    rows_committed bigint NOT NULL DEFAULT 0,
    started_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    error_text text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (source_object_id, pipeline_id),
    CHECK (status IN ('RUNNING', 'COMPLETE', 'FAILED')),
    CHECK (checkpoint >= 0),
    CHECK (rows_committed >= 0)
);
CREATE INDEX IF NOT EXISTS idx_global_trademark_ingest_run_status
    ON acquisition.global_trademark_ingest_run (jurisdiction, pipeline_id, status);
"""


def ensure_seed_ingest_schema() -> None:
    upgrade_pre_ingest_country_schemas()
    ensure_country_trademark_schemas()
    ensure_ca_rich_observation_schema()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(INGEST_SCHEMA_SQL)
        conn.commit()
