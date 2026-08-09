CREATE SCHEMA IF NOT EXISTS interpretation;

CREATE TABLE IF NOT EXISTS interpretation.us_event_role_ruleset_version (
    ruleset_version text PRIMARY KEY,
    event_reference_version text NOT NULL
        REFERENCES reference.us_trademark_event_reference_version(reference_version)
        ON DELETE RESTRICT,
    source_document_name text NOT NULL,
    source_document_sha256 char(64) NOT NULL,
    normalized_payload_sha256 char(64) NOT NULL,
    rule_count integer NOT NULL CHECK (rule_count > 0),
    is_active boolean NOT NULL DEFAULT false,
    imported_at timestamptz NOT NULL DEFAULT now(),
    evidence_note text NOT NULL DEFAULT '',
    CHECK (source_document_sha256 ~ '^[0-9a-fA-F]{64}$'),
    CHECK (normalized_payload_sha256 ~ '^[0-9a-fA-F]{64}$')
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_us_event_role_ruleset_active
ON interpretation.us_event_role_ruleset_version ((1))
WHERE is_active;

CREATE TABLE IF NOT EXISTS interpretation.us_event_role_rule (
    ruleset_version text NOT NULL
        REFERENCES interpretation.us_event_role_ruleset_version(ruleset_version)
        ON DELETE RESTRICT,
    rule_id text NOT NULL,
    event_code text NOT NULL,
    role text NOT NULL,
    rationale text NOT NULL,
    source_refs jsonb NOT NULL,
    PRIMARY KEY (ruleset_version, rule_id),
    UNIQUE (ruleset_version, event_code),
    CHECK (event_code ~ '^[A-Z0-9][A-Z0-9_.:-]{0,63}$'),
    CHECK (role IN (
        'OFFICE_ACTION_NONFINAL_ISSUED',
        'OFFICE_ACTION_FINAL_ISSUED',
        'OFFICE_ACTION_RESPONSE_FILED',
        'NOTICE_OF_ALLOWANCE_ISSUED',
        'STATEMENT_OF_USE_FILED',
        'ITU_EXTENSION_GRANTED',
        'OPPOSITION_EXTENSION_30_GRANTED',
        'OPPOSITION_EXTENSION_90_GRANTED',
        'OPPOSITION_EXTENSION_150_GRANTED'
    )),
    CHECK (rationale <> ''),
    CHECK (jsonb_typeof(source_refs) = 'array' AND jsonb_array_length(source_refs) > 0)
);

CREATE INDEX IF NOT EXISTS ix_us_event_role_rule_event_code
ON interpretation.us_event_role_rule (ruleset_version, event_code);
