CREATE TABLE IF NOT EXISTS markorbit_facts.cn_goods_item_current
(
    case_id UUID,
    application_number String,
    class_no UInt8,
    goods_item_key FixedString(64),

    goods_sequence String,
    goods_name String,
    goods_name_norm String,
    similar_group String,

    goods_status_raw String,
    goods_status_bucket LowCardinality(String),
    goods_status_reason LowCardinality(String),
    goods_status_semantic LowCardinality(String),
    goods_status_source_finality LowCardinality(String),
    operational_effect LowCardinality(String),
    goods_status_mapping_version String,
    evidence_label LowCardinality(String),

    first_source_package_id UUID,
    first_source_package_kind LowCardinality(String),
    first_source_rank UInt64,

    source_package_kind LowCardinality(String),
    source_effective_date Nullable(Date32),
    source_file String,
    source_first_line UInt64,
    source_last_line UInt64,
    source_row_hash FixedString(64),
    last_source_package_id UUID,
    record_hash FixedString(64),
    source_rank UInt64,
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3),
    is_deleted UInt8 DEFAULT 0
)
ENGINE = ReplacingMergeTree(source_rank, is_deleted)
ORDER BY (application_number, class_no, goods_item_key);


CREATE TABLE IF NOT EXISTS markorbit_facts.cn_goods_item_observation
(
    observation_id UUID,
    case_id UUID,
    application_number String,
    class_no UInt8,
    goods_item_key FixedString(64),
    goods_sequence String,
    goods_name String,
    similar_group String,

    previous_status_raw String,
    previous_status_semantic LowCardinality(String),
    previous_operational_effect LowCardinality(String),
    new_status_raw String,
    new_status_semantic LowCardinality(String),
    new_status_source_finality LowCardinality(String),
    new_operational_effect LowCardinality(String),
    transition_type LowCardinality(String),

    evidence_label LowCardinality(String),
    source_package_id UUID,
    source_package_kind LowCardinality(String),
    source_effective_date Nullable(Date32),
    source_file String,
    source_first_line UInt64,
    source_last_line UInt64,
    source_row_hash FixedString(64),
    source_rank UInt64,
    observation_hash FixedString(64),
    observed_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(source_rank)
ORDER BY observation_hash;


CREATE TABLE IF NOT EXISTS markorbit_facts.cn_goods_scope_lifecycle_current
(
    case_id UUID,
    application_number String,
    class_no UInt8,

    known_item_count UInt32,
    operational_effective_item_count UInt32,
    risk_item_count UInt32,
    inactive_high_confidence_item_count UInt32,
    final_inactive_item_count UInt32,
    unknown_item_count UInt32,

    code_0_item_count UInt32,
    code_1_item_count UInt32,
    code_2_item_count UInt32,

    some_goods_inactive UInt8,
    all_known_goods_inactive UInt8,
    some_goods_final_inactive UInt8,
    all_known_goods_final_inactive UInt8,
    goods_risk_signal_present UInt8,

    goods_status_mapping_version String,
    evidence_label LowCardinality(String),
    source_package_kind LowCardinality(String),
    source_effective_date Nullable(Date32),
    last_source_package_id UUID,
    source_rank UInt64,
    updated_at DateTime64(3, 'UTC') DEFAULT now64(3),
    is_deleted UInt8 DEFAULT 0
)
ENGINE = ReplacingMergeTree(source_rank, is_deleted)
ORDER BY (application_number, class_no);


INSERT INTO markorbit_facts.schema_version (component, version)
SELECT 'CN_GOODS', 'M1.6'
WHERE NOT EXISTS
(
    SELECT 1
    FROM markorbit_facts.schema_version FINAL
    WHERE component = 'CN_GOODS' AND version = 'M1.6'
);
