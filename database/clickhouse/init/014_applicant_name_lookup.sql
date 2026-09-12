CREATE TABLE IF NOT EXISTS markorbit_facts.cn_applicant_name_lookup_current
(
    normalized_name String,
    entity_id UUID,
    application_number String,
    relation_key FixedString(64),
    source_row_hash FixedString(64),
    record_hash FixedString(64),
    source_rank UInt64,
    ingested_at DateTime64(3, 'UTC'),
    is_deleted UInt8 DEFAULT 0
)
ENGINE = ReplacingMergeTree(source_rank, is_deleted)
ORDER BY (normalized_name, entity_id, application_number, relation_key);

CREATE TABLE IF NOT EXISTS markorbit_facts.us_applicant_name_lookup_current
(
    normalized_name String,
    candidate_key FixedString(64),
    serial_number String,
    owner_key FixedString(64),
    source_row_hash FixedString(64),
    record_hash FixedString(64),
    source_rank UInt64,
    ingested_at DateTime64(3, 'UTC'),
    is_deleted UInt8 DEFAULT 0
)
ENGINE = ReplacingMergeTree(source_rank, is_deleted)
ORDER BY (normalized_name, candidate_key, serial_number, owner_key);

INSERT INTO markorbit_facts.schema_version (component, version)
SELECT 'APPLICANT_NAME_LOOKUP', 'APPLICANT_NAME_LOOKUP_V1'
WHERE NOT EXISTS
(
    SELECT 1
    FROM markorbit_facts.schema_version FINAL
    WHERE component = 'APPLICANT_NAME_LOOKUP'
      AND version = 'APPLICANT_NAME_LOOKUP_V1'
);
