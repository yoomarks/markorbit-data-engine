CREATE TABLE IF NOT EXISTS markorbit_facts.us_registration_candidate_lookup
(
    registration_number String,
    serial_number String,
    source_row_hash FixedString(64),
    record_hash FixedString(64),
    source_package_id UUID,
    source_rank UInt64,
    observed_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(source_rank)
ORDER BY (registration_number, serial_number);

INSERT INTO markorbit_facts.schema_version (component, version)
SELECT 'US_REGISTRATION_CANDIDATE_LOOKUP', 'US_REGISTRATION_CANDIDATE_LOOKUP_SCHEMA_V1'
WHERE NOT EXISTS
(
    SELECT 1 FROM markorbit_facts.schema_version FINAL
    WHERE component = 'US_REGISTRATION_CANDIDATE_LOOKUP'
);
