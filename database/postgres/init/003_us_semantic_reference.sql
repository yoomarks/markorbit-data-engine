CREATE SCHEMA IF NOT EXISTS reference;
CREATE SCHEMA IF NOT EXISTS interpretation;

CREATE TABLE IF NOT EXISTS reference.us_trademark_event_reference_version (
    reference_version text PRIMARY KEY,
    authority text NOT NULL,
    reference_kind text NOT NULL,
    source_document_name text NOT NULL,
    source_document_date date NOT NULL,
    source_url text NOT NULL,
    source_document_sha256 char(64) NOT NULL,
    normalized_payload_sha256 char(64) NOT NULL,
    record_count integer NOT NULL CHECK (record_count > 0),
    is_active boolean NOT NULL DEFAULT false,
    imported_at timestamptz NOT NULL DEFAULT now(),
    evidence_note text NOT NULL DEFAULT '',
    CHECK (source_document_sha256 ~ '^[0-9a-fA-F]{64}$'),
    CHECK (normalized_payload_sha256 ~ '^[0-9a-fA-F]{64}$')
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_us_trademark_event_reference_active
ON reference.us_trademark_event_reference_version ((1))
WHERE is_active;

CREATE TABLE IF NOT EXISTS reference.us_trademark_event_code (
    reference_version text NOT NULL
        REFERENCES reference.us_trademark_event_reference_version(reference_version)
        ON DELETE RESTRICT,
    raw_code text NOT NULL,
    official_description text NOT NULL,
    official_definition text NOT NULL DEFAULT '',
    official_category text NOT NULL DEFAULT '',
    source_locator text NOT NULL DEFAULT '',
    PRIMARY KEY (reference_version, raw_code),
    CHECK (raw_code ~ '^[A-Z0-9][A-Z0-9_.:-]{0,63}$'),
    CHECK (official_description <> '')
);

CREATE INDEX IF NOT EXISTS ix_us_trademark_event_code_code
ON reference.us_trademark_event_code (raw_code, reference_version);

CREATE TABLE IF NOT EXISTS interpretation.us_status_ruleset_version (
    ruleset_version text PRIMARY KEY,
    status_reference_version text NOT NULL
        REFERENCES reference.us_trademark_status_reference_version(reference_version)
        ON DELETE RESTRICT,
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

CREATE UNIQUE INDEX IF NOT EXISTS ux_us_status_ruleset_active
ON interpretation.us_status_ruleset_version ((1))
WHERE is_active;

CREATE TABLE IF NOT EXISTS interpretation.us_status_rule (
    ruleset_version text NOT NULL
        REFERENCES interpretation.us_status_ruleset_version(ruleset_version)
        ON DELETE RESTRICT,
    rule_id text NOT NULL,
    priority integer NOT NULL DEFAULT 0,
    status_codes jsonb NOT NULL,
    event_codes_any jsonb NOT NULL DEFAULT '[]'::jsonb,
    event_codes_all jsonb NOT NULL DEFAULT '[]'::jsonb,
    result_label text NOT NULL,
    confidence text NOT NULL,
    rationale text NOT NULL,
    source_refs jsonb NOT NULL,
    PRIMARY KEY (ruleset_version, rule_id),
    CHECK (jsonb_typeof(status_codes) = 'array' AND jsonb_array_length(status_codes) > 0),
    CHECK (jsonb_typeof(event_codes_any) = 'array'),
    CHECK (jsonb_typeof(event_codes_all) = 'array'),
    CHECK (jsonb_typeof(source_refs) = 'array' AND jsonb_array_length(source_refs) > 0),
    CHECK (confidence IN ('LOW', 'MEDIUM', 'HIGH')),
    CHECK (result_label <> ''),
    CHECK (rationale <> '')
);

CREATE INDEX IF NOT EXISTS ix_us_status_rule_priority
ON interpretation.us_status_rule (ruleset_version, priority DESC, rule_id);
