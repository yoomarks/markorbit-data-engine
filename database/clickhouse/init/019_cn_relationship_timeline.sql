CREATE TABLE IF NOT EXISTS markorbit_facts.cn_trademark_relationship_event
(
    event_id UUID,
    application_number String,
    event_type LowCardinality(String),
    event_date Nullable(Date32),
    observed_at DateTime64(3, 'UTC'),
    field_name LowCardinality(String),
    old_value_compact String,
    new_value_compact String,
    evidence_level LowCardinality(String),
    source_package_id UUID,
    source_package_kind LowCardinality(String),
    source_file String,
    source_first_line UInt64,
    source_last_line UInt64,
    source_row_hash FixedString(64),
    source_rank UInt64,
    event_hash FixedString(64)
)
ENGINE = ReplacingMergeTree(source_rank)
ORDER BY (application_number, event_hash);

CREATE MATERIALIZED VIEW IF NOT EXISTS markorbit_facts.cn_trademark_relationship_event_mv
TO markorbit_facts.cn_trademark_relationship_event
AS
SELECT
    event_id,
    application_number,
    event_type,
    event_date,
    observed_at,
    field_name,
    old_value_compact,
    new_value_compact,
    evidence_level,
    source_package_id,
    source_package_kind,
    source_file,
    source_first_line,
    source_last_line,
    source_row_hash,
    source_rank,
    event_hash
FROM markorbit_facts.cn_observed_event
WHERE event_type IN
(
    'OWNER_RELATION_OBSERVED',
    'OWNER_RELATION_SUPERSEDED_OBSERVED',
    'CO_OWNER_RELATION_OBSERVED',
    'CO_OWNER_RELATION_SUPERSEDED_OBSERVED',
    'AGENT_RELATION_OBSERVED',
    'AGENT_RELATION_SUPERSEDED_OBSERVED'
);

INSERT INTO markorbit_facts.schema_version (component, version)
SELECT 'CN_RELATIONSHIP_TIMELINE', 'CN_RELATIONSHIP_TIMELINE_SCHEMA_V1'
WHERE NOT EXISTS
(
    SELECT 1 FROM markorbit_facts.schema_version FINAL
    WHERE component = 'CN_RELATIONSHIP_TIMELINE'
);
