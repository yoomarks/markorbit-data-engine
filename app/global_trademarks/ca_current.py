from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from app.db import postgres_conn


CIPO_ST96_CURRENT_PROJECTION_VERSION = "CIPO_ST96_CURRENT_PROJECTION_V1"


_CA_CURRENT_SCHEMA_SQL = """
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


@dataclass(frozen=True, slots=True)
class CipoSourceOrder:
    source_object_id: uuid.UUID
    source_id: str
    manifest_id: uuid.UUID
    manifest_key: str
    source_period_end: date
    source_precedence: int
    source_sequence: int
    part_sequence: int | None

    @property
    def rank(self) -> tuple[date, int, int]:
        # Coverage chronology is the cross-stream ordering axis. Precedence and
        # sequence only break ties inside the same coverage-through date.
        return (self.source_period_end, self.source_precedence, self.source_sequence)


def ensure_ca_current_projection_schema() -> None:
    """Install the explicit Canada source-current projection boundary.

    Immutable CIPO observations remain in their native history tables. These views expose
    only children belonging to the winning source object recorded in ``record_state``.
    The ordering ledger is populated only for manifest-backed source objects.
    """
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_CA_CURRENT_SCHEMA_SQL)
        conn.commit()


def _current_order_relation_ready(cur) -> bool:
    cur.execute(
        "SELECT to_regclass('trademark_ca.current_source_order') AS relation"
    )
    return cur.fetchone()["relation"] is not None


def cipo_source_order(source_object_id: uuid.UUID) -> CipoSourceOrder | None:
    """Return the one manifest-backed ordering context for a CIPO source object.

    Direct legacy/test loader calls without a manifest remain supported, but once a record
    has an ordered winner they can no longer regress that record's current projection.
    Manifest-backed CIPO ingestion is fail-closed unless ``source_period_end`` is explicit.
    """
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    to_regclass('acquisition.global_trademark_manifest') AS manifest_relation,
                    to_regclass('acquisition.global_trademark_manifest_object') AS object_relation,
                    to_regclass('trademark_ca.current_source_order') AS current_relation
                """
            )
            relations = cur.fetchone()
            if (
                relations["manifest_relation"] is None
                or relations["object_relation"] is None
                or relations["current_relation"] is None
            ):
                return None
            cur.execute(
                """
                SELECT m.manifest_id, m.source_id, m.manifest_key,
                       m.source_period_end, m.source_precedence, m.source_sequence,
                       mo.part_sequence
                FROM acquisition.global_trademark_manifest_object AS mo
                JOIN acquisition.global_trademark_manifest AS m
                  ON m.manifest_id = mo.manifest_id
                WHERE mo.source_object_id = %s
                  AND m.jurisdiction = 'CA'
                ORDER BY m.manifest_id
                """,
                (source_object_id,),
            )
            rows = cur.fetchall()

    if not rows:
        return None
    if len(rows) != 1:
        raise RuntimeError(
            "CIPO source object is attached to multiple Canada manifests; "
            "ordered current projection is ambiguous"
        )
    row = rows[0]
    if row["source_period_end"] is None:
        raise RuntimeError(
            "CIPO manifest source_period_end is required for ordered current projection"
        )
    return CipoSourceOrder(
        source_object_id=source_object_id,
        source_id=str(row["source_id"]),
        manifest_id=row["manifest_id"],
        manifest_key=str(row["manifest_key"]),
        source_period_end=row["source_period_end"],
        source_precedence=int(row["source_precedence"]),
        source_sequence=int(row["source_sequence"]),
        part_sequence=(
            int(row["part_sequence"]) if row["part_sequence"] is not None else None
        ),
    )


def current_projection_accepts(
    cur,
    *,
    record_key: str,
    source_object_id: uuid.UUID,
    incoming_order: CipoSourceOrder | None,
) -> bool:
    """Return whether an observation may change the source-current projection.

    History is always appendable. Current state is monotonic by the explicit manifest rank
    ``(source_period_end, source_precedence, source_sequence)``. Equal-ranked different
    source objects are rejected instead of being resolved by ingestion time.
    """
    if not _current_order_relation_ready(cur):
        return True
    cur.execute(
        """
        SELECT source_object_id, source_period_end, source_precedence, source_sequence
        FROM trademark_ca.current_source_order
        WHERE record_key = %s
        FOR UPDATE
        """,
        (record_key,),
    )
    existing = cur.fetchone()

    if incoming_order is None:
        # Preserve direct legacy/test behavior only until an ordered winner exists.
        return existing is None
    if existing is None:
        return True
    if existing["source_object_id"] == source_object_id:
        return True

    existing_rank = (
        existing["source_period_end"],
        int(existing["source_precedence"]),
        int(existing["source_sequence"]),
    )
    if incoming_order.rank > existing_rank:
        return True
    if incoming_order.rank < existing_rank:
        return False
    raise RuntimeError(
        "CIPO current projection encountered different source objects with the same "
        "manifest ordering rank; fix source_period_end/source_precedence/source_sequence"
    )


def upsert_current_source_order(
    cur,
    *,
    record_key: str,
    application_number: str,
    extension_counter: str,
    operation_category: str,
    source_present: bool,
    order: CipoSourceOrder,
) -> None:
    cur.execute(
        """
        INSERT INTO trademark_ca.current_source_order (
            record_key, application_number, extension_counter, source_object_id,
            source_id, manifest_id, manifest_key, source_period_end,
            source_precedence, source_sequence, part_sequence,
            operation_category, source_present, observed_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
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
            observed_at = now()
        """,
        (
            record_key,
            application_number,
            extension_counter,
            order.source_object_id,
            order.source_id,
            order.manifest_id,
            order.manifest_key,
            order.source_period_end,
            order.source_precedence,
            order.source_sequence,
            order.part_sequence,
            operation_category,
            source_present,
        ),
    )
