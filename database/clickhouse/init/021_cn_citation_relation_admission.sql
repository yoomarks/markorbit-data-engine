CREATE TABLE IF NOT EXISTS markorbit_facts.cn_admitted_citation_relation
(
    relation_id UUID,
    candidate_id String,
    candidate_fingerprint_sha256 FixedString(64),
    relation_type LowCardinality(String),
    subject_application_number String,
    subject_registration_number String,
    object_application_number String,
    object_registration_number String,
    event_date Date32,
    source_id String,
    source_document_id String,
    source_document_version UInt32,
    source_document_sha256 FixedString(64),
    source_uri String,
    evidence_locator_json String,
    extraction_method_id String,
    extraction_method_version String,
    confidence_score_basis_points UInt16,
    admitted_at DateTime64(3, 'UTC'),
    record_hash FixedString(64)
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
