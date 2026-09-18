CREATE TABLE IF NOT EXISTS markorbit_facts.cn_admitted_citation_relation
(
    edge_id String,
    candidate_id String,
    candidate_fingerprint_sha256 FixedString(64),
    contract_version LowCardinality(String),
    relationship_type LowCardinality(String),
    source_resource_id String,
    target_resource_id String,
    event_date Nullable(Date32),
    evidence_json String,
    provenance_json String,
    edge_fingerprint String,
    admitted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(admitted_at)
ORDER BY candidate_fingerprint_sha256;

INSERT INTO markorbit_facts.schema_version (component, version)
SELECT 'CN_CITATION_RELATION_ADMISSION', 'CN_CITATION_RELATION_ADMISSION_V1'
WHERE NOT EXISTS
(
    SELECT 1 FROM markorbit_facts.schema_version FINAL
    WHERE component = 'CN_CITATION_RELATION_ADMISSION'
);
