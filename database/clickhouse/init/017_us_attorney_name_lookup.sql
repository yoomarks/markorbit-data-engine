CREATE TABLE IF NOT EXISTS markorbit_facts.us_attorney_name_candidate_lookup
(
    normalized_name String,
    serial_number String,
    correspondent_key FixedString(64),
    attorney_name String,
    attorney_docket_number String,
    source_package_kind LowCardinality(String),
    source_effective_date Nullable(Date32),
    source_file String,
    source_row_hash FixedString(64),
    source_package_id UUID,
    record_hash FixedString(64),
    source_rank UInt64,
    ingested_at DateTime64(3, 'UTC'),
    is_deleted UInt8 DEFAULT 0
)
ENGINE = ReplacingMergeTree(source_rank, is_deleted)
ORDER BY (normalized_name, serial_number, correspondent_key);

CREATE MATERIALIZED VIEW IF NOT EXISTS markorbit_facts.us_attorney_name_candidates_from_correspondent_mv
TO markorbit_facts.us_attorney_name_candidate_lookup
AS
SELECT
    lowerUTF8(replaceRegexpAll(trimBoth(attorney_name), '\\s+', ' ')) AS normalized_name,
    serial_number,
    correspondent_key,
    attorney_name,
    attorney_docket_number,
    source_package_kind,
    source_effective_date,
    source_file,
    source_row_hash,
    last_source_package_id AS source_package_id,
    record_hash,
    source_rank,
    ingested_at,
    is_deleted
FROM markorbit_facts.us_correspondent_current
WHERE attorney_name != '';

INSERT INTO markorbit_facts.schema_version (component, version)
SELECT 'US_ATTORNEY_NAME_LOOKUP', 'US_ATTORNEY_NAME_LOOKUP_SCHEMA_V1'
WHERE NOT EXISTS
(
    SELECT 1 FROM markorbit_facts.schema_version FINAL
    WHERE component = 'US_ATTORNEY_NAME_LOOKUP'
);
