from __future__ import annotations

from app.db import postgres_conn


CIPO_ST96_CURRENT_PROJECTION_VERSION = "CIPO_ST96_CURRENT_PROJECTION_V1"


_CA_CURRENT_SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS trademark_ca.current_source_order (
    record_key text PRIMARY KEY,
    application_number text NOT NULL,
    extension_counter text NOT NULL,
    source_object_id uuid NOT NULL
        REFERENCES acquisition.global_trademark_source_object(object_id),
    source_id text NOT NULL,
    manifest_id uuid NOT NULL
        REFERENCES acquisition.global_trademark_manifest(manifest_id),
    manifest_key text NOT NULL,
    source_period_end date NOT NULL,
    source_precedence bigint NOT NULL,
    source_sequence bigint NOT NULL,
    part_sequence integer,
    operation_category text NOT NULL,
    source_present boolean NOT NULL,
    observed_at timestamptz NOT NULL DEFAULT now(),
    CHECK (source_precedence >= 0),
    CHECK (source_sequence >= 0),
    CHECK (part_sequence IS NULL OR part_sequence >= 1),
    CHECK (operation_category IN ('Update', 'Delete'))
);
CREATE INDEX IF NOT EXISTS idx_trademark_ca_current_source_order_release
    ON trademark_ca.current_source_order (
        source_period_end, source_precedence, source_sequence
    );

CREATE OR REPLACE FUNCTION trademark_ca.st96_record_current_order_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    incoming_count integer;
    incoming_manifest_id uuid;
    incoming_source_id text;
    incoming_manifest_key text;
    incoming_period_end date;
    incoming_precedence bigint;
    incoming_sequence bigint;
    existing_source_object_id uuid;
    existing_period_end date;
    existing_precedence bigint;
    existing_sequence bigint;
BEGIN
    SELECT COUNT(*)::integer,
           MIN(m.manifest_id::text)::uuid,
           MIN(m.source_id),
           MIN(m.manifest_key),
           MIN(m.source_period_end),
           MIN(m.source_precedence),
           MIN(m.source_sequence)
      INTO incoming_count,
           incoming_manifest_id,
           incoming_source_id,
           incoming_manifest_key,
           incoming_period_end,
           incoming_precedence,
           incoming_sequence
      FROM acquisition.global_trademark_manifest_object AS mo
      JOIN acquisition.global_trademark_manifest AS m
        ON m.manifest_id = mo.manifest_id
     WHERE mo.source_object_id = NEW.source_object_id
       AND m.jurisdiction = 'CA';

    SELECT source_object_id, source_period_end, source_precedence, source_sequence
      INTO existing_source_object_id,
           existing_period_end,
           existing_precedence,
           existing_sequence
      FROM trademark_ca.current_source_order
     WHERE record_key = NEW.record_key;

    IF incoming_count = 0 THEN
        -- Backward-compatible direct/test ingestion is allowed only until a record has
        -- a manifest-backed ordered winner. It can never regress an ordered current row.
        IF existing_source_object_id IS NULL THEN
            RETURN NEW;
        END IF;
        RETURN NULL;
    END IF;
    IF incoming_count <> 1 THEN
        RAISE EXCEPTION
            'CIPO source object % is attached to % Canada manifests; current order is ambiguous',
            NEW.source_object_id, incoming_count;
    END IF;
    IF incoming_period_end IS NULL THEN
        RAISE EXCEPTION
            'CIPO manifest % requires source_period_end for ordered current projection',
            incoming_manifest_key;
    END IF;
    IF existing_source_object_id IS NULL OR existing_source_object_id = NEW.source_object_id THEN
        RETURN NEW;
    END IF;
    IF ROW(incoming_period_end, incoming_precedence, incoming_sequence)
       > ROW(existing_period_end, existing_precedence, existing_sequence) THEN
        RETURN NEW;
    END IF;
    IF ROW(incoming_period_end, incoming_precedence, incoming_sequence)
       < ROW(existing_period_end, existing_precedence, existing_sequence) THEN
        RETURN NULL;
    END IF;
    RAISE EXCEPTION
        'CIPO current order collision for record %: different source objects share rank (%, %, %)',
        NEW.record_key, incoming_period_end, incoming_precedence, incoming_sequence;
END;
$$;

DROP TRIGGER IF EXISTS trg_st96_record_current_order_guard
    ON trademark_ca.st96_record;
CREATE TRIGGER trg_st96_record_current_order_guard
BEFORE INSERT OR UPDATE ON trademark_ca.st96_record
FOR EACH ROW EXECUTE FUNCTION trademark_ca.st96_record_current_order_guard();

CREATE OR REPLACE FUNCTION trademark_ca.record_state_current_order_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    incoming_count integer;
    incoming_manifest_id uuid;
    incoming_source_id text;
    incoming_manifest_key text;
    incoming_period_end date;
    incoming_precedence bigint;
    incoming_sequence bigint;
    incoming_part_sequence integer;
    existing_source_object_id uuid;
    existing_period_end date;
    existing_precedence bigint;
    existing_sequence bigint;
    accept_current boolean := false;
