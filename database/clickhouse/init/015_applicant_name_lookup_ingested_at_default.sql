ALTER TABLE markorbit_facts.us_applicant_name_lookup_current
    MODIFY COLUMN ingested_at DateTime64(3, 'UTC') DEFAULT now64(3);
