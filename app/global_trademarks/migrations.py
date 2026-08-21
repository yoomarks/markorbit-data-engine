from __future__ import annotations

from dataclasses import dataclass

from app.db import postgres_conn
from app.global_trademarks.ingest_schema import ensure_seed_ingest_schema


GLOBAL_TRADEMARK_SCHEMA_COMPONENT = "GLOBAL_TRADEMARK"
GLOBAL_TRADEMARK_SCHEMA_VERSION = "GLOBAL_TM_SCHEMA_V1"

_MANIFEST_SQL = """
CREATE TABLE IF NOT EXISTS acquisition.global_trademark_manifest (
    manifest_id uuid PRIMARY KEY,
    jurisdiction text NOT NULL,
    source_id text NOT NULL,
    manifest_key text NOT NULL,
    source_period_start date,
    source_period_end date,
    source_sequence bigint NOT NULL DEFAULT 0,
    source_precedence bigint NOT NULL DEFAULT 0,
    expected_objects integer,
    predecessor_manifest_key text,
    baseline_manifest_key text,
    parser_version text NOT NULL DEFAULT '',
    mapping_version text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (jurisdiction, source_id, manifest_key),
    CHECK (source_sequence >= 0),
    CHECK (source_precedence >= 0),
    CHECK (expected_objects IS NULL OR expected_objects >= 0),
    CHECK (
        source_period_start IS NULL
        OR source_period_end IS NULL
        OR source_period_end >= source_period_start
    )
);
CREATE INDEX IF NOT EXISTS idx_global_trademark_manifest_order
    ON acquisition.global_trademark_manifest (
        jurisdiction, source_id, source_sequence, source_precedence
    );

CREATE TABLE IF NOT EXISTS acquisition.global_trademark_manifest_object (
    manifest_id uuid NOT NULL
        REFERENCES acquisition.global_trademark_manifest(manifest_id) ON DELETE CASCADE,
    source_object_id uuid NOT NULL
        REFERENCES acquisition.global_trademark_source_object(object_id) ON DELETE CASCADE,
    part_sequence integer,
    object_role text NOT NULL DEFAULT 'PRIMARY',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (manifest_id, source_object_id),
    UNIQUE (manifest_id, part_sequence),
    CHECK (part_sequence IS NULL OR part_sequence >= 1)
);
CREATE INDEX IF NOT EXISTS idx_global_trademark_manifest_object_source
    ON acquisition.global_trademark_manifest_object (source_object_id);
"""


@dataclass(frozen=True, slots=True)
class GlobalTrademarkMigrationStatus:
    installed_version: str | None
    expected_version: str
    ready: bool
    manifest_relation_ready: bool
    manifest_object_relation_ready: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "component": GLOBAL_TRADEMARK_SCHEMA_COMPONENT,
            "installed_version": self.installed_version,
            "expected_version": self.expected_version,
            "ready": self.ready,
            "manifest_relation_ready": self.manifest_relation_ready,
            "manifest_object_relation_ready": self.manifest_object_relation_ready,
        }


def migrate_global_trademark_schema() -> GlobalTrademarkMigrationStatus:
    """Install/upgrade the additive global-trademark schema and record its version.

    This command is the operator migration boundary. It changes schema only; it never
    downloads or ingests a source file and it does not touch the CN/US fact stores.
    """
    ensure_seed_ingest_schema()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_MANIFEST_SQL)
            cur.execute(
                """
                INSERT INTO control.schema_version(component, version)
                VALUES (%s, %s)
                ON CONFLICT (component)
                DO UPDATE SET version = EXCLUDED.version, applied_at = now()
                """,
                (GLOBAL_TRADEMARK_SCHEMA_COMPONENT, GLOBAL_TRADEMARK_SCHEMA_VERSION),
            )
        conn.commit()
    return global_trademark_migration_status()


def global_trademark_migration_status() -> GlobalTrademarkMigrationStatus:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT version FROM control.schema_version WHERE component = %s",
                (GLOBAL_TRADEMARK_SCHEMA_COMPONENT,),
            )
            row = cur.fetchone()
            cur.execute(
                """
                SELECT
                    to_regclass('acquisition.global_trademark_manifest') AS manifest_relation,
                    to_regclass('acquisition.global_trademark_manifest_object')
                        AS manifest_object_relation
                """
            )
            relations = cur.fetchone()
    installed = str(row["version"]) if row else None
    manifest_ready = relations["manifest_relation"] is not None
    manifest_object_ready = relations["manifest_object_relation"] is not None
    return GlobalTrademarkMigrationStatus(
        installed_version=installed,
        expected_version=GLOBAL_TRADEMARK_SCHEMA_VERSION,
        ready=(
            installed == GLOBAL_TRADEMARK_SCHEMA_VERSION
            and manifest_ready
            and manifest_object_ready
        ),
        manifest_relation_ready=manifest_ready,
        manifest_object_relation_ready=manifest_object_ready,
    )


def assert_global_trademark_schema() -> None:
    status = global_trademark_migration_status()
    if status.ready:
        return
    raise RuntimeError(
        "Global trademark schema is not at the required operator version. "
        f"installed={status.installed_version!r}, expected={status.expected_version!r}, "
        f"manifest_relation_ready={status.manifest_relation_ready}, "
        f"manifest_object_relation_ready={status.manifest_object_relation_ready}. "
        "Run `python -m app.global_trademarks.cli migrate` before --apply ingestion."
    )
