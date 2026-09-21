CREATE TABLE IF NOT EXISTS markorbit_facts.us_ttab_correspondent_mark_history
(
    observation_key FixedString(64),
    relationship_key FixedString(64),
    normalized_name String,
    correspondent_name String,
    correspondent_organization String,
    proceeding_number String,
    party_side LowCardinality(String),
    party_ordinal UInt16,
    party_name String,
    party_role String,
    party_key FixedString(64),
    property_key FixedString(64),
    mark_identity String,
    serial_number String,
    registration_number String,
    mark_text String,
    source_kind LowCardinality(String),
    source_snapshot_at DateTime64(3, 'UTC'),
    source_file String,
    source_package_id UUID,
    source_rank UInt64,
    serving_generation UInt64,
    observed_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(source_rank)
ORDER BY (normalized_name, serving_generation, observation_key)
SETTINGS storage_policy = 'hot_us_only';

CREATE TABLE IF NOT EXISTS markorbit_facts.us_ttab_correspondent_mark_history_readiness
(
    ready_version LowCardinality(String),
    accepted_source_max_rank UInt64,
    accepted_serving_generation UInt64,
    source_joined_rows UInt64,
    target_rows UInt64,
    target_relationship_count UInt64,
    implementation_sha FixedString(40),
    accepted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(accepted_at)
ORDER BY ready_version
SETTINGS storage_policy = 'hot_us_only';

CREATE TABLE IF NOT EXISTS markorbit_facts.us_ttab_correspondent_mark_history_watermark
(
    ready_version LowCardinality(String),
    serving_generation UInt64,
    source_max_rank UInt64,
    source_package_id UUID,
    updated_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (ready_version, serving_generation)
SETTINGS storage_policy = 'hot_us_only';

INSERT INTO markorbit_facts.schema_version (component, version)
SELECT 'US_TTAB_CORRESPONDENT_MARK_HISTORY_SCHEMA',
       'US_TTAB_CORRESPONDENT_MARK_HISTORY_SCHEMA_V1'
WHERE NOT EXISTS
(
    SELECT 1
    FROM markorbit_facts.schema_version FINAL
    WHERE component = 'US_TTAB_CORRESPONDENT_MARK_HISTORY_SCHEMA'
      AND version = 'US_TTAB_CORRESPONDENT_MARK_HISTORY_SCHEMA_V1'
);
