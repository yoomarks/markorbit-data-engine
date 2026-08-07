CREATE DATABASE IF NOT EXISTS markorbit_facts;

CREATE TABLE IF NOT EXISTS markorbit_facts.schema_version
(
    component LowCardinality(String),
    version String,
    applied_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(applied_at)
ORDER BY component;

INSERT INTO markorbit_facts.schema_version (component, version)
SELECT 'CN_CORE', 'M1.5'
WHERE NOT EXISTS
(
    SELECT 1
    FROM markorbit_facts.schema_version FINAL
    WHERE component = 'CN_CORE' AND version = 'M1.5'
);

CREATE TABLE IF NOT EXISTS markorbit_facts.cn_case_current
(
    case_id UUID,
    jurisdiction LowCardinality(String) DEFAULT 'CN',
    application_number String,
    case_family_root String,
    suffix_path String,
    filing_route LowCardinality(String),
    number_family LowCardinality(String),
    international_registration_number String,
    is_derived_case UInt8,
    derivation_reason LowCardinality(String) DEFAULT 'UNKNOWN',

    mark_name_raw String,
    mark_name_norm String,
    filing_date Nullable(Date32),
    prelim_pub_date Nullable(Date32),
    prelim_pub_issue String,
    registration_pub_date Nullable(Date32),
    registration_pub_issue String,
    valid_from Nullable(Date32),
    valid_until Nullable(Date32),
    exclusive_period_raw String,

    classes Array(UInt8),
    mark_type_raw String,
    mark_form_raw String,
    design_description String,
    color_description String,
    exclusive_rights_disclaimer String,
    is_3d_mark UInt8,
    is_co_application UInt8,
    geo_indication_info String,
    color_mark_flag String,
    is_well_known_mark UInt8,
    agent_code String,
    data_quality_flags Array(String),

    source_package_kind LowCardinality(String),
    source_effective_date Nullable(Date32),
    source_file String,
    source_start_line UInt64,
    source_end_line UInt64,
    source_row_hash FixedString(64),
    last_source_package_id UUID,
    record_hash FixedString(64),
    source_rank UInt64,
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3),
    is_deleted UInt8 DEFAULT 0
)
ENGINE = ReplacingMergeTree(source_rank, is_deleted)
ORDER BY application_number;

CREATE TABLE IF NOT EXISTS markorbit_facts.cn_case_scope_current
(
    case_id UUID,
    application_number String,
    class_no UInt8,

    source_item_count UInt32,
    interpreted_active_item_count UInt32,
    interpreted_inactive_item_count UInt32,
    unmapped_status_item_count UInt32,
    effective_item_count Nullable(UInt32),
    interpretation_complete UInt8,
    scope_interpretation_status LowCardinality(String),
    goods_status_mapping_version String,
    observed_status_codes Array(String),

    goods_items_compact String,
    goods_text_search String,
    similar_groups Array(String),
    active_similar_groups Array(String),

    scope_hash FixedString(64),
    effective_scope_hash String,
    source_package_kind LowCardinality(String),
    source_effective_date Nullable(Date32),
    source_file String,
    source_start_line UInt64,
    source_end_line UInt64,
    source_row_hash FixedString(64),
    last_source_package_id UUID,
    source_rank UInt64,
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3),
    is_deleted UInt8 DEFAULT 0
)
ENGINE = ReplacingMergeTree(source_rank, is_deleted)
ORDER BY (application_number, class_no);

CREATE TABLE IF NOT EXISTS markorbit_facts.cn_case_party_current
(
    relation_id UUID,
    case_id UUID,
    application_number String,
    role LowCardinality(String),
    relation_key FixedString(64),
    mention_id UUID,
    entity_id Nullable(UUID),
    agent_code String,
    raw_name String,
    normalized_name String,
    raw_address String,
    normalized_address String,
    country_code String,
    region_code String,
    city String,
    class_nos Array(UInt8),
    confidence_score Float32,

    valid_from Nullable(Date32),
    valid_to Nullable(Date32),
    is_current UInt8,
    relation_status LowCardinality(String),
    replacement_mode LowCardinality(String),

    source_package_kind LowCardinality(String),
    source_effective_date Nullable(Date32),
    source_file String,
    source_start_line UInt64,
    source_end_line UInt64,
    source_row_hash FixedString(64),
    last_source_package_id UUID,
    record_hash FixedString(64),
    source_rank UInt64,
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3),
    is_deleted UInt8 DEFAULT 0
)
ENGINE = ReplacingMergeTree(source_rank, is_deleted)
ORDER BY (application_number, role, relation_key);

