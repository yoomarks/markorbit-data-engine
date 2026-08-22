from __future__ import annotations

import tempfile
from pathlib import Path

from app.db import postgres_conn
from app.global_trademarks.migrations import assert_global_trademark_schema
from app.global_trademarks.source_objects import register_source_object
from app.trademark_factory.mapping import MappingContract, MappingRule, SelectorKind
from app.trademark_factory.writer import (
    MAPPED_OBSERVATION_WRITER_VERSION,
    append_mapped_observation,
)
from app.trademark_framework.contracts import ObservationDomain
from app.trademark_framework.native_store import (
    NativeColumn,
    NativeSqlType,
    ObservationTableSpec,
    install_observation_table,
)


_SCHEMA = "trademark_mapped_writer_fixture"


def _spec() -> ObservationTableSpec:
    return ObservationTableSpec(
        schema_name=_SCHEMA,
        table_name="party_observation",
        domain=ObservationDomain.PARTY,
        native_columns=(
            NativeColumn("application_number", NativeSqlType.TEXT, nullable=False),
            NativeColumn("party_name", NativeSqlType.TEXT, nullable=False),
            NativeColumn("party_country", NativeSqlType.TEXT),
        ),
    )


def _contract(version: str) -> MappingContract:
    return MappingContract(
        jurisdiction="XX",
        source_id="XX_MAPPED_WRITER_FIXTURE",
        version=version,
        rules=(
            MappingRule(
                selector_kind=SelectorKind.JSON_POINTER,
                source_selector="/application/number",
                domain=ObservationDomain.PARTY,
                target_field="application_number",
                required=True,
            ),
            MappingRule(
                selector_kind=SelectorKind.JSON_POINTER,
                source_selector="/owner/name",
                domain=ObservationDomain.PARTY,
                target_field="party_name",
                required=True,
            ),
            MappingRule(
                selector_kind=SelectorKind.JSON_POINTER,
                source_selector="/owner/country",
                domain=ObservationDomain.PARTY,
                target_field="party_country",
            ),
        ),
    )


def main() -> int:
    assert_global_trademark_schema()
    spec = _spec()

    with tempfile.TemporaryDirectory(prefix="mapped-writer-fixture-") as temporary:
        source_path = Path(temporary) / "xx-page-0001.json"
        source_path.write_text(
            '{"application":{"number":"1001"},"owner":{"name":"Example Owner","country":"US"}}\n',
            encoding="utf-8",
        )
        source_object_id = register_source_object(
            jurisdiction="XX",
            source_id="XX_MAPPED_WRITER_FIXTURE",
            path=source_path,
            metadata={"fixture": "mapped-writer"},
        )

        native = {
            "application": {"number": "1001"},
            "owner": {"name": "Example Owner", "country": "US"},
        }

        with postgres_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
                install_observation_table(cur, spec)

                inserted = append_mapped_observation(
                    cur,
                    spec,
                    contract=_contract("XX_MAPPING_V1"),
                    native=native,
                    record_key="XX-1001",
                    source_object_id=source_object_id,
                    source_index=1,
                    parser_version="XX_PARSER_V1",
                )
                assert inserted is True

                replay = append_mapped_observation(
                    cur,
                    spec,
                    contract=_contract("XX_MAPPING_V1"),
                    native=native,
                    record_key="XX-1001",
                    source_object_id=source_object_id,
                    source_index=1,
                    parser_version="XX_PARSER_V1",
                )
                assert replay is False

                drift_blocked = False
                try:
                    append_mapped_observation(
                        cur,
                        spec,
                        contract=_contract("XX_MAPPING_V1"),
                        native={
                            "application": {"number": "1001"},
                            "owner": {"name": "Different Owner", "country": "US"},
                        },
                        record_key="XX-1001",
                        source_object_id=source_object_id,
                        source_index=1,
                        parser_version="XX_PARSER_V1",
                    )
                except RuntimeError as exc:
                    drift_blocked = "nondeterministic native observation replay" in str(exc)
                assert drift_blocked

                v2_inserted = append_mapped_observation(
                    cur,
                    spec,
                    contract=_contract("XX_MAPPING_V2"),
                    native={
                        "application": {"number": "1001"},
                        "owner": {"name": "Example Owner", "country": "USA"},
                    },
                    record_key="XX-1001",
                    source_object_id=source_object_id,
                    source_index=1,
                    parser_version="XX_PARSER_V1",
                )
                assert v2_inserted is True

                cur.execute(
                    f"""
                    SELECT record_key, source_object_id, source_index,
                           parser_version, mapping_version,
                           application_number, party_name, party_country,
                           source_payload
                    FROM {_SCHEMA}.party_observation
                    ORDER BY mapping_version
                    """
                )
                rows = cur.fetchall()
                assert len(rows) == 2
                assert rows[0]["record_key"] == "XX-1001"
                assert rows[0]["source_object_id"] == source_object_id
                assert rows[0]["parser_version"] == "XX_PARSER_V1"
                assert rows[0]["mapping_version"] == "XX_MAPPING_V1"
                assert rows[0]["party_name"] == "Example Owner"
                assert rows[0]["party_country"] == "US"
                assert rows[0]["source_payload"] == native
                assert rows[1]["mapping_version"] == "XX_MAPPING_V2"
                assert rows[1]["party_country"] == "USA"

                cur.execute(f"DROP SCHEMA {_SCHEMA} CASCADE")
            conn.commit()

    print(
        {
            "status": "PASS",
            "mapped_observation_writer_version": MAPPED_OBSERVATION_WRITER_VERSION,
            "declarative_json_mapping": True,
            "source_native_record_key_preserved": True,
            "source_object_lineage_preserved": True,
            "identical_replay_idempotent": True,
            "same_lineage_drift_blocked": True,
            "reviewed_mapping_version_can_append_distinct_evidence": True,
            "legal_conclusion": False,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
