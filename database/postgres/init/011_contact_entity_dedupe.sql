CREATE SCHEMA IF NOT EXISTS contact;

CREATE TABLE IF NOT EXISTS contact.entity_merge_run (
    run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_version text NOT NULL,
    status text NOT NULL,
    apply_mode boolean NOT NULL DEFAULT false,
    country_code char(2),
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_message text,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED', 'BUSY'))
);

CREATE TABLE IF NOT EXISTS contact.entity_merge_decision (
    decision_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL REFERENCES contact.entity_merge_run(run_id) ON DELETE CASCADE,
    canonical_entity_id uuid NOT NULL REFERENCES entity.entity(entity_id),
    duplicate_entity_id uuid NOT NULL REFERENCES entity.entity(entity_id),
    decision_status text NOT NULL,
    reason_codes jsonb NOT NULL DEFAULT '[]'::jsonb,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    applied_at timestamptz,
    CHECK (decision_status IN ('CANDIDATE', 'BLOCKED', 'APPLIED')),
    UNIQUE(run_id, duplicate_entity_id),
    CHECK (canonical_entity_id <> duplicate_entity_id)
);

CREATE INDEX IF NOT EXISTS ix_contact_entity_merge_run_started
ON contact.entity_merge_run(started_at DESC);

CREATE INDEX IF NOT EXISTS ix_contact_entity_merge_decision_status
ON contact.entity_merge_decision(run_id, decision_status);

CREATE INDEX IF NOT EXISTS ix_contact_entity_merge_duplicate
ON contact.entity_merge_decision(duplicate_entity_id, applied_at DESC);
