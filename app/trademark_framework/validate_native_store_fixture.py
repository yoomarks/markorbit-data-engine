from __future__ import annotations

import tempfile
import uuid
from datetime import date
from pathlib import Path

from app.db import postgres_conn
from app.global_trademarks.migrations import assert_global_trademark_schema
from app.global_trademarks.source_objects import register_source_object
from app.trademark_framework.contracts import ObservationDomain
from app.trademark_framework.native_store import (
    NATIVE_STORE_PRIMITIVES_VERSION,
    NativeColumn,
    NativeSqlType,
    ObservationRow,
    ObservationTableSpec,
    append_observation,
    install_observation_table,
    observation_row_hash,
)


_SCHEMA = "trademark_factory_fixture"


def _spec() -> ObservationTableSpec:
    return ObservationTableSpec(
        schema_name=_SCHEMA,
        table_name="event_observation",
        domain=ObservationDomain.EVENT,
        native_columns=(
            NativeColumn("application_number", NativeSqlType.TEXT, nullable=False),
            NativeColumn("event_code", NativeSqlType.TEXT, nullable=False),
            NativeColumn("event_text", NativeSqlType.TEXT),
            NativeColumn("class_numbers", NativeSqlType.TEXT_ARRAY),
            NativeColumn("source_meta", NativeSqlType.JSONB),
        ),
    )


