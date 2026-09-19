CREATE TABLE IF NOT EXISTS markorbit_facts.cn_trademark_gazette_admission_chunk
(
    announcement_issue UInt32,
    announcement_date Nullable(Date32),
    source_dataset_sha256 FixedString(64),
    range_start_page UInt32,
    range_end_page UInt32,
    source_record_count UInt64,
    source_page_count UInt64,
    page_size UInt32,
    chunk_row_count UInt64,
    query_scope LowCardinality(String),
    source_capture_schema LowCardinality(String),
    source_uri String,
    collected_at DateTime64(3, 'UTC'),
    chunk_fingerprint FixedString(64)
)
ENGINE = ReplacingMergeTree(collected_at)
ORDER BY
(
    announcement_issue,
    source_dataset_sha256,
    range_start_page,
    range_end_page
);

INSERT INTO markorbit_facts.schema_version (component, version)
SELECT 'CN_TRADEMARK_GAZETTE', 'CN_TRADEMARK_GAZETTE_SCHEMA_V2'
WHERE NOT EXISTS
(
    SELECT 1
    FROM markorbit_facts.schema_version FINAL
    WHERE component = 'CN_TRADEMARK_GAZETTE'
      AND version = 'CN_TRADEMARK_GAZETTE_SCHEMA_V2'
);
