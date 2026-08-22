from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

from app.db import postgres_conn
from app.global_trademarks.ingest_runs import get_ingest_run_state
from app.global_trademarks.migrations import migrate_global_trademark_schema
from app.global_trademarks.source_objects import register_source_object
from app.trademark_factory.mapping import MappingContract, MappingRule, SelectorKind
from app.trademark_factory.native_ingest import (
    NATIVE_INGEST_EXECUTOR_VERSION,
    NativeRecordEnvelope,
    execute_native_ingest,
)
from app.trademark_factory.store_bundle import (
    NativeStoreBundle,
    StoreBinding,
    install_native_store_bundle,
)
from app.trademark_framework.contracts import ObservationDomain
from app.trademark_framework.native_store import NativeColumn, NativeSqlType, ObservationTableSpec


_SCHEMA = "trademark_native_ingest_fixture"
_SOURCE_ID = "XX_NATIVE_INGEST_FIXTURE"


def _contract(domain: ObservationDomain, version: str = "XX_MAPPING_V1") -> MappingContract:
    if domain == ObservationDomain.RECORD:
        rules = (
            MappingRule(
                selector_kind=SelectorKind.JSON_POINTER,
                source_selector="/application_number",
                domain=domain,
                target_field="application_number",
                required=True,
            ),
            MappingRule(
                selector_kind=SelectorKind.JSON_POINTER,
                source_selector="/mark_text",
                domain=domain,
                target_field="mark_text",
            ),
        )
    else:
        rules = (
            MappingRule(
                selector_kind=SelectorKind.JSON_POINTER,
                source_selector="/application_number",
                domain=domain,
                target_field="application_number",
                required=True,
            ),
            MappingRule(
                selector_kind=SelectorKind.JSON_POINTER,
                source_selector="/owner/name",
                domain=domain,
                target_field="party_name",
                required=True,
            ),
        )
    return MappingContract(
        jurisdiction="XX",
        source_id=_SOURCE_ID,
        version=version,
        rules=rules,
    )


def _bundle(mapping_version: str = "XX_MAPPING_V1") -> NativeStoreBundle:
    return NativeStoreBundle(
        jurisdiction="XX",
        source_id=_SOURCE_ID,
        store_schema=_SCHEMA,
        bindings=(
            StoreBinding(
                binding_id="record",
                spec=ObservationTableSpec(
                    schema_name=_SCHEMA,
                    table_name="record_observation",
                    domain=ObservationDomain.RECORD,
                    native_columns=(
                        NativeColumn("application_number", NativeSqlType.TEXT, nullable=False),
                        NativeColumn("mark_text", NativeSqlType.TEXT),
                    ),
                ),
                contract=_contract(ObservationDomain.RECORD, mapping_version),
            ),
            StoreBinding(
                binding_id="party",
                spec=ObservationTableSpec(
                    schema_name=_SCHEMA,
                    table_name="party_observation",
                    domain=ObservationDomain.PARTY,
                    native_columns=(
                        NativeColumn("application_number", NativeSqlType.TEXT, nullable=False),
                        NativeColumn("party_name", NativeSqlType.TEXT, nullable=False),
                    ),
                ),
                contract=_contract(ObservationDomain.PARTY, mapping_version),
            ),
        ),
    )


def _records(count: int = 4) -> tuple[NativeRecordEnvelope, ...]:
    return tuple(
        NativeRecordEnvelope(
            source_index=index,
            record_key=f"XX-{index}",
            native={
                "application_number": str(index),
                "mark_text": f"MARK {index}",
                "owner": {"name": f"Owner {index}"},
            },
        )
        for index in range(1, count + 1)
    )


def _interrupted(records: tuple[NativeRecordEnvelope, ...]) -> Iterator[NativeRecordEnvelope]:
    yield records[0]
    raise RuntimeError("synthetic parser interruption")


def _table_count(table_name: str, source_object_id) -> int:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) AS count FROM {_SCHEMA}.{table_name} WHERE source_object_id = %s",
                (source_object_id,),
            )
            return int(cur.fetchone()["count"])


