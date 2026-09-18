CREATE TABLE IF NOT EXISTS markorbit_facts.cn_entity_trademark_relationship_history
(
    entity_id UUID,
    role LowCardinality(String),
    application_number String,
    relation_key FixedString(64),
    action LowCardinality(String),
    effective_date Nullable(Date32),
    observed_at DateTime64(3, 'UTC'),
    source_package_id UUID,
    source_package_kind LowCardinality(String),
    source_file String,
    source_first_line UInt64,
    source_last_line UInt64,
    source_row_hash FixedString(64),
    source_rank UInt64,
    history_hash FixedString(64)
)
ENGINE = ReplacingMergeTree(source_rank)
ORDER BY (entity_id, role, application_number, relation_key, history_hash);

CREATE MATERIALIZED VIEW IF NOT EXISTS markorbit_facts.cn_entity_trademark_relationship_history_mv
TO markorbit_facts.cn_entity_trademark_relationship_history
AS
SELECT
    assumeNotNull(entity_id) AS entity_id,
    role,
    application_number,
    relation_key,
    action,
    effective_date,
    observed_at,
    source_package_id,
    source_package_kind,
    source_file,
    source_first_line,
    source_last_line,
    source_row_hash,
    source_rank,
    history_hash
FROM markorbit_facts.cn_case_party_relation_history
WHERE entity_id IS NOT NULL
  AND role IN ('OWNER', 'CO_OWNER', 'AGENT')
  AND action IN ('OBSERVED_CURRENT', 'SUPERSEDED');

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
