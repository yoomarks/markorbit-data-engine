CREATE TABLE IF NOT EXISTS markorbit_facts.cn_entity_trademark_relationship_event
(
    entity_id UUID,
    role LowCardinality(String),
    application_number String,
    relation_key FixedString(64),
    action LowCardinality(String),
    event_date Nullable(Date32),
    observed_at DateTime64(3, 'UTC'),
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
ORDER BY (entity_id, role, application_number, relation_key, event_hash);

CREATE MATERIALIZED VIEW IF NOT EXISTS markorbit_facts.cn_entity_trademark_relationship_event_mv
TO markorbit_facts.cn_entity_trademark_relationship_event
AS
WITH
    position(event_type, 'SUPERSEDED') > 0 AS is_superseded,
    if(is_superseded, old_value_compact, new_value_compact) AS fact_json,
    JSONExtractString(fact_json, 'entity_id') AS entity_id_text,
    JSONExtractString(fact_json, 'relation_key') AS relation_key_text
SELECT
    toUUID(entity_id_text) AS entity_id,
    multiIf(
        startsWith(event_type, 'CO_OWNER_'), 'CO_OWNER',
        startsWith(event_type, 'OWNER_'), 'OWNER',
        'AGENT'
    ) AS role,
    application_number,
    relation_key_text AS relation_key,
    if(is_superseded, 'SUPERSEDED', 'OBSERVED_CURRENT') AS action,
    event_date,
    observed_at,
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
)
  AND entity_id_text != ''
  AND relation_key_text != '';

CREATE TABLE IF NOT EXISTS markorbit_facts.cn_entity_trademark_portfolio_readiness
(
    ready_version String,
    source_watermark String,
    source_max_rank UInt64,
    implementation_sha FixedString(40),
    accepted_at DateTime64(3, 'UTC'),
    acceptance_hash FixedString(64)
)
ENGINE = ReplacingMergeTree(accepted_at)
ORDER BY ready_version;

INSERT INTO markorbit_facts.schema_version (component, version)
SELECT 'CN_ENTITY_TRADEMARK_PORTFOLIO', 'CN_ENTITY_TRADEMARK_PORTFOLIO_SCHEMA_V1'
WHERE NOT EXISTS
(
    SELECT 1
    FROM markorbit_facts.schema_version FINAL
    WHERE component = 'CN_ENTITY_TRADEMARK_PORTFOLIO'
      AND version = 'CN_ENTITY_TRADEMARK_PORTFOLIO_SCHEMA_V1'
);
