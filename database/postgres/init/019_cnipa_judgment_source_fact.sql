CREATE SCHEMA IF NOT EXISTS cnipa_judgment;

CREATE TABLE IF NOT EXISTS cnipa_judgment.list_observation (
    observation_key char(64) PRIMARY KEY,
    document_kind text NOT NULL CHECK (
        document_kind IN (
            'REGISTRATION_EXAMINATION',
            'OPPOSITION_DECISION',
            'REVIEW_ADJUDICATION'
        )
    ),
    source_record_id text NOT NULL,
    semantic_family text NOT NULL CHECK (semantic_family IN ('EXAMINATION', 'PROCEEDING')),
    detail_canonical_uri text NOT NULL,
    source_row_sha256 char(64) NOT NULL,
    observed_at timestamptz NOT NULL,
    source_artifact_ref text NOT NULL,
    source_fields jsonb NOT NULL,
    application_number text NOT NULL DEFAULT '',
    registration_number text NOT NULL DEFAULT '',
    trademark_name text NOT NULL DEFAULT '',
    source_title text NOT NULL DEFAULT '',
    source_date text NOT NULL DEFAULT '',
    source_document_number text NOT NULL DEFAULT '',
    cited_registration_text text NOT NULL DEFAULT '',
    initial_markdown_ref text NOT NULL DEFAULT '',
    initial_markdown_sha256 char(64),
    ingested_at timestamptz NOT NULL DEFAULT now(),
    CHECK (source_row_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (
        initial_markdown_sha256 IS NULL
        OR initial_markdown_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CHECK (
        (initial_markdown_ref = '' AND initial_markdown_sha256 IS NULL)
        OR (initial_markdown_ref <> '' AND initial_markdown_sha256 IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS ix_cnipa_judgment_list_observation_identity
ON cnipa_judgment.list_observation (
    document_kind,
    source_record_id,
    observed_at DESC
);

CREATE TABLE IF NOT EXISTS cnipa_judgment.current (
    document_kind text NOT NULL CHECK (
        document_kind IN (
            'REGISTRATION_EXAMINATION',
            'OPPOSITION_DECISION',
            'REVIEW_ADJUDICATION'
        )
    ),
    source_record_id text NOT NULL,
    observation_key char(64) NOT NULL
        REFERENCES cnipa_judgment.list_observation(observation_key) ON DELETE RESTRICT,
    semantic_family text NOT NULL CHECK (semantic_family IN ('EXAMINATION', 'PROCEEDING')),
    detail_canonical_uri text NOT NULL,
    source_row_sha256 char(64) NOT NULL,
    observed_at timestamptz NOT NULL,
    source_artifact_ref text NOT NULL,
    source_fields jsonb NOT NULL,
    application_number text NOT NULL DEFAULT '',
    registration_number text NOT NULL DEFAULT '',
    trademark_name text NOT NULL DEFAULT '',
    source_title text NOT NULL DEFAULT '',
    source_date text NOT NULL DEFAULT '',
    source_document_number text NOT NULL DEFAULT '',
    cited_registration_text text NOT NULL DEFAULT '',
    initial_markdown_ref text NOT NULL DEFAULT '',
    initial_markdown_sha256 char(64),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (document_kind, source_record_id),
    CHECK (source_row_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (
        initial_markdown_sha256 IS NULL
        OR initial_markdown_sha256 ~ '^[0-9a-f]{64}$'
    )
);

CREATE INDEX IF NOT EXISTS ix_cnipa_judgment_current_registration
ON cnipa_judgment.current (registration_number)
WHERE registration_number <> '';

CREATE INDEX IF NOT EXISTS ix_cnipa_judgment_current_application
ON cnipa_judgment.current (application_number)
WHERE application_number <> '';

CREATE TABLE IF NOT EXISTS cnipa_judgment.window_observation (
    window_key char(64) PRIMARY KEY,
    document_kind text NOT NULL CHECK (
        document_kind IN (
            'REGISTRATION_EXAMINATION',
            'OPPOSITION_DECISION',
            'REVIEW_ADJUDICATION'
        )
    ),
    query_from date,
    query_to date,
    observed_at timestamptz NOT NULL,
    source_artifact_ref text NOT NULL,
    record_count integer NOT NULL CHECK (record_count >= 0),
    ingested_at timestamptz NOT NULL DEFAULT now(),
    CHECK (
        (query_from IS NULL AND query_to IS NULL)
        OR (query_from IS NOT NULL AND query_to IS NOT NULL AND query_from <= query_to)
    )
);
