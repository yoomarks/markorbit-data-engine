CREATE TABLE IF NOT EXISTS markorbit_facts.us_case_observation_history
(
    observation_key FixedString(64),
    serial_number String,
    registration_number String,
    transaction_date Nullable(Date32),
    status_code String,
    status_date Nullable(Date32),
    current_location String,
    location_date Nullable(Date32),
    filing_date Nullable(Date32),
    publication_date Nullable(Date32),
    registration_date Nullable(Date32),
    abandonment_date Nullable(Date32),
    cancellation_date Nullable(Date32),
    renewal_date Nullable(Date32),
    mark_identification String,
    use_1a_current UInt8,
    intent_to_use_1b_current UInt8,
    foreign_registration_44e_current UInt8,
    madrid_66a_current UInt8,
    renewal_filed UInt8,
    section_8_filed UInt8,
    section_8_accepted UInt8,
    section_15_filed UInt8,
    section_15_acknowledged UInt8,
    opposition_pending UInt8,
    cancellation_pending UInt8,
    owner_set_hash FixedString(64),
    owner_record_set_hash FixedString(64),
    owner_count UInt16,
    owner_names Array(String),
    case_record_hash FixedString(64),
    observation_hash FixedString(64),
    source_package_kind LowCardinality(String),
    source_effective_date Nullable(Date32),
    source_file String,
    source_package_id UUID,
    source_rank UInt64,
    observed_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (serial_number, source_rank, source_package_id);

INSERT INTO markorbit_facts.schema_version (component, version)
SELECT 'US_CORE', 'US_M1.4'
WHERE NOT EXISTS
(
    SELECT 1 FROM markorbit_facts.schema_version FINAL
    WHERE component = 'US_CORE' AND version = 'US_M1.4'
);
