CREATE DATABASE IF NOT EXISTS markorbit_facts;

CREATE TABLE IF NOT EXISTS markorbit_facts.schema_version
(
    component LowCardinality(String),
    version String,
    applied_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(applied_at)
ORDER BY component;
