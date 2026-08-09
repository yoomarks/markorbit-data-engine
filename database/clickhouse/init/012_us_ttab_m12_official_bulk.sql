ALTER TABLE markorbit_facts.us_ttab_proceeding_history
    ADD COLUMN IF NOT EXISTS employee_number String AFTER paralegal_name,
    ADD COLUMN IF NOT EXISTS location_code String AFTER employee_number,
    ADD COLUMN IF NOT EXISTS day_in_location Nullable(Date32) AFTER location_code,
    ADD COLUMN IF NOT EXISTS day_in_location_raw String AFTER day_in_location,
    ADD COLUMN IF NOT EXISTS charge_to_location_code String AFTER day_in_location_raw,
    ADD COLUMN IF NOT EXISTS charge_to_employee_name String AFTER charge_to_location_code;

ALTER TABLE markorbit_facts.us_ttab_party_history
    ADD COLUMN IF NOT EXISTS correspondent_address_id String AFTER correspondent_phone,
    ADD COLUMN IF NOT EXISTS correspondent_address_type_code String AFTER correspondent_address_id;

ALTER TABLE markorbit_facts.us_ttab_property_history
    ADD COLUMN IF NOT EXISTS source_property_id String AFTER trademark_gid,
    ADD COLUMN IF NOT EXISTS tma_proceeding_number String AFTER source_property_id,
    ADD COLUMN IF NOT EXISTS tma_proceeding_type_code String AFTER tma_proceeding_number;

ALTER TABLE markorbit_facts.us_ttab_docket_history
    ADD COLUMN IF NOT EXISTS entry_type_code String AFTER entry_code;

INSERT INTO markorbit_facts.schema_version (component, version)
SELECT 'US_TTAB', 'US_TTAB_M1.2'
WHERE NOT EXISTS
(
    SELECT 1 FROM markorbit_facts.schema_version FINAL
    WHERE component = 'US_TTAB' AND version = 'US_TTAB_M1.2'
);
