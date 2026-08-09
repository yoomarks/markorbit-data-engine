ALTER TABLE markorbit_facts.us_ttab_proceeding_history
    ADD COLUMN IF NOT EXISTS proceeding_type_code String AFTER proceeding_type,
    ADD COLUMN IF NOT EXISTS status_code String AFTER status_text;

ALTER TABLE markorbit_facts.us_ttab_party_history
    ADD COLUMN IF NOT EXISTS party_id String AFTER party_name,
    ADD COLUMN IF NOT EXISTS role String AFTER party_id,
    ADD COLUMN IF NOT EXISTS company String AFTER role,
    ADD COLUMN IF NOT EXISTS organization String AFTER company,
    ADD COLUMN IF NOT EXISTS granted_to_date_raw String AFTER organization,
    ADD COLUMN IF NOT EXISTS correspondent_organization String AFTER correspondent_name;

ALTER TABLE markorbit_facts.us_ttab_property_history
    ADD COLUMN IF NOT EXISTS mark_explanation String AFTER mark_text,
    ADD COLUMN IF NOT EXISTS property_filing String AFTER mark_explanation,
    ADD COLUMN IF NOT EXISTS property_filing_code String AFTER property_filing,
    ADD COLUMN IF NOT EXISTS common_law_indicator String AFTER property_filing_code,
    ADD COLUMN IF NOT EXISTS application_status_code String AFTER application_status,
    ADD COLUMN IF NOT EXISTS trademark_gid String AFTER application_status_code;

ALTER TABLE markorbit_facts.us_ttab_docket_history
    ADD COLUMN IF NOT EXISTS identifier String AFTER entry_number,
    ADD COLUMN IF NOT EXISTS object_id String AFTER identifier,
    ADD COLUMN IF NOT EXISTS entry_code String AFTER object_id,
    ADD COLUMN IF NOT EXISTS confidential String AFTER entry_code;

INSERT INTO markorbit_facts.schema_version (component, version)
SELECT 'US_TTAB', 'US_TTAB_M1.1'
WHERE NOT EXISTS
(
    SELECT 1 FROM markorbit_facts.schema_version FINAL
    WHERE component = 'US_TTAB' AND version = 'US_TTAB_M1.1'
);
