CREATE SCHEMA IF NOT EXISTS contact;

CREATE TABLE IF NOT EXISTS contact.case_contact_observation (
    observation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    observation_key char(64) NOT NULL UNIQUE,
    source_id uuid NOT NULL REFERENCES contact.source(source_id) ON DELETE CASCADE,
    raw_record_id uuid REFERENCES contact.raw_record(raw_record_id) ON DELETE SET NULL,
    jurisdiction char(2),
    application_number text NOT NULL DEFAULT '',
    registration_number text NOT NULL DEFAULT '',
    channel_type text NOT NULL,
    raw_value text NOT NULL,
    normalized_value text NOT NULL,
    source_column text NOT NULL DEFAULT '',
    owner_assignment_status text NOT NULL DEFAULT 'UNRESOLVED',
    confidence_score numeric(5,4) NOT NULL DEFAULT 0.7500,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    observed_at timestamptz NOT NULL DEFAULT now(),
    CHECK (application_number <> '' OR registration_number <> ''),
    CHECK (owner_assignment_status = 'UNRESOLVED')
);
CREATE INDEX IF NOT EXISTS ix_contact_case_observation_application
ON contact.case_contact_observation(application_number)
WHERE application_number <> '';
CREATE INDEX IF NOT EXISTS ix_contact_case_observation_registration
ON contact.case_contact_observation(registration_number)
WHERE registration_number <> '';
CREATE INDEX IF NOT EXISTS ix_contact_case_observation_channel
ON contact.case_contact_observation(channel_type, normalized_value);

INSERT INTO control.schema_version(component, version)
VALUES ('CONTACT_CASE_EVIDENCE', 'CONTACT_CASE_EVIDENCE_V1')
ON CONFLICT (component)
DO UPDATE SET version = EXCLUDED.version, applied_at = now();
