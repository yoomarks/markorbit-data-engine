CREATE TABLE IF NOT EXISTS markorbit_facts.cn_stage_basic
(
    package_id UUID,
    case_id UUID,
    family_root_case_id UUID,
    application_number String,
    case_family_root String,
    suffix_path String,
    filing_route LowCardinality(String),
    number_family LowCardinality(String),
    international_registration_number String,
    is_derived_case UInt8,
    relation_id UUID,
    class_no UInt8,

    filing_date Nullable(Date32),
    filing_date_raw String,
    mark_name_raw String,
    mark_type_raw String,
    agent_code String,
    agent_relation_id UUID,
    agent_relation_key FixedString(64),
    agent_mention_id UUID,
    agent_entity_id Nullable(UUID),
    prelim_pub_issue String,
    prelim_pub_date Nullable(Date32),
    prelim_pub_date_raw String,
    registration_pub_issue String,
    registration_pub_date Nullable(Date32),
    registration_pub_date_raw String,
    exclusive_start_date Nullable(Date32),
    exclusive_start_date_raw String,
    exclusive_end_date Nullable(Date32),
    exclusive_end_date_raw String,
    exclusive_period String,
    design_description String,
    color_description String,
    exclusive_rights_disclaimer String,
    is_3d_mark UInt8,
    is_co_application UInt8,
    mark_form_raw String,
    geo_indication_info String,
    color_mark_flag String,
    is_well_known_mark UInt8,
    date_quality_flags Array(String),

    source_file String,
    source_start_line UInt64,
    source_end_line UInt64,
    row_hash FixedString(64),
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (package_id, application_number, class_no, source_start_line)
TTL toDateTime(ingested_at) + INTERVAL 7 DAY DELETE;

CREATE TABLE IF NOT EXISTS markorbit_facts.cn_stage_applicant
(
    package_id UUID,
    case_id UUID,
    application_number String,
    class_no UInt8,
    relation_id UUID,
    relation_key FixedString(64),
    mention_id UUID,
    entity_id Nullable(UUID),
    raw_name String,
    normalized_name String,
    raw_address String,
    normalized_address String,
    country_code String,
    region_code String,
    city String,
    geo_confidence Float32,
    source_file String,
    source_start_line UInt64,
    source_end_line UInt64,
    row_hash FixedString(64),
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (package_id, application_number, class_no, relation_key)
TTL toDateTime(ingested_at) + INTERVAL 7 DAY DELETE;

CREATE TABLE IF NOT EXISTS markorbit_facts.cn_stage_goods
(
    package_id UUID,
    case_id UUID,
    application_number String,
    class_no UInt8,
    similar_group String,
    goods_sequence String,
    goods_name String,
    goods_status_raw String,
    goods_status_bucket LowCardinality(String),
    goods_status_reason LowCardinality(String),
    goods_status_mapping_version String,
    source_file String,
    source_start_line UInt64,
    source_end_line UInt64,
    row_hash FixedString(64),
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (package_id, application_number, class_no, goods_sequence, source_start_line)
TTL toDateTime(ingested_at) + INTERVAL 7 DAY DELETE;

CREATE TABLE IF NOT EXISTS markorbit_facts.cn_stage_agent
(
    package_id UUID,
    relation_id UUID,
    mention_id UUID,
    entity_id Nullable(UUID),
    agent_code String,
    agent_name String,
    agent_name_norm String,
    source_file String,
    source_start_line UInt64,
    source_end_line UInt64,
    row_hash FixedString(64),
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (package_id, agent_code)
TTL toDateTime(ingested_at) + INTERVAL 7 DAY DELETE;

CREATE TABLE IF NOT EXISTS markorbit_facts.cn_stage_priority
(
    package_id UUID,
    application_number String,
    class_no UInt8,
    priority_number String,
    priority_type String,
    priority_date Nullable(Date32),
    priority_goods String,
    priority_country_region String,
    source_file String,
    source_start_line UInt64,
    source_end_line UInt64,
    row_hash FixedString(64),
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (package_id, application_number, class_no, priority_number)
TTL toDateTime(ingested_at) + INTERVAL 7 DAY DELETE;

CREATE TABLE IF NOT EXISTS markorbit_facts.cn_stage_madrid
(
    package_id UUID,
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
    source_end_line UInt64,
    row_hash FixedString(64),
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (package_id, application_number, international_registration_number)
TTL toDateTime(ingested_at) + INTERVAL 7 DAY DELETE;

CREATE TABLE IF NOT EXISTS markorbit_facts.cn_stage_coowner
(
    package_id UUID,
    case_id UUID,
    application_number String,
    relation_id UUID,
    relation_key FixedString(64),
    mention_id UUID,
    entity_id Nullable(UUID),
    raw_name String,
    normalized_name String,
    raw_address String,
    normalized_address String,
    country_code String,
    region_code String,
    city String,
    geo_confidence Float32,
    source_file String,
    source_start_line UInt64,
    source_end_line UInt64,
    row_hash FixedString(64),
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (package_id, application_number, relation_key)
TTL toDateTime(ingested_at) + INTERVAL 7 DAY DELETE;
