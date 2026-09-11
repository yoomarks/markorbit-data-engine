CREATE TABLE IF NOT EXISTS markorbit_facts.us_applicant_candidate_current
(
    candidate_key FixedString(64),
    owner_key FixedString(64),
    serial_number String,
    entry_number UInt16,
    party_type String,
    legal_entity_type_code String,
    entity_statement String,
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
    dba_aka_text String,
    composed_of_statement String,
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
ORDER BY (candidate_key, serial_number, owner_key);

INSERT INTO markorbit_facts.schema_version (component, version)
SELECT 'US_OWNER_READ', 'US_OWNER_READ_V1'
WHERE NOT EXISTS
(
    SELECT 1
    FROM markorbit_facts.schema_version FINAL
    WHERE component = 'US_OWNER_READ' AND version = 'US_OWNER_READ_V1'
);
