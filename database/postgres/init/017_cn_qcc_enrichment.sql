CREATE SCHEMA IF NOT EXISTS acquisition;

CREATE TABLE IF NOT EXISTS acquisition.cn_qcc_planner_state (
    state_key text PRIMARY KEY,
    source_rank_watermark bigint NOT NULL DEFAULT 0 CHECK (source_rank_watermark >= 0),
    source_entity_watermark text NOT NULL DEFAULT '',
    backfill_bucket integer NOT NULL DEFAULT 0 CHECK (backfill_bucket >= 0 AND backfill_bucket < 52),
    last_completed_batch_id uuid,
    updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO acquisition.cn_qcc_planner_state (state_key) VALUES ('CN_QCC_APPLICANT') ON CONFLICT (state_key) DO NOTHING;

CREATE TABLE IF NOT EXISTS acquisition.cn_qcc_company_coverage (
    entity_id uuid PRIMARY KEY REFERENCES entity.entity(entity_id) ON DELETE CASCADE,
    source_fingerprint char(64),
    first_fetched_at timestamptz,
    last_fetched_at timestamptz,
    last_result_status text NOT NULL DEFAULT 'NEVER_FETCHED',
    last_snapshot_hash char(64),
    refresh_due_at timestamptz,
    last_batch_id uuid,
    successful_fetch_count integer NOT NULL DEFAULT 0 CHECK (successful_fetch_count >= 0),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (source_fingerprint IS NULL OR source_fingerprint ~ '^[0-9a-fA-F]{64}$'),
    CHECK (last_snapshot_hash IS NULL OR last_snapshot_hash ~ '^[0-9a-fA-F]{64}$'),
    CHECK (last_result_status IN ('NEVER_FETCHED', 'SUCCESS', 'NOT_FOUND', 'FAILED', 'UNATTEMPTED'))
);
CREATE INDEX IF NOT EXISTS ix_cn_qcc_coverage_due ON acquisition.cn_qcc_company_coverage(refresh_due_at) WHERE last_result_status IN ('SUCCESS', 'NOT_FOUND');

CREATE TABLE IF NOT EXISTS acquisition.cn_qcc_batch (
    batch_id uuid PRIMARY KEY,
    batch_key text NOT NULL UNIQUE,
    policy_version text NOT NULL,
    status text NOT NULL,
    target_capacity integer NOT NULL CHECK (target_capacity > 0),
    refresh_days integer NOT NULL CHECK (refresh_days > 0),
    backfill_bucket integer NOT NULL CHECK (backfill_bucket >= 0 AND backfill_bucket < 52),
    task_count integer NOT NULL DEFAULT 0 CHECK (task_count >= 0),
    source_rank_from bigint NOT NULL DEFAULT 0 CHECK (source_rank_from >= 0),
    source_entity_from text NOT NULL DEFAULT '',
    source_rank_to bigint NOT NULL DEFAULT 0 CHECK (source_rank_to >= 0),
    source_entity_to text NOT NULL DEFAULT '',
    planned_at timestamptz NOT NULL DEFAULT now(),
    exported_at timestamptz,
    result_received_at timestamptz,
    completed_at timestamptz,
    export_path text,
    export_sha256 char(64),
    result_path text,
    result_sha256 char(64),
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (status IN ('PLANNED', 'EXPORTED', 'RESULT_RECEIVED', 'COMPLETED', 'FAILED')),
    CHECK (export_sha256 IS NULL OR export_sha256 ~ '^[0-9a-fA-F]{64}$'),
    CHECK (result_sha256 IS NULL OR result_sha256 ~ '^[0-9a-fA-F]{64}$')
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_cn_qcc_one_open_batch ON acquisition.cn_qcc_batch ((1)) WHERE status IN ('PLANNED', 'EXPORTED', 'RESULT_RECEIVED');

CREATE TABLE IF NOT EXISTS acquisition.cn_qcc_task (
    task_id uuid PRIMARY KEY,
    batch_id uuid NOT NULL REFERENCES acquisition.cn_qcc_batch(batch_id) ON DELETE RESTRICT,
    entity_id uuid NOT NULL REFERENCES entity.entity(entity_id) ON DELETE RESTRICT,
    task_type text NOT NULL,
    priority_score integer NOT NULL,
    reason_codes text[] NOT NULL DEFAULT ARRAY[]::text[],
    applicant_name text NOT NULL,
    normalized_name text NOT NULL DEFAULT '',
    applicant_address text NOT NULL DEFAULT '',
    country_code text NOT NULL DEFAULT '',
    region_code text NOT NULL DEFAULT '',
    city text NOT NULL DEFAULT '',
    trademark_count integer NOT NULL DEFAULT 0 CHECK (trademark_count >= 0),
    latest_application_number text NOT NULL DEFAULT '',
    source_rank bigint NOT NULL DEFAULT 0 CHECK (source_rank >= 0),
    source_fingerprint char(64) NOT NULL,
    state text NOT NULL DEFAULT 'PLANNED',
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    result_status text,
    fetched_at timestamptz,
    snapshot_hash char(64),
    error_message text,
    UNIQUE(batch_id, entity_id),
    CHECK (task_type IN ('INITIAL_FETCH', 'REFRESH')),
    CHECK (state IN ('PLANNED', 'EXPORTED', 'SUCCESS', 'NOT_FOUND', 'FAILED', 'UNATTEMPTED')),
    CHECK (source_fingerprint ~ '^[0-9a-fA-F]{64}$'),
    CHECK (snapshot_hash IS NULL OR snapshot_hash ~ '^[0-9a-fA-F]{64}$')
);
CREATE INDEX IF NOT EXISTS ix_cn_qcc_task_batch_state ON acquisition.cn_qcc_task(batch_id, state, priority_score DESC);
CREATE INDEX IF NOT EXISTS ix_cn_qcc_task_entity ON acquisition.cn_qcc_task(entity_id, created_at DESC);

CREATE TABLE IF NOT EXISTS acquisition.cn_qcc_company_observation (
    observation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id uuid NOT NULL UNIQUE REFERENCES acquisition.cn_qcc_task(task_id) ON DELETE RESTRICT,
    batch_id uuid NOT NULL REFERENCES acquisition.cn_qcc_batch(batch_id) ON DELETE RESTRICT,
    entity_id uuid NOT NULL REFERENCES entity.entity(entity_id) ON DELETE RESTRICT,
    observation_hash char(64) NOT NULL UNIQUE,
    qcc_company_id text NOT NULL DEFAULT '',
    company_name text NOT NULL DEFAULT '',
    unified_social_credit_code text NOT NULL DEFAULT '',
    legal_representative text NOT NULL DEFAULT '',
    registration_status text NOT NULL DEFAULT '',
    registered_capital text NOT NULL DEFAULT '',
    establishment_date date,
    registered_address text NOT NULL DEFAULT '',
    business_scope text NOT NULL DEFAULT '',
    phones text[] NOT NULL DEFAULT ARRAY[]::text[],
    emails text[] NOT NULL DEFAULT ARRAY[]::text[],
    websites text[] NOT NULL DEFAULT ARRAY[]::text[],
    contact_name text NOT NULL DEFAULT '',
    contact_title text NOT NULL DEFAULT '',
    contact_phones text[] NOT NULL DEFAULT ARRAY[]::text[],
    contact_emails text[] NOT NULL DEFAULT ARRAY[]::text[],
    source_result_path text NOT NULL DEFAULT '',
    source_result_sha256 char(64) NOT NULL,
    source_row bigint NOT NULL,
    raw_fields jsonb NOT NULL DEFAULT '{}'::jsonb,
    observed_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (observation_hash ~ '^[0-9a-fA-F]{64}$'),
    CHECK (source_result_sha256 ~ '^[0-9a-fA-F]{64}$')
);
CREATE INDEX IF NOT EXISTS ix_cn_qcc_observation_entity ON acquisition.cn_qcc_company_observation(entity_id, observed_at DESC);

INSERT INTO control.schema_version(component, version)
VALUES ('CN_QCC_ENRICHMENT', 'CN_QCC_ENRICHMENT_V1')
ON CONFLICT (component) DO UPDATE SET version = EXCLUDED.version, applied_at = now();