def main() -> int:
    # The dedicated workflow deliberately runs this after the consolidated country-store
    # suite so the legacy-upgrade-first invariant remains intact.
    assert_global_trademark_schema()

    spec = _spec()
    assert not spec.validate(), spec.validate()
    statements = spec.create_statements()
    rendered = "\n".join(statements)
    assert "source_row_hash text PRIMARY KEY" in rendered
    assert (
        "source_object_id uuid NOT NULL REFERENCES acquisition.global_trademark_source_object"
        in rendered
    )
    assert "parser_version text NOT NULL" in rendered
    assert "mapping_version text NOT NULL" in rendered
    assert "source_payload jsonb NOT NULL" in rendered
    assert "application_number text NOT NULL" in rendered
    assert (
        "UNIQUE (source_object_id, record_key, source_index, parser_version, mapping_version)"
        in rendered
    )

    invalid = ObservationTableSpec(
        schema_name="bad-schema",
        table_name="event_observation",
        domain=ObservationDomain.EVENT,
        native_columns=(),
    )
    assert invalid.validate()

    collision = ObservationTableSpec(
        schema_name=_SCHEMA,
        table_name="bad_observation",
        domain=ObservationDomain.EVENT,
        native_columns=(NativeColumn("record_key", NativeSqlType.TEXT),),
    )
    assert any("collides" in error for error in collision.validate())

    with tempfile.TemporaryDirectory(prefix="native-store-fixture-") as temporary:
        source_path = Path(temporary) / "xx-events.json"
        source_path.write_text('{"fixture":"native-store"}\n', encoding="utf-8")
        source_object_id = register_source_object(
            jurisdiction="XX",
            source_id="XX_NATIVE_STORE_FIXTURE",
            path=source_path,
            metadata={"fixture": True},
        )

        first = ObservationRow(
            record_key="XX-1001",
            source_object_id=source_object_id,
            source_index=1,
            parser_version="XX_PARSER_V1",
            mapping_version="XX_MAPPING_V1",
            native_values={
                "application_number": "1001",
                "event_code": "PUBLISHED",
                "event_text": "Published by virtual office",
                "class_numbers": ["9", "42"],
                "source_meta": {"language": "en", "event_date": date(2026, 8, 22)},
            },
            source_payload={
                "event": {
                    "code": "PUBLISHED",
                    "sequence": 1,
                    "event_date": date(2026, 8, 22),
                }
            },
        )
        same_different_mapping_order = ObservationRow(
            record_key="XX-1001",
            source_object_id=source_object_id,
            source_index=1,
            parser_version="XX_PARSER_V1",
            mapping_version="XX_MAPPING_V1",
            native_values={
                "source_meta": {"event_date": date(2026, 8, 22), "language": "en"},
                "class_numbers": ["9", "42"],
                "event_text": "Published by virtual office",
                "event_code": "PUBLISHED",
                "application_number": "1001",
            },
            source_payload={
                "event": {
                    "event_date": date(2026, 8, 22),
                    "sequence": 1,
                    "code": "PUBLISHED",
                }
            },
        )
        assert observation_row_hash(spec, first) == observation_row_hash(
            spec, same_different_mapping_order
        )

        newer_mapping = ObservationRow(
            record_key=first.record_key,
            source_object_id=first.source_object_id,
            source_index=first.source_index,
            parser_version=first.parser_version,
            mapping_version="XX_MAPPING_V2",
            native_values=first.native_values,
            source_payload=first.source_payload,
        )
        assert observation_row_hash(spec, first) != observation_row_hash(spec, newer_mapping)

        with postgres_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
                install_observation_table(cur, spec)
                assert append_observation(cur, spec, first) is True
                assert append_observation(cur, spec, same_different_mapping_order) is False

                nondeterministic = ObservationRow(
                    record_key=first.record_key,
                    source_object_id=first.source_object_id,
                    source_index=first.source_index,
                    parser_version=first.parser_version,
                    mapping_version=first.mapping_version,
                    native_values={
                        **dict(first.native_values),
                        "event_text": "Different output from the same parser lineage",
                    },
                    source_payload=first.source_payload,
                )
                replay_drift_blocked = False
                try:
                    append_observation(cur, spec, nondeterministic)
                except RuntimeError as exc:
                    replay_drift_blocked = "nondeterministic native observation replay" in str(exc)
                assert replay_drift_blocked

                changed = ObservationRow(
                    record_key="XX-1001",
                    source_object_id=source_object_id,
                    source_index=2,
                    parser_version="XX_PARSER_V1",
                    mapping_version="XX_MAPPING_V1",
                    native_values={
                        "application_number": "1001",
                        "event_code": "REGISTERED",
                        "event_text": "Registered by virtual office",
                        "class_numbers": ["9", "42"],
                        "source_meta": {"language": "en", "sequence": 2},
                    },
                    source_payload={"event": {"code": "REGISTERED", "sequence": 2}},
                )
                assert append_observation(cur, spec, changed) is True

                cur.execute(
                    f"""
                    SELECT record_key, source_object_id, source_index,
                           parser_version, mapping_version,
                           application_number, event_code, event_text,
                           class_numbers, source_meta, source_payload
                    FROM {_SCHEMA}.event_observation
                    ORDER BY source_index
                    """
                )
                rows = cur.fetchall()
                assert len(rows) == 2
                assert rows[0]["record_key"] == "XX-1001"
                assert rows[0]["source_object_id"] == source_object_id
                assert rows[0]["parser_version"] == "XX_PARSER_V1"
                assert rows[0]["mapping_version"] == "XX_MAPPING_V1"
                assert rows[0]["event_code"] == "PUBLISHED"
                assert rows[0]["class_numbers"] == ["9", "42"]
                assert rows[0]["source_meta"] == {
                    "language": "en",
                    "event_date": "2026-08-22",
                }
                assert rows[0]["source_payload"]["event"]["event_date"] == "2026-08-22"
                assert rows[1]["event_code"] == "REGISTERED"

                missing_required_blocked = False
                try:
                    append_observation(
                        cur,
                        spec,
                        ObservationRow(
                            record_key="XX-1002",
                            source_object_id=source_object_id,
                            source_index=1,
                            native_values={"event_code": "FILED"},
                            source_payload={},
                        ),
                    )
                except ValueError:
                    missing_required_blocked = True
                assert missing_required_blocked

                unknown_column_blocked = False
                try:
                    append_observation(
                        cur,
                        spec,
                        ObservationRow(
                            record_key="XX-1002",
                            source_object_id=source_object_id,
                            source_index=1,
                            native_values={
                                "application_number": "1002",
                                "event_code": "FILED",
                                "invented_legal_status": "VALID",
                            },
                            source_payload={},
                        ),
                    )
                except ValueError:
                    unknown_column_blocked = True
                assert unknown_column_blocked

                cur.execute(f"DROP SCHEMA {_SCHEMA} CASCADE")
            conn.commit()

        assert isinstance(source_object_id, uuid.UUID)

    print(
        {
            "status": "PASS",
            "native_store_primitives_version": NATIVE_STORE_PRIMITIVES_VERSION,
            "country_native_columns_preserved": True,
            "provenance_columns_standardized": True,
            "parser_mapping_lineage_persisted": True,
            "deterministic_replay_idempotent": True,
            "nondeterministic_replay_drift_blocked": True,
            "changed_mapping_version_has_distinct_identity": True,
            "changed_source_sequence_appends_history": True,
            "unknown_native_fields_blocked": True,
            "current_projection_implemented": False,
            "legal_conclusion": False,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
