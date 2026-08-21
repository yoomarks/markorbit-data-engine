from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

from app.db import postgres_conn
from app.global_trademarks.execution import (
    ExecutionAlreadyRunning,
    global_trademark_execution_lock,
)
from app.global_trademarks.manifest import (
    attach_manifest_object,
    source_manifest,
    upsert_source_manifest,
)
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
        root = Path(temporary)
        object_ids = []
        for part in (1, 2):
            path = root / f"source-{part}.dat"
            path.write_bytes(f"official-source-fixture-{part}\n".encode())
            object_ids.append(
                register_source_object(
                    jurisdiction="CA",
                    source_id="CIPO_GLOBAL_2025_06_14",
                    path=path,
                    source_period_start=date(2025, 6, 14),
                    source_period_end=date(2025, 6, 14),
                    metadata={"fixture_part": part},
                )
            )

        first_object = object_ids[0]
        repeated = register_source_object(
            jurisdiction="CA",
            source_id="CIPO_GLOBAL_2025_06_14",
            path=root / "source-1.dat",
            metadata={"fixture_replayed": True},
        )
        assert repeated == first_object

        manifest = upsert_source_manifest(
            jurisdiction="CA",
            source_id="CIPO_GLOBAL_2025_06_14",
            manifest_key="CIPO_GLOBAL_2025_06_14",
            source_period_start=date(2025, 6, 14),
            source_period_end=date(2025, 6, 14),
            source_sequence=1,
            source_precedence=100,
            expected_objects=2,
            parser_version="CIPO_ST96_CORE_V1",
            mapping_version="COUNTRY_NATIVE_V1",
        )
        assert manifest.objects_complete is False
        manifest = attach_manifest_object(
            manifest_id=manifest.manifest_id,
            source_object_id=object_ids[0],
            part_sequence=1,
        )
        assert manifest.objects_complete is False
        manifest = attach_manifest_object(
            manifest_id=manifest.manifest_id,
            source_object_id=object_ids[1],
            part_sequence=2,
        )
        assert manifest.objects_complete is True
        assert source_manifest(manifest.manifest_id) == manifest

        with postgres_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT source_period_start, source_period_end, metadata
                    FROM acquisition.global_trademark_source_object
                    WHERE object_id = %s
                    """,
                    (first_object,),
                )
                row = cur.fetchone()
        assert row["source_period_start"] == date(2025, 6, 14)
        assert row["source_period_end"] == date(2025, 6, 14)
        assert row["metadata"] == {"fixture_part": 1, "fixture_replayed": True}

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
            "dataset_manifest_complete": True,
            "duplicate_execution_blocked": True,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
