CREATE MATERIALIZED VIEW IF NOT EXISTS markorbit_facts.cn_applicant_name_lookup_from_case_party_mv
TO markorbit_facts.cn_applicant_name_lookup_current
AS
SELECT
    normalized_name,
    assumeNotNull(entity_id) AS entity_id,
    application_number,
    relation_key,
    source_row_hash,
    record_hash,
    source_rank,
    ingested_at,
    toUInt8(is_deleted = 1 OR is_current = 0) AS is_deleted
FROM markorbit_facts.cn_case_party_current
WHERE role IN ('OWNER', 'CO_OWNER')
  AND entity_id IS NOT NULL
  AND normalized_name != '';
