from __future__ import annotations

from app.contact_ingest import country_inference_work as work
from app.db import postgres_conn


MEMBERSHIP_GUARD_VERSION = "CONTACT_COUNTRY_WORK_MEMBERSHIP_GUARD_V1"

_GUARD_SQL = r"""
ALTER TABLE contact.country_inference_work_unit
ADD COLUMN IF NOT EXISTS member_fingerprint char(32);

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

    IF NEW.task_group IS DISTINCT FROM OLD.task_group
       OR NEW.task_index IS DISTINCT FROM OLD.task_index
       OR NEW.task_total IS DISTINCT FROM OLD.task_total
       OR NEW.partition_kind IS DISTINCT FROM OLD.partition_kind
       OR NEW.range_lower IS DISTINCT FROM OLD.range_lower
       OR NEW.range_upper IS DISTINCT FROM OLD.range_upper
       OR NEW.operation_hash IS DISTINCT FROM OLD.operation_hash
       OR NEW.item_count IS DISTINCT FROM OLD.item_count THEN
        RAISE EXCEPTION
            'contact country work-unit durable identity is immutable';
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

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM contact.country_inference_work_unit
        WHERE member_fingerprint IS NULL
    ) THEN
        RAISE EXCEPTION
            'pre-existing contact country work units lack membership fingerprints';
    END IF;
END;
$$;

ALTER TABLE contact.country_inference_work_unit
ALTER COLUMN member_fingerprint SET NOT NULL;
"""


def ensure_country_inference_work_membership_guard() -> None:
    """Install fail-closed identity and membership gates around entity-range retry."""
    work.ensure_country_inference_work_schema()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_GUARD_SQL)
        conn.commit()
