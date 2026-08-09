INSERT INTO markorbit_facts.schema_version (component, version)
SELECT 'US_CORE', 'US_M1.2'
WHERE NOT EXISTS
(
    SELECT 1 FROM markorbit_facts.schema_version FINAL
    WHERE component = 'US_CORE' AND version = 'US_M1.2'
);
