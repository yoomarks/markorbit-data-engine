CREATE TABLE IF NOT EXISTS markorbit_facts.us_natural_lapse_discovery_current
(
    jurisdiction LowCardinality(String),
    lapse_reason LowCardinality(String),
    lapse_event_date Date32,
    nice_class UInt8,
    lapse_event_key FixedString(64),
    serial_number String,
    registration_number String,
    mark_identification String,
    mark_drawing_code String,
    owner_names Array(String),
    observed_status_code String,
    observed_status_date Nullable(Date32),
    cancellation_date Nullable(Date32),
    renewal_date Nullable(Date32),
    event_code LowCardinality(String),
    event_type_code String,
    description_text String,
    event_source_package_kind LowCardinality(String),
    event_source_effective_date Nullable(Date32),
    event_source_file String,
    event_source_row_hash FixedString(64),
    event_source_package_id UUID,
    event_source_rank UInt64,
    event_observed_at DateTime64(3, 'UTC'),
    case_source_package_kind LowCardinality(String),
    case_source_effective_date Nullable(Date32),
    case_source_file String,
    case_source_row_hash FixedString(64),
    case_source_package_id UUID,
    case_record_hash FixedString(64),
    case_source_rank UInt64,
    class_lineage_json String,
    owner_lineage_json String,
    source_manifest_fingerprint FixedString(64),
    projection_snapshot_id FixedString(64),
    legal_conclusion UInt8 DEFAULT 0,
    projection_rank UInt64,
    built_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(projection_rank)
ORDER BY (
    lapse_reason,
    lapse_event_date,
    nice_class,
    lapse_event_key,
    serial_number,
    projection_snapshot_id
)
SETTINGS storage_policy = 'hot_us_only';

INSERT INTO markorbit_facts.schema_version (component, version)
SELECT 'US_NATURAL_LAPSE_DISCOVERY', 'US_NATURAL_LAPSE_DISCOVERY_V1'
WHERE NOT EXISTS
(
    SELECT 1 FROM markorbit_facts.schema_version FINAL
    WHERE component = 'US_NATURAL_LAPSE_DISCOVERY'
      AND version = 'US_NATURAL_LAPSE_DISCOVERY_V1'
);