from __future__ import annotations

from app.db import postgres_conn


PORTABILITY_SQL = r'''
ALTER TABLE acquisition.cn_qcc_batch
    ADD COLUMN IF NOT EXISTS export_object_key text;
ALTER TABLE acquisition.cn_qcc_batch
    ADD COLUMN IF NOT EXISTS result_object_key text;
ALTER TABLE acquisition.cn_qcc_company_observation
    ADD COLUMN IF NOT EXISTS source_result_key text NOT NULL DEFAULT '';

-- Historical rows keep their original local path for audit compatibility, but
-- receive a portable logical key. New writes are normalized by triggers below.
UPDATE acquisition.cn_qcc_batch
SET export_object_key = 'cn_qcc/outgoing/' || batch_key || '.tasks.csv'
WHERE export_sha256 IS NOT NULL
  AND COALESCE(export_object_key, '') = '';

UPDATE acquisition.cn_qcc_batch
SET result_object_key = 'cn_qcc/incoming/' || batch_key || '.result.csv'
WHERE result_sha256 IS NOT NULL
  AND COALESCE(result_object_key, '') = '';

UPDATE acquisition.cn_qcc_company_observation AS observation
SET source_result_key = 'cn_qcc/incoming/' || batch.batch_key || '.result.csv'
FROM acquisition.cn_qcc_batch AS batch
WHERE batch.batch_id = observation.batch_id
  AND observation.source_result_sha256 IS NOT NULL
  AND COALESCE(observation.source_result_key, '') = '';

CREATE OR REPLACE FUNCTION acquisition.cn_qcc_portable_batch_paths()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT'
       OR NEW.export_path IS DISTINCT FROM OLD.export_path THEN
        IF NEW.export_sha256 IS NOT NULL THEN
            NEW.export_object_key := 'cn_qcc/outgoing/' || NEW.batch_key || '.tasks.csv';
            NEW.export_path := NULL;
        END IF;
    END IF;

    IF TG_OP = 'INSERT'
       OR NEW.result_path IS DISTINCT FROM OLD.result_path THEN
        IF NEW.result_sha256 IS NOT NULL THEN
            NEW.result_object_key := 'cn_qcc/incoming/' || NEW.batch_key || '.result.csv';
            NEW.result_path := NULL;
        END IF;
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_cn_qcc_portable_batch_paths
ON acquisition.cn_qcc_batch;
CREATE TRIGGER trg_cn_qcc_portable_batch_paths
BEFORE INSERT OR UPDATE ON acquisition.cn_qcc_batch
FOR EACH ROW EXECUTE FUNCTION acquisition.cn_qcc_portable_batch_paths();

CREATE OR REPLACE FUNCTION acquisition.cn_qcc_portable_observation_path()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    source_batch_key text;
BEGIN
    IF COALESCE(NEW.source_result_key, '') = '' THEN
        SELECT batch_key INTO source_batch_key
        FROM acquisition.cn_qcc_batch
        WHERE batch_id = NEW.batch_id;
        IF source_batch_key IS NULL THEN
            RAISE EXCEPTION 'QCC observation batch missing while assigning source_result_key'
                USING ERRCODE = '23514';
        END IF;
        NEW.source_result_key := 'cn_qcc/incoming/' || source_batch_key || '.result.csv';
    END IF;
    -- Host-local paths are runtime details, not canonical provenance.
    NEW.source_result_path := '';
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_cn_qcc_portable_observation_path
ON acquisition.cn_qcc_company_observation;
CREATE TRIGGER trg_cn_qcc_portable_observation_path
BEFORE INSERT OR UPDATE ON acquisition.cn_qcc_company_observation
FOR EACH ROW EXECUTE FUNCTION acquisition.cn_qcc_portable_observation_path();
'''


def ensure_qcc_portability_schema() -> None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(PORTABILITY_SQL)
        conn.commit()


__all__ = ["PORTABILITY_SQL", "ensure_qcc_portability_schema"]
