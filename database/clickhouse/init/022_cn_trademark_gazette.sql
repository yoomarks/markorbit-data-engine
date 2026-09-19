CREATE TABLE IF NOT EXISTS markorbit_facts.cn_trademark_gazette_issue
(
    announcement_issue UInt32,
    announcement_date Nullable(Date32),
    record_count UInt64,
    page_count UInt64,
    capture_page_size UInt32,
    capture_completeness LowCardinality(String),
    source_capture_schema LowCardinality(String),
    source_dataset_sha256 FixedString(64),
    source_uri String,
    collected_at DateTime64(3, 'UTC'),
    observation_fingerprint FixedString(64)
)
ENGINE = ReplacingMergeTree(collected_at)
ORDER BY (announcement_issue, observation_fingerprint);

CREATE TABLE IF NOT EXISTS markorbit_facts.cn_trademark_gazette_announcement
(
    source_row_id String,
    source_search_id String,
    announcement_issue UInt32,
    registration_number String,
    announcement_type_code String,
    announcement_type_name String,
    detail_page_no Nullable(UInt32),
    announcement_page_count Nullable(UInt32),
    detail_file_id String,
    detail_asset_path String,
    announcement_detail_url String,
    detail_resolution_status LowCardinality(String),
    source_row_fingerprint FixedString(64),
    source_dataset_sha256 FixedString(64),
    source_uri String,
    collected_at DateTime64(3, 'UTC'),
    observation_fingerprint FixedString(64)
)
ENGINE = ReplacingMergeTree(collected_at)
ORDER BY (announcement_issue, source_row_id);

INSERT INTO markorbit_facts.schema_version (component, version)
SELECT 'CN_TRADEMARK_GAZETTE', 'CN_TRADEMARK_GAZETTE_SCHEMA_V1'
WHERE NOT EXISTS
(
    SELECT 1
    FROM markorbit_facts.schema_version FINAL
    WHERE component = 'CN_TRADEMARK_GAZETTE'
      AND version = 'CN_TRADEMARK_GAZETTE_SCHEMA_V1'
);
