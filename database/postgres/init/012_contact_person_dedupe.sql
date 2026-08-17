CREATE SCHEMA IF NOT EXISTS contact;

CREATE TABLE IF NOT EXISTS contact.person_merge_run (
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

CREATE TABLE IF NOT EXISTS contact.person_merge_decision (
    decision_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL REFERENCES contact.person_merge_run(run_id) ON DELETE CASCADE,
    entity_id uuid NOT NULL REFERENCES entity.entity(entity_id),
    canonical_person_id uuid NOT NULL REFERENCES contact.person(person_id),
    duplicate_person_id uuid NOT NULL REFERENCES contact.person(person_id),
    decision_status text NOT NULL,
    reason_codes jsonb NOT NULL DEFAULT '[]'::jsonb,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    applied_at timestamptz,
    CHECK (decision_status IN ('CANDIDATE', 'BLOCKED', 'APPLIED')),
    UNIQUE(run_id, duplicate_person_id),
    CHECK (canonical_person_id <> duplicate_person_id)
);

CREATE INDEX IF NOT EXISTS ix_contact_person_merge_run_started
ON contact.person_merge_run(started_at DESC);

CREATE INDEX IF NOT EXISTS ix_contact_person_merge_decision_status
ON contact.person_merge_decision(run_id, decision_status);

CREATE INDEX IF NOT EXISTS ix_contact_person_merge_duplicate
ON contact.person_merge_decision(duplicate_person_id, applied_at DESC);
