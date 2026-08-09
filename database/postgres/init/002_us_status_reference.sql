CREATE SCHEMA IF NOT EXISTS reference;

CREATE TABLE IF NOT EXISTS reference.us_trademark_status_reference_version (
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
    evidence_note text NOT NULL DEFAULT ''
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_us_trademark_status_reference_active
ON reference.us_trademark_status_reference_version ((1))
WHERE is_active;

CREATE TABLE IF NOT EXISTS reference.us_trademark_status_code (
    reference_version text NOT NULL
        REFERENCES reference.us_trademark_status_reference_version(reference_version)
        ON DELETE RESTRICT,
    raw_code text NOT NULL,
    official_description text NOT NULL,
    official_definition text NOT NULL DEFAULT '',
    official_category text NOT NULL DEFAULT '',
    source_locator text NOT NULL DEFAULT '',
    PRIMARY KEY (reference_version, raw_code),
    CHECK (raw_code <> ''),
    CHECK (official_description <> '')
);

CREATE INDEX IF NOT EXISTS ix_us_trademark_status_code_code
ON reference.us_trademark_status_code (raw_code, reference_version);
