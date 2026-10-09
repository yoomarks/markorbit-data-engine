CREATE TABLE IF NOT EXISTS markorbit_facts.wipo_mgs_term_observation
(
    source_id LowCardinality(String),
    source_term_id String,
    nice_class UInt8,
    language LowCardinality(String),
    request_language LowCardinality(String),
    source_version Nullable(String),
    mapping_version String,
    source_response_sha256 FixedString(64),
    evidence_sha256 FixedString(64),
    evidence_canonical_uri String,
    source_uri String,
    observed_at DateTime64(3, 'UTC'),
    term_text String,
    seq_json String,
    src_json String,
    prf_json String,
    acc_raw_json String,
    rej_raw_json String,
    accepted_jurisdictions Array(String),
    rejected_jurisdictions Array(String),
    jurisdiction_statuses_json String,
    raw_payload_json String,
    content_sha256 FixedString(64),
    record_sha256 FixedString(64),
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY nice_class
ORDER BY (nice_class, language, source_term_id, observed_at,
          source_response_sha256, mapping_version)
SETTINGS storage_policy = 'hot_global_only';
