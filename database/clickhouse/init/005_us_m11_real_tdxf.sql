ALTER TABLE markorbit_facts.us_case_current ADD COLUMN IF NOT EXISTS transaction_date Nullable(Date32) AFTER registration_number;
ALTER TABLE markorbit_facts.us_case_current ADD COLUMN IF NOT EXISTS use_1a_filed UInt8 DEFAULT 0 AFTER no_basis;
ALTER TABLE markorbit_facts.us_case_current ADD COLUMN IF NOT EXISTS use_1a_current UInt8 DEFAULT 0 AFTER use_1a_filed;
ALTER TABLE markorbit_facts.us_case_current ADD COLUMN IF NOT EXISTS intent_to_use_1b_filed UInt8 DEFAULT 0 AFTER use_1a_current;
ALTER TABLE markorbit_facts.us_case_current ADD COLUMN IF NOT EXISTS intent_to_use_1b_current UInt8 DEFAULT 0 AFTER intent_to_use_1b_filed;
ALTER TABLE markorbit_facts.us_case_current ADD COLUMN IF NOT EXISTS foreign_application_44d_filed UInt8 DEFAULT 0 AFTER intent_to_use_1b_current;
ALTER TABLE markorbit_facts.us_case_current ADD COLUMN IF NOT EXISTS foreign_application_44d_current UInt8 DEFAULT 0 AFTER foreign_application_44d_filed;
ALTER TABLE markorbit_facts.us_case_current ADD COLUMN IF NOT EXISTS foreign_registration_44e_filed UInt8 DEFAULT 0 AFTER foreign_application_44d_current;
ALTER TABLE markorbit_facts.us_case_current ADD COLUMN IF NOT EXISTS foreign_registration_44e_current UInt8 DEFAULT 0 AFTER foreign_registration_44e_filed;
ALTER TABLE markorbit_facts.us_case_current ADD COLUMN IF NOT EXISTS madrid_66a_filed UInt8 DEFAULT 0 AFTER foreign_registration_44e_current;
ALTER TABLE markorbit_facts.us_case_current ADD COLUMN IF NOT EXISTS madrid_66a_current UInt8 DEFAULT 0 AFTER madrid_66a_filed;
ALTER TABLE markorbit_facts.us_case_current ADD COLUMN IF NOT EXISTS no_basis_current UInt8 DEFAULT 0 AFTER madrid_66a_current;
ALTER TABLE markorbit_facts.us_case_current ADD COLUMN IF NOT EXISTS renewal_filed UInt8 DEFAULT 0 AFTER no_basis_current;
ALTER TABLE markorbit_facts.us_case_current ADD COLUMN IF NOT EXISTS section_8_filed UInt8 DEFAULT 0 AFTER renewal_filed;
ALTER TABLE markorbit_facts.us_case_current ADD COLUMN IF NOT EXISTS section_8_accepted UInt8 DEFAULT 0 AFTER section_8_filed;
ALTER TABLE markorbit_facts.us_case_current ADD COLUMN IF NOT EXISTS section_8_partial_accepted UInt8 DEFAULT 0 AFTER section_8_accepted;
ALTER TABLE markorbit_facts.us_case_current ADD COLUMN IF NOT EXISTS section_15_filed UInt8 DEFAULT 0 AFTER section_8_partial_accepted;
ALTER TABLE markorbit_facts.us_case_current ADD COLUMN IF NOT EXISTS section_15_acknowledged UInt8 DEFAULT 0 AFTER section_15_filed;
ALTER TABLE markorbit_facts.us_case_current ADD COLUMN IF NOT EXISTS opposition_pending UInt8 DEFAULT 0 AFTER section_15_acknowledged;
ALTER TABLE markorbit_facts.us_case_current ADD COLUMN IF NOT EXISTS cancellation_pending UInt8 DEFAULT 0 AFTER opposition_pending;
ALTER TABLE markorbit_facts.us_case_current ADD COLUMN IF NOT EXISTS international_registration_date Nullable(Date32) AFTER international_registration_number;
ALTER TABLE markorbit_facts.us_case_current ADD COLUMN IF NOT EXISTS international_publication_date Nullable(Date32) AFTER international_registration_date;
ALTER TABLE markorbit_facts.us_case_current ADD COLUMN IF NOT EXISTS international_renewal_date Nullable(Date32) AFTER international_publication_date;
ALTER TABLE markorbit_facts.us_case_current ADD COLUMN IF NOT EXISTS international_auto_protection_date Nullable(Date32) AFTER international_renewal_date;
ALTER TABLE markorbit_facts.us_case_current ADD COLUMN IF NOT EXISTS international_death_date Nullable(Date32) AFTER international_auto_protection_date;
ALTER TABLE markorbit_facts.us_case_current ADD COLUMN IF NOT EXISTS international_priority_claimed UInt8 DEFAULT 0 AFTER international_registration_status_date;
ALTER TABLE markorbit_facts.us_case_current ADD COLUMN IF NOT EXISTS international_priority_claimed_date Nullable(Date32) AFTER international_priority_claimed;
ALTER TABLE markorbit_facts.us_case_current ADD COLUMN IF NOT EXISTS international_first_refusal UInt8 DEFAULT 0 AFTER international_priority_claimed_date;

ALTER TABLE markorbit_facts.us_owner_current ADD COLUMN IF NOT EXISTS entity_statement String AFTER legal_entity_type_code;
ALTER TABLE markorbit_facts.us_owner_current ADD COLUMN IF NOT EXISTS dba_aka_text String AFTER postcode;
ALTER TABLE markorbit_facts.us_owner_current ADD COLUMN IF NOT EXISTS composed_of_statement String AFTER dba_aka_text;

ALTER TABLE markorbit_facts.us_event_history ADD COLUMN IF NOT EXISTS description_text String AFTER event_type_code;

INSERT INTO markorbit_facts.schema_version (component, version)
SELECT 'US_CORE', 'US_M1.1'
WHERE NOT EXISTS
(
    SELECT 1 FROM markorbit_facts.schema_version FINAL
    WHERE component = 'US_CORE' AND version = 'US_M1.1'
);
