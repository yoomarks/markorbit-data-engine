CREATE SCHEMA IF NOT EXISTS contact;

CREATE TABLE IF NOT EXISTS contact.country_inference_work_unit (
    run_id uuid NOT NULL
        REFERENCES contact.country_inference_run(run_id) ON DELETE CASCADE,
    checkpoint_version text NOT NULL,
    task_key char(64) NOT NULL,
    task_group text NOT NULL,
    task_index integer NOT NULL,
    task_total integer NOT NULL,
    partition_kind text NOT NULL,
    range_lower uuid,
    range_upper uuid,
    operation_hash char(64) NOT NULL,
    item_count integer NOT NULL,
    status text NOT NULL,
    attempts integer NOT NULL DEFAULT 0,
    started_at timestamptz,
    completed_at timestamptz,
    last_error text NOT NULL DEFAULT '',
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, checkpoint_version, task_key),
    CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED')),
    CHECK (task_index >= 1),
    CHECK (task_total >= task_index),
    CHECK (item_count >= 1),
    CHECK (partition_kind = 'ENTITY_RANGE'),
    CHECK (range_lower IS NOT NULL),
    CHECK (range_upper IS NOT NULL),
    CHECK (range_lower <= range_upper)
);

CREATE INDEX IF NOT EXISTS ix_contact_country_inference_work_status
ON contact.country_inference_work_unit(run_id, checkpoint_version, status, task_index);

CREATE INDEX IF NOT EXISTS ix_contact_country_inference_last_run
ON contact.entity_country_inference(last_run_id, entity_id);

INSERT INTO control.schema_version(component, version)
VALUES ('CONTACT_COUNTRY_INFERENCE_WORK', 'CONTACT_COUNTRY_INFERENCE_WORK_V1')
ON CONFLICT (component)
DO UPDATE SET version = EXCLUDED.version, applied_at = now();
