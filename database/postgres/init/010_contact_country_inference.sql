CREATE SCHEMA IF NOT EXISTS contact;

CREATE TABLE IF NOT EXISTS contact.country_inference_run (
    run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_version text NOT NULL,
    status text NOT NULL,
    apply_mode boolean NOT NULL DEFAULT false,
    min_confidence numeric(5,4) NOT NULL,
    min_margin numeric(5,4) NOT NULL,
    batch_size integer NOT NULL,
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_message text,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED', 'BUSY'))
);

CREATE TABLE IF NOT EXISTS contact.entity_country_inference (
    entity_id uuid PRIMARY KEY REFERENCES entity.entity(entity_id) ON DELETE CASCADE,
    last_run_id uuid REFERENCES contact.country_inference_run(run_id) ON DELETE SET NULL,
    rule_version text NOT NULL,
    status text NOT NULL,
    country_code char(2),
    confidence numeric(5,4) NOT NULL DEFAULT 0,
    runner_up_country_code char(2),
    runner_up_confidence numeric(5,4) NOT NULL DEFAULT 0,
    evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
    reason_codes jsonb NOT NULL DEFAULT '[]'::jsonb,
    first_inferred_at timestamptz NOT NULL DEFAULT now(),
    last_inferred_at timestamptz NOT NULL DEFAULT now(),
    applied_at timestamptz,
    CHECK (status IN ('ACCEPTED', 'CONFLICT', 'INSUFFICIENT'))
);

CREATE INDEX IF NOT EXISTS ix_contact_country_inference_status
ON contact.entity_country_inference(status, confidence DESC);

CREATE INDEX IF NOT EXISTS ix_contact_country_inference_country
ON contact.entity_country_inference(country_code, confidence DESC)
WHERE country_code IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_contact_country_inference_applied
ON contact.entity_country_inference(applied_at DESC)
WHERE applied_at IS NOT NULL;
