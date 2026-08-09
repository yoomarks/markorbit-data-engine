CREATE TABLE IF NOT EXISTS markorbit_facts.us_ttab_proceeding_history
(
    observation_key FixedString(64),
    proceeding_number String,
    proceeding_type String,
    filing_date Nullable(Date32),
    filing_date_raw String,
    status_text String,
    status_date Nullable(Date32),
    status_date_raw String,
    general_contact_number String,
    interlocutory_attorney String,
    paralegal_name String,
    record_hash FixedString(64),
    source_kind LowCardinality(String),
    source_snapshot_at DateTime64(3, 'UTC'),
    source_file String,
    source_package_id UUID,
    source_rank UInt64,
    observed_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (proceeding_number, source_rank, source_package_id);

CREATE TABLE IF NOT EXISTS markorbit_facts.us_ttab_party_history
(
    observation_key FixedString(64),
    party_key FixedString(64),
    proceeding_number String,
    side LowCardinality(String),
    ordinal UInt16,
    party_name String,
    correspondent_name String,
    correspondent_address String,
    correspondent_email_text String,
    correspondent_phone String,
    record_hash FixedString(64),
    source_kind LowCardinality(String),
    source_snapshot_at DateTime64(3, 'UTC'),
    source_file String,
    source_package_id UUID,
    source_rank UInt64,
    observed_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (proceeding_number, side, party_key, source_rank, source_package_id);

CREATE TABLE IF NOT EXISTS markorbit_facts.us_ttab_property_history
(
    observation_key FixedString(64),
    property_key FixedString(64),
    proceeding_number String,
    party_side LowCardinality(String),
    party_ordinal UInt16,
    ordinal UInt16,
    serial_number String,
    registration_number String,
    mark_text String,
    application_status String,
    record_hash FixedString(64),
    source_kind LowCardinality(String),
    source_snapshot_at DateTime64(3, 'UTC'),
    source_file String,
    source_package_id UUID,
    source_rank UInt64,
    observed_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (serial_number, proceeding_number, property_key, source_rank, source_package_id);

CREATE TABLE IF NOT EXISTS markorbit_facts.us_ttab_docket_history
(
    observation_key FixedString(64),
    docket_key FixedString(64),
    proceeding_number String,
    ordinal UInt32,
    entry_number String,
    filing_date Nullable(Date32),
    filing_date_raw String,
    history_text String,
    due_date Nullable(Date32),
    due_date_raw String,
    document_url String,
    record_hash FixedString(64),
    source_kind LowCardinality(String),
    source_snapshot_at DateTime64(3, 'UTC'),
    source_file String,
    source_package_id UUID,
    source_rank UInt64,
    observed_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (proceeding_number, docket_key, source_rank, source_package_id);

INSERT INTO markorbit_facts.schema_version (component, version)
SELECT 'US_TTAB', 'US_TTAB_M1.0'
WHERE NOT EXISTS
(
    SELECT 1 FROM markorbit_facts.schema_version FINAL
    WHERE component = 'US_TTAB' AND version = 'US_TTAB_M1.0'
);
