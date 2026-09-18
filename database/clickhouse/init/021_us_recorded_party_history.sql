CREATE TABLE IF NOT EXISTS markorbit_facts.us_recorded_party_relationship_event
(
    normalized_name String,
    source_domain LowCardinality(String),
    relationship_type LowCardinality(String),
    party_key FixedString(64),
    party_name String,
    party_side LowCardinality(String),
    source_role String,
    serial_number String,
    registration_number String,
    resource_type LowCardinality(String),
    resource_id String,
    event_date Nullable(Date32),
    observed_at DateTime64(3, 'UTC'),
    source_file String,
    source_package_id UUID,
    source_rank UInt64,
    party_observation_key FixedString(64),
    resource_observation_key FixedString(64),
    relationship_observation_hash FixedString(64)
)
ENGINE = ReplacingMergeTree(source_rank)
ORDER BY
(
    normalized_name,
    source_domain,
    relationship_type,
    serial_number,
    resource_id,
    party_key,
    relationship_observation_hash
);

CREATE MATERIALIZED VIEW IF NOT EXISTS markorbit_facts.us_assignment_recorded_party_relationship_mv
TO markorbit_facts.us_recorded_party_relationship_event
AS
SELECT
    lowerUTF8(replaceRegexpAll(trimBoth(p.party_name), '\\s+', ' ')) AS normalized_name,
    'US_ASSIGNMENT' AS source_domain,
    p.relationship_type,
    p.party_key,
    p.party_name,
    '' AS party_side,
    '' AS source_role,
    prop.serial_number,
    prop.registration_number,
    'ASSIGNMENT' AS resource_type,
    prop.reel_frame_id AS resource_id,
    rec.recorded_date AS event_date,
    greatest(p.observed_at, prop.observed_at, rec.observed_at) AS observed_at,
    prop.source_file,
    prop.source_package_id,
    greatest(p.source_rank, prop.source_rank, rec.source_rank) AS source_rank,
    p.observation_key AS party_observation_key,
    prop.observation_key AS resource_observation_key,
    hex(SHA256(concat(
        'US_ASSIGNMENT|',
        toString(prop.source_package_id), '|',
        toString(p.party_key), '|',
        p.relationship_type, '|',
        prop.serial_number, '|',
        prop.reel_frame_id
    ))) AS relationship_observation_hash
FROM markorbit_facts.us_assignment_property_history AS prop
INNER JOIN
(
    SELECT
        observation_key, party_key, reel_frame_id, party_name,
        source_package_id, source_rank, observed_at,
        'ASSIGNOR' AS relationship_type
    FROM markorbit_facts.us_assignment_assignor_history
    UNION ALL
    SELECT
        observation_key, party_key, reel_frame_id, party_name,
        source_package_id, source_rank, observed_at,
        'ASSIGNEE' AS relationship_type
    FROM markorbit_facts.us_assignment_assignee_history
) AS p
    ON p.reel_frame_id = prop.reel_frame_id
   AND p.source_package_id = prop.source_package_id
INNER JOIN markorbit_facts.us_assignment_record_history AS rec
    ON rec.reel_frame_id = prop.reel_frame_id
   AND rec.source_package_id = prop.source_package_id
WHERE normalized_name != '';

CREATE MATERIALIZED VIEW IF NOT EXISTS markorbit_facts.us_ttab_recorded_party_relationship_mv
TO markorbit_facts.us_recorded_party_relationship_event
AS
SELECT
    lowerUTF8(replaceRegexpAll(trimBoth(party.party_name), '\\s+', ' ')) AS normalized_name,
    'US_TTAB' AS source_domain,
    multiIf(
        proceeding.proceeding_type_code = 'OPP' AND party.side = 'PLAINTIFF',
            'OPPOSITION_PLAINTIFF',
        proceeding.proceeding_type_code = 'OPP' AND party.side = 'DEFENDANT',
            'OPPOSITION_DEFENDANT',
        proceeding.proceeding_type_code = 'CAN' AND party.side = 'PLAINTIFF',
            'CANCELLATION_PETITIONER',
        proceeding.proceeding_type_code = 'CAN' AND party.side = 'DEFENDANT',
            'CANCELLATION_RESPONDENT',
        proceeding.proceeding_type_code = 'EXA',
            'EX_PARTE_APPEAL_PARTY',
        'TTAB_PARTY'
    ) AS relationship_type,
    party.party_key,
    party.party_name,
    party.side AS party_side,
    party.role AS source_role,
    prop.serial_number,
    prop.registration_number,
    'PROCEEDING' AS resource_type,
    prop.proceeding_number AS resource_id,
    proceeding.filing_date AS event_date,
    greatest(party.observed_at, prop.observed_at, proceeding.observed_at) AS observed_at,
    prop.source_file,
    prop.source_package_id,
    greatest(party.source_rank, prop.source_rank, proceeding.source_rank) AS source_rank,
    party.observation_key AS party_observation_key,
    prop.observation_key AS resource_observation_key,
    hex(SHA256(concat(
        'US_TTAB|',
        toString(prop.source_package_id), '|',
        toString(party.party_key), '|',
        relationship_type, '|',
        prop.serial_number, '|',
        prop.proceeding_number
    ))) AS relationship_observation_hash
FROM markorbit_facts.us_ttab_property_history AS prop
INNER JOIN markorbit_facts.us_ttab_party_history AS party
    ON party.proceeding_number = prop.proceeding_number
   AND party.source_package_id = prop.source_package_id
   AND party.side = prop.party_side
   AND party.ordinal = prop.party_ordinal
INNER JOIN markorbit_facts.us_ttab_proceeding_history AS proceeding
    ON proceeding.proceeding_number = prop.proceeding_number
   AND proceeding.source_package_id = prop.source_package_id
WHERE normalized_name != '';

CREATE TABLE IF NOT EXISTS markorbit_facts.us_recorded_party_history_readiness
(
    ready_version String,
    assignment_max_rank UInt64,
    ttab_max_rank UInt64,
    implementation_sha FixedString(40),
    accepted_at DateTime64(3, 'UTC'),
    acceptance_hash FixedString(64)
)
ENGINE = ReplacingMergeTree(accepted_at)
ORDER BY ready_version;

INSERT INTO markorbit_facts.schema_version (component, version)
SELECT 'US_RECORDED_PARTY_HISTORY', 'US_RECORDED_PARTY_HISTORY_SCHEMA_V1'
WHERE NOT EXISTS
(
    SELECT 1
    FROM markorbit_facts.schema_version FINAL
    WHERE component = 'US_RECORDED_PARTY_HISTORY'
      AND version = 'US_RECORDED_PARTY_HISTORY_SCHEMA_V1'
);
