from __future__ import annotations

import tempfile
from pathlib import Path

from app.db import postgres_conn
from app.global_trademarks.ingest_runs import get_ingest_run_state
from app.global_trademarks.migrations import migrate_global_trademark_schema
from app.global_trademarks.source_objects import register_source_object
from app.trademark_factory.mapping import MappingContract, MappingRule, SelectorKind
from app.trademark_factory.native_ingest import NativeRecordEnvelope, execute_native_ingest
from app.trademark_factory.store_bundle import (
    NativeStoreBundle,
    StoreBinding,
    install_native_store_bundle,
)
from app.trademark_framework.contracts import ObservationDomain
from app.trademark_framework.native_store import NativeColumn, NativeSqlType, ObservationTableSpec


_SCHEMA = "trademark_native_ingest_truncation_fixture"
_SOURCE_ID = "XX_NATIVE_INGEST_TRUNCATION_FIXTURE"
_PIPELINE_ID = "XX_NATIVE_INGEST_TRUNCATION_V1"


def _bundle() -> NativeStoreBundle:
    contract = MappingContract(
        jurisdiction="XX",
        source_id=_SOURCE_ID,
        version="XX_TRUNCATION_MAPPING_V1",
        rules=(
            MappingRule(
                selector_kind=SelectorKind.JSON_POINTER,
                source_selector="/application_number",
                domain=ObservationDomain.RECORD,
                target_field="application_number",
                required=True,
            ),
        ),
    )
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
                    ),
                ),
                contract=contract,
            ),
        ),
    )


def _records(count: int) -> tuple[NativeRecordEnvelope, ...]:
    return tuple(
        NativeRecordEnvelope(
            source_index=index,
            record_key=f"XX-T-{index}",
            native={"application_number": str(index)},
        )
        for index in range(1, count + 1)
    )


def main() -> int:
    assert migrate_global_trademark_schema().ready
    bundle = _bundle()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
            install_native_store_bundle(cur, bundle)
        conn.commit()

    with tempfile.TemporaryDirectory(prefix="native-ingest-truncation-") as temporary:
        path = Path(temporary) / "source.json"
        path.write_text('{"fixture":"truncation"}\n', encoding="utf-8")
        source_object_id = register_source_object(
            jurisdiction="XX",
            source_id=_SOURCE_ID,
            path=path,
        )

        partial = execute_native_ingest(
            source_object_id=source_object_id,
            pipeline_id=_PIPELINE_ID,
            bundle=bundle,
            parser_version="XX_TRUNCATION_PARSER_V1",
            records=_records(3),
            batch_size=1,
            max_records=2,
        )
        assert partial.status == "PARTIAL"
        assert partial.checkpoint == 2

        truncated_replay_blocked = False
        try:
            execute_native_ingest(
                source_object_id=source_object_id,
                pipeline_id=_PIPELINE_ID,
                bundle=bundle,
                parser_version="XX_TRUNCATION_PARSER_V1",
                records=_records(1),
                batch_size=1,
            )
        except RuntimeError as exc:
            truncated_replay_blocked = "ended before the durable checkpoint" in str(exc)
        assert truncated_replay_blocked

        failed = get_ingest_run_state(
            source_object_id=source_object_id,
            pipeline_id=_PIPELINE_ID,
        )
        assert failed is not None
        assert failed.status == "FAILED"
        assert failed.checkpoint == 2
        assert failed.rows_committed == 2

        resumed = execute_native_ingest(
            source_object_id=source_object_id,
            pipeline_id=_PIPELINE_ID,
            bundle=bundle,
            parser_version="XX_TRUNCATION_PARSER_V1",
            records=_records(3),
            batch_size=1,
        )
        assert resumed.status == "COMPLETE"
        assert resumed.processed_records == 1
        assert resumed.checkpoint == 3
        assert resumed.cumulative_records == 3

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA {_SCHEMA} CASCADE")
        conn.commit()

    print(
        {
            "status": "PASS",
            "truncated_full_replay_blocked": True,
            "durable_checkpoint_preserved": True,
            "resume_after_truncation_failure": True,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
