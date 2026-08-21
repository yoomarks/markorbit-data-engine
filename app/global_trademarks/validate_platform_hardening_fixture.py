from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

from app.db import postgres_conn
from app.global_trademarks.execution import (
    ExecutionAlreadyRunning,
    global_trademark_execution_lock,
)
from app.global_trademarks.manifest import source_manifest, upsert_source_manifest
from app.global_trademarks.migrations import (
    GLOBAL_TRADEMARK_SCHEMA_VERSION,
    assert_global_trademark_schema,
    migrate_global_trademark_schema,
)
from app.global_trademarks.source_objects import register_source_object


def main() -> int:
    first = migrate_global_trademark_schema()
    second = migrate_global_trademark_schema()
    assert first.ready and second.ready
    assert first.expected_version == GLOBAL_TRADEMARK_SCHEMA_VERSION
    assert_global_trademark_schema()

    with tempfile.TemporaryDirectory(prefix="global-platform-hardening-") as temporary:
        path = Path(temporary) / "source.dat"
        path.write_bytes(b"official-source-fixture\n")
        source_object_id = register_source_object(
            jurisdiction="CA",
            source_id="CIPO_GLOBAL_2025_06_14",
            path=path,
            source_period_start=date(2025, 6, 14),
            source_period_end=date(2025, 6, 14),
            metadata={"fixture_a": True},
        )
        repeated = register_source_object(
            jurisdiction="CA",
            source_id="CIPO_GLOBAL_2025_06_14",
            path=path,
            metadata={"fixture_b": True},
        )
        assert repeated == source_object_id

        manifest = upsert_source_manifest(
            source_object_id=source_object_id,
            source_sequence=1,
            source_precedence=100,
            expected_parts=161,
            received_parts=161,
            parser_version="CIPO_ST96_CORE_V1",
            mapping_version="COUNTRY_NATIVE_V1",
        )
        assert manifest.parts_complete is True
        assert source_manifest(source_object_id) == manifest

        with postgres_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT source_period_start, source_period_end, metadata
                    FROM acquisition.global_trademark_source_object
                    WHERE object_id = %s
                    """,
                    (source_object_id,),
                )
                row = cur.fetchone()
        assert row["source_period_start"] == date(2025, 6, 14)
        assert row["source_period_end"] == date(2025, 6, 14)
        assert row["metadata"] == {"fixture_a": True, "fixture_b": True}

    scope = "CA:CIPO_GLOBAL_2025_06_14:fixture"
    with global_trademark_execution_lock(scope):
        blocked = False
        try:
            with global_trademark_execution_lock(scope):
                pass
        except ExecutionAlreadyRunning:
            blocked = True
        assert blocked is True

    with global_trademark_execution_lock(scope):
        pass

    print(
        {
            "status": "PASS",
            "schema_version": GLOBAL_TRADEMARK_SCHEMA_VERSION,
            "migration_idempotent": True,
            "source_period_preserved": True,
            "manifest_complete": True,
            "duplicate_execution_blocked": True,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
