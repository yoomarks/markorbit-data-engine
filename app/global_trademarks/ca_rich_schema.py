from __future__ import annotations

from app.db import postgres_conn


CIPO_ST96_RICH_OBSERVATION_VERSION = "CIPO_ST96_RICH_OBSERVATION_V1"


_CA_RICH_SCHEMA_SQL = r"""
ALTER TABLE trademark_ca.party
    ADD COLUMN IF NOT EXISTS source_index integer NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS language_code text,
    ADD COLUMN IF NOT EXISTS party_code text,
    ADD COLUMN IF NOT EXISTS address_lines text[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS address_region text,
    ADD COLUMN IF NOT EXISTS postal_code text,
    ADD COLUMN IF NOT EXISTS national_legal_entity_code text,
    ADD COLUMN IF NOT EXISTS source_payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE trademark_ca.party
    ALTER COLUMN party_name DROP NOT NULL;
CREATE INDEX IF NOT EXISTS idx_trademark_ca_party_record_source
    ON trademark_ca.party (record_key, source_object_id, source_index);

ALTER TABLE trademark_ca.goods_service
    ADD COLUMN IF NOT EXISTS source_index integer NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS classification_version text,
    ADD COLUMN IF NOT EXISTS sequence_number text,
    ADD COLUMN IF NOT EXISTS text_kind text NOT NULL DEFAULT 'DESCRIPTION',
    ADD COLUMN IF NOT EXISTS source_payload jsonb NOT NULL DEFAULT '{}'::jsonb;
CREATE INDEX IF NOT EXISTS idx_trademark_ca_goods_service_record_source
    ON trademark_ca.goods_service (record_key, source_object_id, source_index);

ALTER TABLE trademark_ca.event
    ADD COLUMN IF NOT EXISTS source_index integer NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS event_category text,
    ADD COLUMN IF NOT EXISTS response_date date,
    ADD COLUMN IF NOT EXISTS additional_text text,
    ADD COLUMN IF NOT EXISTS source_payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE trademark_ca.event
    ALTER COLUMN event_code DROP NOT NULL;
CREATE INDEX IF NOT EXISTS idx_trademark_ca_event_record_source
    ON trademark_ca.event (record_key, source_object_id, source_index);

ALTER TABLE trademark_ca.relationship
    ADD COLUMN IF NOT EXISTS source_index integer NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS related_extension_counter text,
    ADD COLUMN IF NOT EXISTS related_registration_number text,
    ADD COLUMN IF NOT EXISTS related_office_code text,
    ADD COLUMN IF NOT EXISTS per_se_registration boolean,
    ADD COLUMN IF NOT EXISTS initial_application_date date,
    ADD COLUMN IF NOT EXISTS source_payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE trademark_ca.relationship
    ALTER COLUMN related_application_number DROP NOT NULL;
CREATE INDEX IF NOT EXISTS idx_trademark_ca_relationship_record_source
    ON trademark_ca.relationship (record_key, source_object_id, source_index);
"""


def ensure_ca_rich_observation_schema() -> None:
    """Add source-faithful CIPO child-observation columns without replacing base tables.

    The child tables are immutable observations keyed by ``source_row_hash``. They are
    deliberately not a current-state projection: a later weekly Update contributes a new
    source-object snapshot and a Delete tombstone never erases earlier child evidence.
    """
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_CA_RICH_SCHEMA_SQL)
        conn.commit()