BEGIN
    SELECT COUNT(*)::integer,
           MIN(m.manifest_id::text)::uuid,
           MIN(m.source_id),
           MIN(m.manifest_key),
           MIN(m.source_period_end),
           MIN(m.source_precedence),
           MIN(m.source_sequence),
           MIN(mo.part_sequence)
      INTO incoming_count,
           incoming_manifest_id,
           incoming_source_id,
           incoming_manifest_key,
           incoming_period_end,
           incoming_precedence,
           incoming_sequence,
           incoming_part_sequence
      FROM acquisition.global_trademark_manifest_object AS mo
      JOIN acquisition.global_trademark_manifest AS m
        ON m.manifest_id = mo.manifest_id
     WHERE mo.source_object_id = NEW.last_source_object_id
       AND m.jurisdiction = 'CA';

    SELECT source_object_id, source_period_end, source_precedence, source_sequence
      INTO existing_source_object_id,
           existing_period_end,
           existing_precedence,
           existing_sequence
      FROM trademark_ca.current_source_order
     WHERE record_key = NEW.record_key
     FOR UPDATE;

    IF incoming_count = 0 THEN
        IF existing_source_object_id IS NULL THEN
            RETURN NEW;
        END IF;
        RETURN NULL;
    END IF;
    IF incoming_count <> 1 THEN
        RAISE EXCEPTION
            'CIPO source object % is attached to % Canada manifests; current order is ambiguous',
            NEW.last_source_object_id, incoming_count;
    END IF;
    IF incoming_period_end IS NULL THEN
        RAISE EXCEPTION
            'CIPO manifest % requires source_period_end for ordered current projection',
            incoming_manifest_key;
    END IF;

    IF existing_source_object_id IS NULL OR existing_source_object_id = NEW.last_source_object_id THEN
        accept_current := true;
    ELSIF ROW(incoming_period_end, incoming_precedence, incoming_sequence)
          > ROW(existing_period_end, existing_precedence, existing_sequence) THEN
        accept_current := true;
    ELSIF ROW(incoming_period_end, incoming_precedence, incoming_sequence)
          < ROW(existing_period_end, existing_precedence, existing_sequence) THEN
        RETURN NULL;
    ELSE
        RAISE EXCEPTION
            'CIPO current order collision for record %: different source objects share rank (%, %, %)',
            NEW.record_key, incoming_period_end, incoming_precedence, incoming_sequence;
    END IF;

    IF accept_current THEN
        INSERT INTO trademark_ca.current_source_order (
            record_key, application_number, extension_counter, source_object_id,
            source_id, manifest_id, manifest_key, source_period_end,
            source_precedence, source_sequence, part_sequence,
            operation_category, source_present, observed_at
        ) VALUES (
            NEW.record_key, NEW.application_number, NEW.extension_counter,
            NEW.last_source_object_id, incoming_source_id, incoming_manifest_id,
            incoming_manifest_key, incoming_period_end, incoming_precedence,
            incoming_sequence, incoming_part_sequence,
            NEW.last_operation_category, NEW.source_present, now()
        )
        ON CONFLICT (record_key) DO UPDATE SET
            application_number = EXCLUDED.application_number,
            extension_counter = EXCLUDED.extension_counter,
            source_object_id = EXCLUDED.source_object_id,
            source_id = EXCLUDED.source_id,
            manifest_id = EXCLUDED.manifest_id,
            manifest_key = EXCLUDED.manifest_key,
            source_period_end = EXCLUDED.source_period_end,
            source_precedence = EXCLUDED.source_precedence,
            source_sequence = EXCLUDED.source_sequence,
            part_sequence = EXCLUDED.part_sequence,
            operation_category = EXCLUDED.operation_category,
            source_present = EXCLUDED.source_present,
            observed_at = now();
        RETURN NEW;
    END IF;
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_record_state_current_order_guard
    ON trademark_ca.record_state;
CREATE TRIGGER trg_record_state_current_order_guard
BEFORE INSERT OR UPDATE ON trademark_ca.record_state
FOR EACH ROW EXECUTE FUNCTION trademark_ca.record_state_current_order_guard();

CREATE OR REPLACE VIEW trademark_ca.record_current AS
SELECT r.*
FROM trademark_ca.st96_record AS r
JOIN trademark_ca.record_state AS s
  ON s.record_key = r.record_key
WHERE s.source_present = true
  AND r.source_object_id = s.last_source_object_id;

CREATE OR REPLACE VIEW trademark_ca.party_current AS
SELECT p.*
FROM trademark_ca.party AS p
JOIN trademark_ca.record_state AS s
  ON s.record_key = p.record_key
WHERE s.source_present = true
  AND p.source_object_id = s.last_source_object_id;

CREATE OR REPLACE VIEW trademark_ca.goods_service_current AS
SELECT g.*
FROM trademark_ca.goods_service AS g
JOIN trademark_ca.record_state AS s
  ON s.record_key = g.record_key
WHERE s.source_present = true
  AND g.source_object_id = s.last_source_object_id;

CREATE OR REPLACE VIEW trademark_ca.event_current AS
SELECT e.*
FROM trademark_ca.event AS e
JOIN trademark_ca.record_state AS s
  ON s.record_key = e.record_key
WHERE s.source_present = true
  AND e.source_object_id = s.last_source_object_id;

CREATE OR REPLACE VIEW trademark_ca.relationship_current AS
SELECT r.*
FROM trademark_ca.relationship AS r
JOIN trademark_ca.record_state AS s
  ON s.record_key = r.record_key
WHERE s.source_present = true
  AND r.source_object_id = s.last_source_object_id;
"""


def ensure_ca_current_projection_schema() -> None:
    """Install the ordered Canada source-current storage boundary.

    CIPO history remains append-only. Manifest-backed observations compete for the current
    projection by ``(source_period_end, source_precedence, source_sequence)`` rather than
    ingestion time. Stale observations remain queryable history but cannot regress parent
    current state. Equal-ranked different objects fail closed.
    """
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_CA_CURRENT_SCHEMA_SQL)
        conn.commit()
