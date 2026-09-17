CREATE TABLE IF NOT EXISTS markorbit_facts.us_event_serial_history
(
    event_key FixedString(64),
    serial_number String,
    event_code String,
    event_date Nullable(Date32),
    event_sequence UInt32,
    event_type_code String,
    description_text String,
    source_package_kind LowCardinality(String),
    source_effective_date Nullable(Date32),
    source_file String,
    source_row_hash FixedString(64),
    source_package_id UUID,
    source_rank UInt64,
    observed_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(source_rank)
ORDER BY (serial_number, event_key);

INSERT INTO markorbit_facts.schema_version (component, version)
SELECT 'US_EVENT_SERIAL_LOOKUP', 'US_EVENT_SERIAL_LOOKUP_SCHEMA_V1'
WHERE NOT EXISTS
(
    SELECT 1 FROM markorbit_facts.schema_version FINAL
    WHERE component = 'US_EVENT_SERIAL_LOOKUP'
);
