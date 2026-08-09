CREATE TABLE IF NOT EXISTS markorbit_facts.us_correspondent_current
(
    correspondent_key FixedString(64),
    serial_number String,
    address_1 String,
    address_2 String,
    address_3 String,
    address_4 String,
    address_5 String,
    attorney_name String,
    attorney_docket_number String,
    domestic_representative_name String,
    source_package_kind LowCardinality(String),
    source_effective_date Nullable(Date32),
    source_file String,
    source_row_hash FixedString(64),
    last_source_package_id UUID,
    record_hash FixedString(64),
    source_rank UInt64,
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3),
    is_deleted UInt8 DEFAULT 0
)
ENGINE = ReplacingMergeTree(source_rank, is_deleted)
ORDER BY (serial_number, correspondent_key);

CREATE TABLE IF NOT EXISTS markorbit_facts.us_design_search_current
(
    design_search_key FixedString(64),
    serial_number String,
    code String,
    source_package_kind LowCardinality(String),
    source_effective_date Nullable(Date32),
    source_file String,
    source_row_hash FixedString(64),
    last_source_package_id UUID,
    record_hash FixedString(64),
    source_rank UInt64,
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3),
    is_deleted UInt8 DEFAULT 0
)
ENGINE = ReplacingMergeTree(source_rank, is_deleted)
ORDER BY (serial_number, design_search_key);

CREATE TABLE IF NOT EXISTS markorbit_facts.us_prior_registration_current
(
    prior_registration_key FixedString(64),
    serial_number String,
    relationship_type String,
    number String,
    source_package_kind LowCardinality(String),
    source_effective_date Nullable(Date32),
    source_file String,
    source_row_hash FixedString(64),
    last_source_package_id UUID,
    record_hash FixedString(64),
    source_rank UInt64,
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3),
    is_deleted UInt8 DEFAULT 0
)
ENGINE = ReplacingMergeTree(source_rank, is_deleted)
ORDER BY (serial_number, prior_registration_key);

CREATE TABLE IF NOT EXISTS markorbit_facts.us_foreign_application_current
(
    foreign_application_key FixedString(64),
    serial_number String,
    entry_number UInt32,
    application_number String,
    country String,
    filing_date Nullable(Date32),
    foreign_priority_claimed UInt8,
    source_package_kind LowCardinality(String),
    source_effective_date Nullable(Date32),
    source_file String,
    source_row_hash FixedString(64),
    last_source_package_id UUID,
    record_hash FixedString(64),
    source_rank UInt64,
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3),
    is_deleted UInt8 DEFAULT 0
)
ENGINE = ReplacingMergeTree(source_rank, is_deleted)
ORDER BY (serial_number, foreign_application_key);

CREATE TABLE IF NOT EXISTS markorbit_facts.us_madrid_filing_current
(
    madrid_filing_key FixedString(64),
    serial_number String,
    entry_number UInt32,
    reference_number String,
    original_filing_date_uspto Nullable(Date32),
    international_registration_number String,
    international_registration_date Nullable(Date32),
    international_status_code String,
    international_status_date Nullable(Date32),
    international_renewal_date Nullable(Date32),
    source_package_kind LowCardinality(String),
    source_effective_date Nullable(Date32),
    source_file String,
    source_row_hash FixedString(64),
    last_source_package_id UUID,
    record_hash FixedString(64),
    source_rank UInt64,
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3),
    is_deleted UInt8 DEFAULT 0
)
ENGINE = ReplacingMergeTree(source_rank, is_deleted)
ORDER BY (serial_number, madrid_filing_key);

CREATE TABLE IF NOT EXISTS markorbit_facts.us_madrid_event_history
(
    madrid_event_key FixedString(64),
    serial_number String,
    filing_entry_number UInt32,
    filing_reference_number String,
    event_entry_number UInt32,
    code String,
    event_date Nullable(Date32),
    description_text String,
    source_package_kind LowCardinality(String),
    source_effective_date Nullable(Date32),
    source_file String,
    source_row_hash FixedString(64),
    source_package_id UUID,
    source_rank UInt64,
    observed_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(source_rank)
ORDER BY madrid_event_key;

INSERT INTO markorbit_facts.schema_version (component, version)
SELECT 'US_CORE', 'US_M1.3'
WHERE NOT EXISTS
(
    SELECT 1 FROM markorbit_facts.schema_version FINAL
    WHERE component = 'US_CORE' AND version = 'US_M1.3'
);
