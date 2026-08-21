from app.db import postgres_conn
from app.global_trademarks.schema import ensure_country_trademark_schemas


INGEST_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS acquisition.global_trademark_record_source (
    jurisdiction text NOT NULL,
    application_number text NOT NULL,
    source_object_id uuid NOT NULL REFERENCES acquisition.global_trademark_source_object(object_id),
    source_record_role text NOT NULL,
    observed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (jurisdiction, application_number, source_object_id, source_record_role)
);
"""


def ensure_seed_ingest_schema() -> None:
    ensure_country_trademark_schemas()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(INGEST_SCHEMA_SQL)
        conn.commit()
