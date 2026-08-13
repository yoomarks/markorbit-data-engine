from __future__ import annotations

from app.contact_ingest import CONTACT_SCHEMA_VERSION


# Kept runtime-local because the API/worker image copies app/, not database/.
# database/postgres/init/005_contact_ingest.sql mirrors this additive schema for
# fresh PostgreSQL volumes.
SCHEMA_SQL = r"""
CREATE SCHEMA IF NOT EXISTS contact;

CREATE TABLE IF NOT EXISTS entity.entity_identifier (
    identifier_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id uuid NOT NULL REFERENCES entity.entity(entity_id) ON DELETE CASCADE,
    identifier_type text NOT NULL,
    identifier_value text NOT NULL,
    normalized_value text NOT NULL,
    country_code char(2),
    source text NOT NULL DEFAULT 'CONTACT_INGEST',
    confidence_score numeric(5,4) NOT NULL DEFAULT 0.9500,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_entity_identifier_value
ON entity.entity_identifier(identifier_type, normalized_value, COALESCE(country_code, ''));
CREATE INDEX IF NOT EXISTS ix_entity_identifier_entity
ON entity.entity_identifier(entity_id, identifier_type);

CREATE TABLE IF NOT EXISTS contact.source (
    source_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_sha256 char(64) NOT NULL UNIQUE,
    source_name text NOT NULL,
    file_type text NOT NULL,
    source_profile text NOT NULL,
    source_segment text NOT NULL DEFAULT 'UNKNOWN',
    source_scope text NOT NULL DEFAULT '',
    default_country_code char(2),
    ingest_version text NOT NULL,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE contact.source
    ADD COLUMN IF NOT EXISTS source_segment text NOT NULL DEFAULT 'UNKNOWN';
ALTER TABLE contact.source
    ADD COLUMN IF NOT EXISTS source_scope text NOT NULL DEFAULT '';
ALTER TABLE contact.source
    ADD COLUMN IF NOT EXISTS default_country_code char(2);

CREATE TABLE IF NOT EXISTS contact.import_run (
    run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id uuid NOT NULL REFERENCES contact.source(source_id),
    status text NOT NULL,
    apply_mode boolean NOT NULL DEFAULT true,
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_message text,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);
CREATE INDEX IF NOT EXISTS ix_contact_import_run_started ON contact.import_run(started_at DESC);

CREATE TABLE IF NOT EXISTS contact.person (
    person_id uuid PRIMARY KEY,
    person_key char(64) NOT NULL UNIQUE,
    canonical_name text NOT NULL,
    normalized_name text NOT NULL,
    country_code char(2),
    status text NOT NULL DEFAULT 'CANDIDATE',
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_contact_person_name ON contact.person(normalized_name);

CREATE TABLE IF NOT EXISTS contact.entity_person_relation (
    relation_id uuid PRIMARY KEY,
    entity_id uuid NOT NULL REFERENCES entity.entity(entity_id) ON DELETE CASCADE,
    person_id uuid NOT NULL REFERENCES contact.person(person_id) ON DELETE CASCADE,
    relation_type text NOT NULL,
    title text NOT NULL DEFAULT '',
    department text NOT NULL DEFAULT '',
    confidence_score numeric(5,4) NOT NULL DEFAULT 0.9000,
    first_source_id uuid REFERENCES contact.source(source_id),
    last_source_id uuid REFERENCES contact.source(source_id),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(entity_id, person_id, relation_type)
);
CREATE INDEX IF NOT EXISTS ix_contact_relation_entity ON contact.entity_person_relation(entity_id, relation_type);

CREATE TABLE IF NOT EXISTS contact.channel (
    channel_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id uuid REFERENCES entity.entity(entity_id) ON DELETE CASCADE,
    person_id uuid REFERENCES contact.person(person_id) ON DELETE CASCADE,
    channel_type text NOT NULL,
    channel_value text NOT NULL,
    normalized_value text NOT NULL,
    verification_status text NOT NULL DEFAULT 'UNVERIFIED',
    verification_score numeric(5,4) NOT NULL DEFAULT 0.5000,
    is_reachable boolean NOT NULL DEFAULT false,
    is_primary boolean NOT NULL DEFAULT false,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((entity_id IS NOT NULL)::int + (person_id IS NOT NULL)::int = 1)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_contact_channel_entity
ON contact.channel(entity_id, channel_type, normalized_value) WHERE entity_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_contact_channel_person
ON contact.channel(person_id, channel_type, normalized_value) WHERE person_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_contact_channel_normalized ON contact.channel(channel_type, normalized_value);

CREATE TABLE IF NOT EXISTS contact.raw_record (
    raw_record_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id uuid NOT NULL REFERENCES contact.source(source_id) ON DELETE CASCADE,
    source_member text NOT NULL DEFAULT '',
    sheet_name text NOT NULL DEFAULT '',
    source_row bigint NOT NULL,
    source_profile text NOT NULL,
    entity_id uuid REFERENCES entity.entity(entity_id),
    entity_match_method text NOT NULL DEFAULT '',
    entity_match_confidence numeric(5,4),
    raw_data jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(source_id, source_member, sheet_name, source_row)
);
CREATE INDEX IF NOT EXISTS ix_contact_raw_record_entity ON contact.raw_record(entity_id, source_id);

CREATE TABLE IF NOT EXISTS contact.channel_observation (
    observation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    observation_key char(64) NOT NULL UNIQUE,
    channel_id uuid NOT NULL REFERENCES contact.channel(channel_id) ON DELETE CASCADE,
    source_id uuid NOT NULL REFERENCES contact.source(source_id) ON DELETE CASCADE,
    raw_record_id uuid REFERENCES contact.raw_record(raw_record_id) ON DELETE SET NULL,
    source_column text NOT NULL DEFAULT '',
    raw_value text NOT NULL,
    confidence_score numeric(5,4) NOT NULL DEFAULT 0.9000,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    observed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_contact_observation_channel ON contact.channel_observation(channel_id, observed_at DESC);

CREATE OR REPLACE VIEW contact.v_marketing_contacts AS
WITH mention_stats AS (
    SELECT entity_id,
        count(*) AS trademark_mention_count,
        count(*) FILTER (WHERE role IN ('OWNER', 'CO_OWNER', 'APPLICANT')) AS applicant_mention_count,
        count(*) FILTER (WHERE role IN ('AGENT', 'ATTORNEY', 'CORRESPONDENT')) AS agent_mention_count,
        array_agg(DISTINCT jurisdiction ORDER BY jurisdiction) AS trademark_jurisdictions
    FROM entity.entity_mention WHERE entity_id IS NOT NULL GROUP BY entity_id
),
person_owner AS (
    SELECT DISTINCT ON (person_id) person_id, entity_id, relation_type, title, department
    FROM contact.entity_person_relation ORDER BY person_id, last_seen_at DESC
)
SELECT e.entity_id, e.canonical_name AS entity_name, e.entity_type,
    e.country_code, e.region_code, e.city,
    p.person_id, p.canonical_name AS contact_person,
    po.relation_type, po.title, po.department,
    c.channel_id, c.channel_type, c.channel_value, c.normalized_value,
    c.verification_status, c.verification_score, c.is_reachable, c.is_primary,
    COALESCE(ms.trademark_mention_count, 0) AS trademark_mention_count,
    COALESCE(ms.applicant_mention_count, 0) AS applicant_mention_count,
    COALESCE(ms.agent_mention_count, 0) AS agent_mention_count,
    COALESCE(ms.trademark_jurisdictions, ARRAY[]::text[]) AS trademark_jurisdictions
FROM contact.channel c
LEFT JOIN person_owner po ON po.person_id = c.person_id
LEFT JOIN contact.person p ON p.person_id = c.person_id
JOIN entity.entity e ON e.entity_id = COALESCE(c.entity_id, po.entity_id)
LEFT JOIN mention_stats ms ON ms.entity_id = e.entity_id;
"""


def _apply_contact_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
        cur.execute(
            """
            INSERT INTO control.schema_version(component, version)
            VALUES ('CONTACT_INGEST', %s)
            ON CONFLICT (component)
            DO UPDATE SET version = EXCLUDED.version, applied_at = now()
            """,
            (CONTACT_SCHEMA_VERSION,),
        )


def ensure_contact_schema(conn=None) -> None:
    """Apply the additive contact schema without touching CN/US fact tables."""
    if conn is not None:
        _apply_contact_schema(conn)
        return

    from app.db import postgres_conn

    with postgres_conn() as owned_conn:
        _apply_contact_schema(owned_conn)
        owned_conn.commit()