def main() -> int:
    migration = migrate_global_trademark_schema()
    assert migration.ready

    bundle = _bundle()
    assert not bundle.validate(), bundle.validate()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
            install_native_store_bundle(cur, bundle)
        conn.commit()

    with tempfile.TemporaryDirectory(prefix="native-ingest-fixture-") as temporary:
        root = Path(temporary)
        bounded_path = root / "bounded.json"
        bounded_path.write_text('{"fixture":"bounded"}\n', encoding="utf-8")
        bounded_source = register_source_object(
            jurisdiction="XX",
            source_id=_SOURCE_ID,
            path=bounded_path,
            metadata={"fixture": "native-ingest-bounded"},
        )

        first = execute_native_ingest(
            source_object_id=bounded_source,
            pipeline_id="XX_NATIVE_INGEST_V1",
            bundle=bundle,
            parser_version="XX_PARSER_V1",
            records=_records(),
            batch_size=1,
            max_records=2,
        )
        assert first.status == "PARTIAL"
        assert first.processed_records == 2
        assert first.checkpoint == 2
        assert first.cumulative_records == 2
        assert first.inserted_observations == 4
        assert _table_count("record_observation", bounded_source) == 2
        assert _table_count("party_observation", bounded_source) == 2

        resumed = execute_native_ingest(
            source_object_id=bounded_source,
            pipeline_id="XX_NATIVE_INGEST_V1",
            bundle=bundle,
            parser_version="XX_PARSER_V1",
            records=_records(),
            batch_size=2,
        )
        assert resumed.status == "COMPLETE"
        assert resumed.processed_records == 2
        assert resumed.checkpoint == 4
        assert resumed.cumulative_records == 4
        assert resumed.inserted_observations == 4
        assert _table_count("record_observation", bounded_source) == 4
        assert _table_count("party_observation", bounded_source) == 4

        already_complete = execute_native_ingest(
            source_object_id=bounded_source,
            pipeline_id="XX_NATIVE_INGEST_V1",
            bundle=bundle,
            parser_version="XX_PARSER_V1",
            records=(),
        )
        assert already_complete.status == "ALREADY_COMPLETE"
        assert already_complete.processed_records == 0
        assert already_complete.checkpoint == 4

        lineage_drift_blocked = False
        try:
            execute_native_ingest(
                source_object_id=bounded_source,
                pipeline_id="XX_NATIVE_INGEST_V1",
                bundle=_bundle("XX_MAPPING_V2"),
                parser_version="XX_PARSER_V1",
                records=_records(),
            )
        except RuntimeError as exc:
            lineage_drift_blocked = "lineage changed" in str(exc)
        assert lineage_drift_blocked

        interrupted_path = root / "interrupted.json"
        interrupted_path.write_text('{"fixture":"interrupted"}\n', encoding="utf-8")
        interrupted_source = register_source_object(
            jurisdiction="XX",
            source_id=_SOURCE_ID,
            path=interrupted_path,
            metadata={"fixture": "native-ingest-interrupted"},
        )
        interruption_seen = False
        try:
            execute_native_ingest(
                source_object_id=interrupted_source,
                pipeline_id="XX_NATIVE_INGEST_INTERRUPT_V1",
                bundle=bundle,
                parser_version="XX_PARSER_V1",
                records=_interrupted(_records(3)),
                batch_size=1,
            )
        except RuntimeError as exc:
            interruption_seen = "synthetic parser interruption" in str(exc)
        assert interruption_seen
        failed_state = get_ingest_run_state(
            source_object_id=interrupted_source,
            pipeline_id="XX_NATIVE_INGEST_INTERRUPT_V1",
        )
        assert failed_state is not None
        assert failed_state.status == "FAILED"
        assert failed_state.checkpoint == 1
        assert failed_state.rows_committed == 1
        assert _table_count("record_observation", interrupted_source) == 1

        interruption_resume = execute_native_ingest(
            source_object_id=interrupted_source,
            pipeline_id="XX_NATIVE_INGEST_INTERRUPT_V1",
            bundle=bundle,
            parser_version="XX_PARSER_V1",
            records=_records(3),
            batch_size=1,
        )
        assert interruption_resume.status == "COMPLETE"
        assert interruption_resume.processed_records == 2
        assert interruption_resume.checkpoint == 3
        assert interruption_resume.cumulative_records == 3
        assert _table_count("record_observation", interrupted_source) == 3
        assert _table_count("party_observation", interrupted_source) == 3

        gap_path = root / "gap.json"
        gap_path.write_text('{"fixture":"gap"}\n', encoding="utf-8")
        gap_source = register_source_object(
            jurisdiction="XX",
            source_id=_SOURCE_ID,
            path=gap_path,
            metadata={"fixture": "native-ingest-gap"},
        )
        gap_blocked = False
        try:
            execute_native_ingest(
                source_object_id=gap_source,
                pipeline_id="XX_NATIVE_INGEST_GAP_V1",
                bundle=bundle,
                parser_version="XX_PARSER_V1",
                records=(
                    _records(3)[0],
                    _records(3)[2],
                ),
                batch_size=1,
            )
        except RuntimeError as exc:
            gap_blocked = "not contiguous" in str(exc)
        assert gap_blocked
        gap_state = get_ingest_run_state(
            source_object_id=gap_source,
            pipeline_id="XX_NATIVE_INGEST_GAP_V1",
        )
        assert gap_state is not None
        assert gap_state.status == "FAILED"
        assert gap_state.checkpoint == 1

        wrong_source_path = root / "wrong-source.json"
        wrong_source_path.write_text('{"fixture":"wrong-source"}\n', encoding="utf-8")
        wrong_source = register_source_object(
            jurisdiction="XX",
            source_id="XX_DIFFERENT_SOURCE",
            path=wrong_source_path,
        )
        identity_blocked = False
        try:
            execute_native_ingest(
                source_object_id=wrong_source,
                pipeline_id="XX_NATIVE_INGEST_WRONG_SOURCE_V1",
                bundle=bundle,
                parser_version="XX_PARSER_V1",
                records=_records(1),
            )
        except RuntimeError as exc:
            identity_blocked = "source_id does not match" in str(exc)
        assert identity_blocked

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA {_SCHEMA} CASCADE")
        conn.commit()

    print(
        {
            "status": "PASS",
            "native_ingest_executor_version": NATIVE_INGEST_EXECUTOR_VERSION,
            "bounded_partial_resume": True,
            "interruption_resume": True,
            "bundle_atomic_source_records": True,
            "lineage_drift_blocked": True,
            "source_index_drift_blocked": True,
            "source_identity_verified": True,
            "current_projection_implemented": False,
            "legal_conclusion": False,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
