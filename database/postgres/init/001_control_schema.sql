CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS control;
CREATE SCHEMA IF NOT EXISTS entity;

CREATE TABLE IF NOT EXISTS control.schema_version (
    component text PRIMARY KEY,
    version text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO control.schema_version(component, version)
VALUES ('CN_CORE', 'M1.5')
ON CONFLICT (component)
DO UPDATE SET version = EXCLUDED.version, applied_at = now();

CREATE TABLE IF NOT EXISTS control.source_package (
    package_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    package_sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    jurisdiction text NOT NULL,
    file_name text NOT NULL,
    file_path text NOT NULL,
    file_size bigint NOT NULL CHECK (file_size >= 0),
    sha256 char(64) NOT NULL UNIQUE,
    source_modified_at timestamptz,

    package_kind text NOT NULL DEFAULT 'UNKNOWN',
    partition_dimension text NOT NULL DEFAULT '',
    partition_value text NOT NULL DEFAULT '',
    source_period_start date,
    source_period_end date,
    source_sequence bigint NOT NULL DEFAULT 0,
    source_rank bigint NOT NULL DEFAULT 0,
    dataset_release_date date,

    status text NOT NULL,
    profile jsonb NOT NULL DEFAULT '{}'::jsonb,
    schema_version text NOT NULL DEFAULT 'M1.5',
    archived_path text,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,
    error_message text
);

CREATE INDEX IF NOT EXISTS ix_source_package_jurisdiction_seen
ON control.source_package (jurisdiction, first_seen_at DESC);

CREATE INDEX IF NOT EXISTS ix_source_package_queue
ON control.source_package (jurisdiction, status, source_rank, package_sequence);

CREATE TABLE IF NOT EXISTS control.source_package_file (
    package_file_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    package_id uuid NOT NULL REFERENCES control.source_package(package_id) ON DELETE CASCADE,
    internal_name text NOT NULL,
    original_internal_name text NOT NULL,
    file_role text,
    file_size bigint NOT NULL DEFAULT 0,
    compressed_size bigint NOT NULL DEFAULT 0,
    filename_encoding text,
    filename_repaired boolean NOT NULL DEFAULT false,
    content_encoding text,
    header_raw jsonb NOT NULL DEFAULT '[]'::jsonb,
    header_canonical jsonb NOT NULL DEFAULT '[]'::jsonb,
    physical_rows bigint NOT NULL DEFAULT 0,
    logical_rows bigint NOT NULL DEFAULT 0,
    continuation_rows bigint NOT NULL DEFAULT 0,
    repaired_rows bigint NOT NULL DEFAULT 0,
    failed_rows bigint NOT NULL DEFAULT 0,
    replacement_chars bigint NOT NULL DEFAULT 0,
    max_record_length bigint NOT NULL DEFAULT 0,
    max_field_length bigint NOT NULL DEFAULT 0,
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(package_id, internal_name)
);

CREATE INDEX IF NOT EXISTS ix_source_package_file_package
ON control.source_package_file(package_id, file_role);

CREATE TABLE IF NOT EXISTS control.job_run (
    run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_type text NOT NULL,
    trigger_type text NOT NULL,
    status text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_message text
);

CREATE INDEX IF NOT EXISTS ix_job_run_started_at
ON control.job_run (started_at DESC);

CREATE TABLE IF NOT EXISTS control.data_quality_issue (
    issue_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    issue_key char(64) NOT NULL UNIQUE,
    package_id uuid REFERENCES control.source_package(package_id),
    run_id uuid REFERENCES control.job_run(run_id),
    jurisdiction text NOT NULL,
    issue_type text NOT NULL,
    severity text NOT NULL,
    occurrence_count bigint NOT NULL DEFAULT 1,
    source_file text,
    source_row bigint,
    raw_excerpt text,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'OPEN',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz
);

CREATE INDEX IF NOT EXISTS ix_data_quality_issue_package
ON control.data_quality_issue(package_id, issue_type, severity);

CREATE TABLE IF NOT EXISTS control.schema_alias_observation (
    observation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    jurisdiction text NOT NULL,
    file_role text NOT NULL,
    raw_header text NOT NULL,
    normalized_header text NOT NULL,
    package_id uuid REFERENCES control.source_package(package_id),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(jurisdiction, file_role, raw_header)
);

CREATE TABLE IF NOT EXISTS control.source_code_mapping (
    jurisdiction text NOT NULL,
    field_name text NOT NULL,
    raw_code text NOT NULL,
    normalized_value text NOT NULL,
    mapping_status text NOT NULL,
    mapping_version text NOT NULL,
    evidence_note text NOT NULL DEFAULT '',
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (jurisdiction, field_name, raw_code, mapping_version)
);

INSERT INTO control.source_code_mapping (
    jurisdiction, field_name, raw_code, normalized_value,
    mapping_status, mapping_version, evidence_note
)
VALUES
    ('CN', 'goods_status', '', 'ACTIVE', 'RULE', 'CN_GOODS_STATUS_V1_NUMERIC_UNMAPPED', 'Blank status is treated as active by pipeline default.'),
    ('CN', 'goods_status', '0', 'UNKNOWN', 'UNVERIFIED', 'CN_GOODS_STATUS_V1_NUMERIC_UNMAPPED', 'Meaning not established by supplied source materials.'),
    ('CN', 'goods_status', '1', 'UNKNOWN', 'UNVERIFIED', 'CN_GOODS_STATUS_V1_NUMERIC_UNMAPPED', 'Meaning not established by supplied source materials.'),
    ('CN', 'goods_status', '2', 'UNKNOWN', 'UNVERIFIED', 'CN_GOODS_STATUS_V1_NUMERIC_UNMAPPED', 'Meaning not established by supplied source materials.')
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS entity.entity (
    entity_id uuid PRIMARY KEY,
    entity_key char(64) NOT NULL UNIQUE,
    entity_type text NOT NULL,
    canonical_name text NOT NULL,
    normalized_name text NOT NULL DEFAULT '',
    normalized_address text NOT NULL DEFAULT '',
    country_code char(2),
    region_code text,
    city text,
    status text NOT NULL DEFAULT 'CANDIDATE',
    resolution_method text NOT NULL,
    source_primary text,
    confidence_score numeric(5,4),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_entity_geo
ON entity.entity (country_code, region_code, city);

CREATE INDEX IF NOT EXISTS ix_entity_match
ON entity.entity (normalized_name, normalized_address);

CREATE TABLE IF NOT EXISTS entity.entity_alias (
    alias_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id uuid NOT NULL REFERENCES entity.entity(entity_id),
    alias_name text NOT NULL,
    normalized_name text NOT NULL,
    language_code text,
    source text NOT NULL,
    confidence_score numeric(5,4),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(entity_id, normalized_name, source)
);

CREATE INDEX IF NOT EXISTS ix_entity_alias_normalized
ON entity.entity_alias (normalized_name);

CREATE TABLE IF NOT EXISTS entity.entity_mention (
    mention_id uuid PRIMARY KEY,
    jurisdiction text NOT NULL,
    source_case_key text NOT NULL,
    role text NOT NULL,
    raw_name text NOT NULL,
    normalized_name text NOT NULL,
    raw_address text NOT NULL DEFAULT '',
    normalized_address text NOT NULL DEFAULT '',
    country_code char(2),
    region_code text,
    city text,
    geo_confidence numeric(5,2),
    source_package_id uuid REFERENCES control.source_package(package_id),
    source_internal_file text,
    source_start_line bigint,
    entity_id uuid REFERENCES entity.entity(entity_id),
    match_status text NOT NULL DEFAULT 'UNRESOLVED',
    resolution_method text NOT NULL DEFAULT '',
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_entity_mention_match_key
ON entity.entity_mention(normalized_name, normalized_address);

CREATE INDEX IF NOT EXISTS ix_entity_mention_geo
ON entity.entity_mention(country_code, region_code, city);
