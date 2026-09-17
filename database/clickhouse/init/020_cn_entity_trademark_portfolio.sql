CREATE TABLE IF NOT EXISTS markorbit_facts.cn_entity_trademark_portfolio
(
    entity_id UUID,
    role LowCardinality(String),
    application_number String,
    has_current UInt8,
    has_former UInt8,
    relation_count UInt16,
    first_observed_at DateTime64(3, 'UTC'),
    last_observed_at DateTime64(3, 'UTC'),
    latest_source_rank UInt64,
    latest_source_package_id UUID,
    latest_event_hash FixedString(64)
)
ENGINE = ReplacingMergeTree(latest_source_rank)
ORDER BY (entity_id, role, application_number);

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