CREATE TABLE IF NOT EXISTS markorbit_facts.cn_case_party_relation_history
(
    history_id UUID,
    relation_id UUID,
    case_id UUID,
    application_number String,
    role LowCardinality(String),
    action LowCardinality(String),
    effective_date Nullable(Date32),
    relation_key FixedString(64),
    mention_id UUID,
    entity_id Nullable(UUID),
    raw_name String,
    raw_address String,
    source_package_id UUID,
    source_package_kind LowCardinality(String),
    source_file String,
    source_row UInt64,
    source_rank UInt64,
    history_hash FixedString(64),
    observed_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(source_rank)
ORDER BY history_hash;

CREATE TABLE IF NOT EXISTS markorbit_facts.cn_agent_current
(
    agent_code String,
    mention_id UUID,
    entity_id Nullable(UUID),
    agent_name String,
    agent_name_norm String,
    source_file String,
    source_start_line UInt64,
    source_row_hash FixedString(64),
    last_source_package_id UUID,
    source_rank UInt64,
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3),
    is_deleted UInt8 DEFAULT 0
)
ENGINE = ReplacingMergeTree(source_rank, is_deleted)
ORDER BY agent_code;

CREATE TABLE IF NOT EXISTS markorbit_facts.cn_priority_current
(
    application_number String,
    class_no UInt8,
    priority_number String,
    priority_type String,
    priority_date Nullable(Date32),
    priority_goods String,
    priority_country_region String,
    source_file String,
    source_start_line UInt64,
    source_row_hash FixedString(64),
    record_hash FixedString(64),
    last_source_package_id UUID,
    source_rank UInt64,
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3),
    is_deleted UInt8 DEFAULT 0
)
ENGINE = ReplacingMergeTree(source_rank, is_deleted)
ORDER BY (application_number, class_no, priority_number);

CREATE TABLE IF NOT EXISTS markorbit_facts.cn_madrid_current
(
    application_number String,
    international_registration_number String,
    international_registration_date Nullable(Date32),
    international_notification_date Nullable(Date32),
    application_language String,
    application_type String,
    international_pub_issue String,
    international_pub_date Nullable(Date32),
    subsequent_designation_date Nullable(Date32),
    basic_registration_date Nullable(Date32),
    source_file String,
    source_start_line UInt64,
    source_row_hash FixedString(64),
    record_hash FixedString(64),
    last_source_package_id UUID,
    source_rank UInt64,
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3),
    is_deleted UInt8 DEFAULT 0
)
ENGINE = ReplacingMergeTree(source_rank, is_deleted)
ORDER BY (application_number, international_registration_number);

CREATE TABLE IF NOT EXISTS markorbit_facts.cn_observed_event
(
    event_id UUID,
    case_id UUID,
    application_number String,
    event_type LowCardinality(String),
    event_date Nullable(Date32),
    observed_at DateTime64(3, 'UTC') DEFAULT now64(3),
    affected_scope LowCardinality(String),
    class_no Nullable(UInt8),
    field_name LowCardinality(String),
    old_value_compact String,
    new_value_compact String,
    evidence_level LowCardinality(String),
    legal_effect LowCardinality(String),
    confidence_score Float32,
    source_package_id UUID,
    source_package_kind LowCardinality(String),
    source_file String,
    source_row UInt64,
    source_rank UInt64,
    event_hash FixedString(64)
)
ENGINE = ReplacingMergeTree(source_rank)
ORDER BY event_hash;

CREATE TABLE IF NOT EXISTS markorbit_facts.cn_case_relation_current
(
    relation_id UUID,
    source_case_id UUID,
    target_case_id UUID,
    source_application_number String,
    target_application_number String,
    relation_type LowCardinality(String),
    derivation_reason LowCardinality(String),
    filing_route LowCardinality(String),
    international_registration_number String,
    confidence_score Float32,
    evidence_status LowCardinality(String),
    source_package_id UUID,
    source_package_kind LowCardinality(String),
    source_file String,
    source_row UInt64,
    record_hash FixedString(64),
    source_rank UInt64,
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3),
    is_deleted UInt8 DEFAULT 0
)
ENGINE = ReplacingMergeTree(source_rank, is_deleted)
ORDER BY (source_application_number, target_application_number, relation_type);

CREATE TABLE IF NOT EXISTS markorbit_facts.cn_scope_carve_out_current
(
    carve_out_id UUID,
    relation_id UUID,
    source_application_number String,
    target_application_number String,
    class_no UInt8,
    carve_out_type LowCardinality(String),
    source_scope_hash String,
    target_scope_hash String,
    evidence_status LowCardinality(String),
    confidence_score Float32,
    source_package_id UUID,
    source_file String,
    source_row UInt64,
    record_hash FixedString(64),
    source_rank UInt64,
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3),
    is_deleted UInt8 DEFAULT 0
)
ENGINE = ReplacingMergeTree(source_rank, is_deleted)
ORDER BY (source_application_number, target_application_number, class_no);
