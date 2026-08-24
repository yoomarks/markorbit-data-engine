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
    member_fingerprint char(32) NOT NULL,
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

CREATE OR REPLACE FUNCTION contact.country_inference_work_member_fingerprint(
    p_lower uuid,
    p_upper uuid
) RETURNS char(32)
LANGUAGE sql
STABLE
AS $$
    SELECT md5(
        COALESCE(
            string_agg(e.entity_id::text, ',' ORDER BY e.entity_id),
            ''
        )
    )
    FROM entity.entity AS e
    WHERE e.entity_id >= p_lower
      AND e.entity_id <= p_upper
      AND e.country_code IS NULL
      AND NOT EXISTS (
          SELECT 1
          FROM contact.entity_country_inference AS active_ci
          WHERE active_ci.entity_id = e.entity_id
            AND active_ci.status = 'ACCEPTED'
            AND active_ci.applied_at IS NOT NULL
      )
      AND (
          EXISTS (
              SELECT 1
              FROM contact.raw_record AS rr
              WHERE rr.entity_id = e.entity_id
          )
          OR EXISTS (
              SELECT 1
              FROM contact.entity_person_relation AS r
              WHERE r.entity_id = e.entity_id
          )
          OR EXISTS (
              SELECT 1
              FROM contact.channel AS c
              WHERE c.entity_id = e.entity_id
          )
      );
$$;

CREATE OR REPLACE FUNCTION contact.guard_country_inference_work_membership()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    observed_fingerprint char(32);
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.member_fingerprint := contact.country_inference_work_member_fingerprint(
            NEW.range_lower,
            NEW.range_upper
        );
        RETURN NEW;
    END IF;

    IF NEW.status = 'RUNNING' AND NEW.attempts > OLD.attempts THEN
        IF OLD.member_fingerprint IS NULL THEN
            RAISE EXCEPTION
                'contact country work unit is missing durable membership fingerprint';
        END IF;
        observed_fingerprint := contact.country_inference_work_member_fingerprint(
            OLD.range_lower,
            OLD.range_upper
        );
        IF observed_fingerprint IS DISTINCT FROM OLD.member_fingerprint THEN
            RAISE EXCEPTION
                'contact country work-unit membership drift: expected %, observed %',
                OLD.member_fingerprint,
                observed_fingerprint;
        END IF;
    END IF;

    NEW.member_fingerprint := OLD.member_fingerprint;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_contact_country_work_membership
ON contact.country_inference_work_unit;

CREATE TRIGGER trg_contact_country_work_membership
BEFORE INSERT OR UPDATE ON contact.country_inference_work_unit
FOR EACH ROW
EXECUTE FUNCTION contact.guard_country_inference_work_membership();

INSERT INTO control.schema_version(component, version)
VALUES ('CONTACT_COUNTRY_INFERENCE_WORK', 'CONTACT_COUNTRY_INFERENCE_WORK_V1')
ON CONFLICT (component)
DO UPDATE SET version = EXCLUDED.version, applied_at = now();
