CREATE TABLE IF NOT EXISTS acquisition.us_tsdr_contact_observation (
    observation_id uuid PRIMARY KEY,
    task_id uuid NOT NULL UNIQUE
        REFERENCES acquisition.us_tsdr_task(task_id) ON DELETE RESTRICT,
    batch_id uuid NOT NULL
        REFERENCES acquisition.us_tsdr_batch(batch_id) ON DELETE RESTRICT,
    serial_number text NOT NULL,
    source_csv_path text NOT NULL,
    source_csv_sha256 char(64) NOT NULL,
    observation_sha256 char(64) NOT NULL,
    source_url text NOT NULL DEFAULT '',
    collected_at timestamptz,
    collected_at_evidence text NOT NULL,
    attorney_name text NOT NULL DEFAULT '',
    docket_number text NOT NULL DEFAULT '',
    attorney_primary_email text NOT NULL DEFAULT '',
    attorney_email_authorized boolean,
    correspondent_name_address_raw text NOT NULL DEFAULT '',
    correspondent_name_address_lines text[] NOT NULL DEFAULT ARRAY[]::text[],
    phone text NOT NULL DEFAULT '',
    correspondent_emails text[] NOT NULL DEFAULT ARRAY[]::text[],
    correspondent_email_authorized boolean,
    raw_fields jsonb NOT NULL DEFAULT '{}'::jsonb,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    CHECK (serial_number ~ '^[0-9]{8}$'),
    CHECK (source_csv_sha256 ~ '^[0-9a-fA-F]{64}$'),
    CHECK (observation_sha256 ~ '^[0-9a-fA-F]{64}$'),
    CHECK (collected_at_evidence IN ('COLLECTOR_FIELD', 'INGESTED_AT_FALLBACK'))
);

CREATE INDEX IF NOT EXISTS ix_us_tsdr_contact_observation_serial
ON acquisition.us_tsdr_contact_observation (serial_number, ingested_at DESC);
