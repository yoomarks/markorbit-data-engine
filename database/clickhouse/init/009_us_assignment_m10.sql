CREATE TABLE IF NOT EXISTS markorbit_facts.us_assignment_record_history
(
    observation_key FixedString(64),
    reel_frame_id String,
    reel_no String,
    frame_no String,
    recorded_date Nullable(Date32),
    recorded_date_raw String,
    last_update_date Nullable(Date32),
    last_update_date_raw String,
    page_count Nullable(UInt32),
    conveyance_text String,
    purge_indicator String,
    correspondent_name String,
    correspondent_address_1 String,
    correspondent_address_2 String,
    correspondent_address_3 String,
    correspondent_address_4 String,
    record_hash FixedString(64),
    source_kind LowCardinality(String),
    source_effective_date Date32,
    source_file String,
    source_package_id UUID,
    source_rank UInt64,
    observed_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (reel_frame_id, source_rank, source_package_id);

CREATE TABLE IF NOT EXISTS markorbit_facts.us_assignment_assignor_history
(
    observation_key FixedString(64),
    party_key FixedString(64),
    reel_frame_id String,
    ordinal UInt16,
    party_name String,
    address_1 String,
    address_2 String,
    city String,
    state String,
    postcode String,
    country String,
    nationality String,
    legal_entity_text String,
    formerly_statement String,
    composed_of_statement String,
    dba_statement String,
    execution_date Nullable(Date32),
    execution_date_raw String,
    acknowledgement_date Nullable(Date32),
    acknowledgement_date_raw String,
    record_hash FixedString(64),
    source_kind LowCardinality(String),
    source_effective_date Date32,
    source_file String,
    source_package_id UUID,
    source_rank UInt64,
    observed_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (reel_frame_id, party_key, source_rank, source_package_id);

CREATE TABLE IF NOT EXISTS markorbit_facts.us_assignment_assignee_history
(
    observation_key FixedString(64),
    party_key FixedString(64),
    reel_frame_id String,
    ordinal UInt16,
    party_name String,
    address_1 String,
    address_2 String,
    city String,
    state String,
    postcode String,
    country String,
    nationality String,
    legal_entity_text String,
    formerly_statement String,
    composed_of_statement String,
    dba_statement String,
    execution_date Nullable(Date32),
    execution_date_raw String,
    acknowledgement_date Nullable(Date32),
    acknowledgement_date_raw String,
    record_hash FixedString(64),
    source_kind LowCardinality(String),
    source_effective_date Date32,
    source_file String,
    source_package_id UUID,
    source_rank UInt64,
    observed_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (reel_frame_id, party_key, source_rank, source_package_id);

CREATE TABLE IF NOT EXISTS markorbit_facts.us_assignment_property_history
(
    observation_key FixedString(64),
    property_key FixedString(64),
    reel_frame_id String,
    ordinal UInt16,
    serial_number String,
    registration_number String,
    international_registration_number String,
    record_hash FixedString(64),
    source_kind LowCardinality(String),
    source_effective_date Date32,
    source_file String,
    source_package_id UUID,
    source_rank UInt64,
    observed_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (serial_number, reel_frame_id, property_key, source_rank, source_package_id);

INSERT INTO markorbit_facts.schema_version (component, version)
SELECT 'US_ASSIGNMENT', 'US_ASSIGNMENT_M1.0'
WHERE NOT EXISTS
(
    SELECT 1 FROM markorbit_facts.schema_version FINAL
    WHERE component = 'US_ASSIGNMENT' AND version = 'US_ASSIGNMENT_M1.0'
);
