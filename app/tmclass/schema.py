from __future__ import annotations

from dataclasses import dataclass

from app.db import postgres_conn


SCHEMA_VERSION = "TMCLASS_FACT_STORE_V1"
SCHEMA_COMPONENT = "TMCLASS_FACT_STORE"

SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS tmclass;

CREATE TABLE IF NOT EXISTS tmclass.source_evidence (
    raw_artifact_id text PRIMARY KEY,
    artifact_version integer NOT NULL CHECK (artifact_version >= 1),
    workspace_id text NOT NULL,
    source_definition_id text NOT NULL,
    collection_run_id text NOT NULL,
    canonical_uri text NOT NULL,
    source_uri text NOT NULL,
    sha256 text NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$'),
    page_kind text NOT NULL CHECK (page_kind IN ('TERM', 'CONCEPT_OVERVIEW', 'CONCEPT_LANGUAGE')),
    observed_at timestamptz NOT NULL,
    normalized_payload jsonb NOT NULL,
    admitted_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (raw_artifact_id, sha256)
);
CREATE INDEX IF NOT EXISTS idx_tmclass_source_evidence_observed
    ON tmclass.source_evidence (observed_at DESC);

CREATE TABLE IF NOT EXISTS tmclass.source (
    source_name text PRIMARY KEY,
    first_observed_at timestamptz NOT NULL,
    last_observed_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS tmclass.office (
    office_key text PRIMARY KEY,
    office_code text,
    office_name text NOT NULL,
    first_observed_at timestamptz NOT NULL,
    last_observed_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS tmclass.term (
    term_id bigint PRIMARY KEY,
    text_value text NOT NULL,
    nice_class smallint NOT NULL CHECK (nice_class BETWEEN 1 AND 45),
    language_code text NOT NULL,
    language_label text,
    detail_observed boolean NOT NULL DEFAULT false,
    first_observed_at timestamptz NOT NULL,
    last_observed_at timestamptz NOT NULL,
    latest_raw_artifact_id text NOT NULL REFERENCES tmclass.source_evidence(raw_artifact_id)
);
CREATE INDEX IF NOT EXISTS idx_tmclass_term_class_language
    ON tmclass.term (nice_class, language_code);
CREATE INDEX IF NOT EXISTS idx_tmclass_term_text
    ON tmclass.term (language_code, lower(text_value));

CREATE TABLE IF NOT EXISTS tmclass.concept (
    concept_id bigint PRIMARY KEY,
    source_name text NOT NULL REFERENCES tmclass.source(source_name),
    reference_id text NOT NULL,
    title text,
    status text,
    nice_class smallint NOT NULL CHECK (nice_class BETWEEN 1 AND 45),
    source_date_text text,
    scope_status text,
    master_count integer CHECK (master_count IS NULL OR master_count >= 0),
    variant_count integer CHECK (variant_count IS NULL OR variant_count >= 0),
    detail_observed boolean NOT NULL DEFAULT false,
    first_observed_at timestamptz NOT NULL,
    last_observed_at timestamptz NOT NULL,
    latest_raw_artifact_id text NOT NULL REFERENCES tmclass.source_evidence(raw_artifact_id),
    UNIQUE (source_name, reference_id)
);
CREATE INDEX IF NOT EXISTS idx_tmclass_concept_class
    ON tmclass.concept (nice_class, source_name);

CREATE TABLE IF NOT EXISTS tmclass.taxonomy_node (
    node_key text PRIMARY KEY,
    nice_class smallint NOT NULL CHECK (nice_class BETWEEN 1 AND 45),
    source_node_id text,
    label text NOT NULL,
    parent_node_key text REFERENCES tmclass.taxonomy_node(node_key),
    depth smallint NOT NULL CHECK (depth >= 0)
);

CREATE TABLE IF NOT EXISTS tmclass.term_taxonomy (
    term_id bigint NOT NULL REFERENCES tmclass.term(term_id) ON DELETE CASCADE,
    node_key text NOT NULL REFERENCES tmclass.taxonomy_node(node_key),
    depth smallint NOT NULL CHECK (depth >= 0),
    PRIMARY KEY (term_id, node_key)
);

CREATE TABLE IF NOT EXISTS tmclass.concept_taxonomy (
    concept_id bigint NOT NULL REFERENCES tmclass.concept(concept_id) ON DELETE CASCADE,
    node_key text NOT NULL REFERENCES tmclass.taxonomy_node(node_key),
    depth smallint NOT NULL CHECK (depth >= 0),
    PRIMARY KEY (concept_id, node_key)
);

CREATE TABLE IF NOT EXISTS tmclass.term_office_acceptance (
    term_id bigint NOT NULL REFERENCES tmclass.term(term_id) ON DELETE CASCADE,
    office_key text NOT NULL REFERENCES tmclass.office(office_key),
    latest_raw_artifact_id text NOT NULL REFERENCES tmclass.source_evidence(raw_artifact_id),
    observed_at timestamptz NOT NULL,
    PRIMARY KEY (term_id, office_key)
);

CREATE TABLE IF NOT EXISTS tmclass.term_translation (
    source_term_id bigint NOT NULL REFERENCES tmclass.term(term_id) ON DELETE CASCADE,
    target_term_id bigint NOT NULL REFERENCES tmclass.term(term_id),
    target_language_code text NOT NULL,
    target_nice_class smallint NOT NULL CHECK (target_nice_class BETWEEN 1 AND 45),
    target_text text NOT NULL,
    quality text NOT NULL,
    latest_raw_artifact_id text NOT NULL REFERENCES tmclass.source_evidence(raw_artifact_id),
    observed_at timestamptz NOT NULL,
    PRIMARY KEY (source_term_id, target_term_id)
);
CREATE INDEX IF NOT EXISTS idx_tmclass_translation_target
    ON tmclass.term_translation (target_term_id, source_term_id);

CREATE TABLE IF NOT EXISTS tmclass.concept_language (
    concept_id bigint NOT NULL REFERENCES tmclass.concept(concept_id) ON DELETE CASCADE,
    language_code text NOT NULL,
    master_term_id bigint REFERENCES tmclass.term(term_id),
    variant_count integer NOT NULL CHECK (variant_count >= 0),
    total_term_count integer NOT NULL CHECK (total_term_count >= 1),
    latest_raw_artifact_id text NOT NULL REFERENCES tmclass.source_evidence(raw_artifact_id),
    observed_at timestamptz NOT NULL,
    PRIMARY KEY (concept_id, language_code),
    CHECK (total_term_count = variant_count + 1)
);

CREATE TABLE IF NOT EXISTS tmclass.concept_term (
    concept_id bigint NOT NULL REFERENCES tmclass.concept(concept_id) ON DELETE CASCADE,
    term_id bigint NOT NULL REFERENCES tmclass.term(term_id),
    language_code text NOT NULL,
    role text NOT NULL CHECK (role IN ('MASTER', 'VARIANT', 'UNKNOWN')),
    ordinal integer CHECK (ordinal IS NULL OR ordinal >= 1),
    latest_raw_artifact_id text NOT NULL REFERENCES tmclass.source_evidence(raw_artifact_id),
    observed_at timestamptz NOT NULL,
    PRIMARY KEY (concept_id, term_id)
);
CREATE INDEX IF NOT EXISTS idx_tmclass_concept_term_term
    ON tmclass.concept_term (term_id, concept_id);
""".strip()


@dataclass(frozen=True, slots=True)
class TmclassSchemaStatus:
    installed_version: str | None
    ready: bool


def migrate_tmclass_schema() -> TmclassSchemaStatus:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute(
                """
                INSERT INTO control.schema_version(component, version)
                VALUES (%s, %s)
                ON CONFLICT (component)
                DO UPDATE SET version = EXCLUDED.version, applied_at = now()
                """,
                (SCHEMA_COMPONENT, SCHEMA_VERSION),
            )
        conn.commit()
    return tmclass_schema_status()


def tmclass_schema_status() -> TmclassSchemaStatus:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT version FROM control.schema_version WHERE component = %s",
                (SCHEMA_COMPONENT,),
            )
            row = cur.fetchone()
            cur.execute("SELECT to_regclass('tmclass.source_evidence') AS evidence_relation")
            relation = cur.fetchone()
    installed = str(row["version"]) if row else None
    return TmclassSchemaStatus(
        installed_version=installed,
        ready=installed == SCHEMA_VERSION and relation["evidence_relation"] is not None,
    )


def assert_tmclass_schema() -> None:
    status = tmclass_schema_status()
    if status.ready:
        return
    raise RuntimeError(
        "TMclass fact store is not migrated: "
        f"installed={status.installed_version!r}, expected={SCHEMA_VERSION!r}. "
        "Run `python -m app.tmclass.cli migrate` before fact admission."
    )
