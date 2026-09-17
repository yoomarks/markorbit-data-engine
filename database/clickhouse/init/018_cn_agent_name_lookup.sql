CREATE TABLE IF NOT EXISTS markorbit_facts.cn_agent_name_candidate_lookup
(
    normalized_name String,
    agent_code String,
    mention_id UUID,
    entity_id Nullable(UUID),
    agent_name String,
    source_file String,
    source_first_line UInt64,
    source_last_line UInt64,
    source_row_hash FixedString(64),
    source_package_id UUID,
    source_rank UInt64,
    ingested_at DateTime64(3, 'UTC'),
    is_deleted UInt8 DEFAULT 0
)
ENGINE = ReplacingMergeTree(source_rank, is_deleted)
ORDER BY (normalized_name, agent_code);

CREATE MATERIALIZED VIEW IF NOT EXISTS markorbit_facts.cn_agent_name_candidates_from_agent_mv
TO markorbit_facts.cn_agent_name_candidate_lookup
AS
SELECT
    agent_name_norm AS normalized_name,
    agent_code,
    mention_id,
    entity_id,
    agent_name,
    source_file,
    source_first_line,
    source_last_line,
    source_row_hash,
    last_source_package_id AS source_package_id,
    source_rank,
    ingested_at,
    is_deleted
FROM markorbit_facts.cn_agent_current
WHERE agent_name_norm != '';

INSERT INTO markorbit_facts.schema_version (component, version)
SELECT 'CN_AGENT_NAME_LOOKUP', 'CN_AGENT_NAME_LOOKUP_SCHEMA_V1'
WHERE NOT EXISTS
(
    SELECT 1 FROM markorbit_facts.schema_version FINAL
    WHERE component = 'CN_AGENT_NAME_LOOKUP'
);
