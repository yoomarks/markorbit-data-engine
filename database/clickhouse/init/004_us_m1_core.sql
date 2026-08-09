INSERT INTO markorbit_facts.schema_version (component, version)
SELECT 'US_CORE', 'US_M1.0'
WHERE NOT EXISTS
(
    SELECT 1
    FROM markorbit_facts.schema_version FINAL
    WHERE component = 'US_CORE' AND version = 'US_M1.0'
);

CREATE TABLE IF NOT EXISTS markorbit_facts.us_case_current
(
    case_id UUID,
    jurisdiction LowCardinality(String) DEFAULT 'US',
    serial_number String,
    registration_number String,
    filing_date Nullable(Date32),
    publication_date Nullable(Date32),
    registration_date Nullable(Date32),
    abandonment_date Nullable(Date32),
    cancellation_date Nullable(Date32),
    renewal_date Nullable(Date32),
    status_code String,
    status_date Nullable(Date32),
    mark_identification String,
    mark_drawing_code String,
    current_location String,
    location_date Nullable(Date32),
    examiner_name String,
    law_office_code String,
    standard_character_claimed UInt8,
    use_1a UInt8,
    intent_to_use_1b UInt8,
    foreign_application_44d UInt8,
    foreign_registration_44e UInt8,
    madrid_66a UInt8,
    no_basis UInt8,
    international_registration_number String,
    international_registration_status_code String,
    international_registration_status_date Nullable(Date32),
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
ORDER BY serial_number;

CREATE TABLE IF NOT EXISTS markorbit_facts.us_owner_current
(
    owner_key FixedString(64),
    serial_number String,
    entry_number UInt16,
    party_type String,
    legal_entity_type_code String,
    party_name String,
    party_name_norm String,
    nationality_country String,
    nationality_state String,
    nationality_other String,
    address_1 String,
    address_2 String,
    city String,
    state String,
    country String,
    postcode String,
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
ORDER BY (serial_number, owner_key);

CREATE TABLE IF NOT EXISTS markorbit_facts.us_classification_current
(
    classification_key FixedString(64),
    serial_number String,
    primary_code String,
    international_codes Array(String),
    us_codes Array(String),
    status_code String,
    status_date Nullable(Date32),
    first_use_anywhere Nullable(Date32),
    first_use_anywhere_raw String,
    first_use_commerce Nullable(Date32),
    first_use_commerce_raw String,
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
ORDER BY (serial_number, classification_key);

CREATE TABLE IF NOT EXISTS markorbit_facts.us_event_history
(
    event_key FixedString(64),
    serial_number String,
    event_code String,
    event_date Nullable(Date32),
    event_sequence UInt32,
    event_type_code String,
    source_package_kind LowCardinality(String),
    source_effective_date Nullable(Date32),
    source_file String,
    source_row_hash FixedString(64),
    source_package_id UUID,
    source_rank UInt64,
    observed_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(source_rank)
ORDER BY event_key;

CREATE TABLE IF NOT EXISTS markorbit_facts.us_statement_current
(
    statement_key FixedString(64),
    serial_number String,
    type_code String,
    statement_text String,
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
ORDER BY (serial_number, statement_key);
