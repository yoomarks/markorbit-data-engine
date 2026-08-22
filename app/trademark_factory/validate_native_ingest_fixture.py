from __future__ import annotations

import tempfile
from pathlib import Path

from app.db import postgres_conn
from app.global_trademarks.ingest_runs import get_ingest_run_state
from app.global_trademarks.migrations import assert_global_trademark_schema
from app.global_trademarks.source_objects import register_source_object
from app.trademark_factory.mapping import MappingContract, MappingRule, SelectorKind
from app.trademark_factory.native_ingest import (
    NATIVE_INGEST_RUNNER_VERSION,
    NativeRecordEnvelope,
    run_native_ingest,
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
_PIPELINE_ID = "XX_NATIVE_INGEST_FIXTURE_V1"
_GAP_PIPELINE_ID = "XX_NATIVE_INGEST_GAP_FIXTURE_V1"


def _contract(domain: ObservationDomain, rules: tuple[tuple[str, str, bool], ...]) -> MappingContract:
    return MappingContract(
        jurisdiction="XX",
        source_id=_SOURCE_ID,
        version="XX_MAPPING_V1",
        rules=tuple(
            MappingRule(
                selector_kind=SelectorKind.JSON_POINTER,
                source_selector=selector,
                domain=domain,
                target_field=target,
                required=required,
            )
            for selector, target, required in rules
        ),
    )


def _bundle() -> NativeStoreBundle:
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
                        NativeColumn("mark_text", NativeSqlType.TEXT, nullable=False),
                    ),
                ),
                contract=_contract(
                    ObservationDomain.RECORD,
                    (
                        ("/application", "application_number", True),
                        ("/mark", "mark_text", True),
                    ),
                ),
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
                contract=_contract(
                    ObservationDomain.PARTY,
                    (
                        ("/application", "application_number", True),
                        ("/owner", "party_name", True),
                    ),
                ),
            ),
        ),
    )


def _records(count: int = 5) -> tuple[NativeRecordEnvelope, ...]:
    return tuple(
        NativeRecordEnvelope(
            source_index=index,
            record_key=f"XX-{index:04d}",
            native={
                "application": f"{index:04d}",
                "mark": f"MARK {index}",
                "owner": f"Owner {index}",
            },
        )
        for index in range(1, count + 1)
    )


def _cleanup(source_object_ids: tuple[object, ...]) -> None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
            cur.execute(
                "DELETE FROM acquisition.global_trademark_ingest_run WHERE source_object_id = ANY(%s)",
                (list(source_object_ids),),
            )
            cur.execute(
                "DELETE FROM acquisition.global_trademark_source_object WHERE object_id = ANY(%s)",
                (list(source_object_ids),),
            )
        conn.commit()


def main() -> int:
    assert_global_trademark_schema()
    bundle = _bundle()
    assert bundle.validate() == (), bundle.validate()

    source_object_ids: list[object] = []
    try:
        with tempfile.TemporaryDirectory(prefix="native-ingest-runner-") as temporary:
            root = Path(temporary)
            source_path = root / "records.json"
            source_path.write_text('{"fixture":"native-ingest-v1"}\n', encoding="utf-8")
            source_object_id = register_source_object(
                jurisdiction="XX",
                source_id=_SOURCE_ID,
                path=source_path,
                metadata={"fixture": "native-ingest-runner"},
            )
            source_object_ids.append(source_object_id)

            with postgres_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
                    install_native_store_bundle(cur, bundle)
                conn.commit()

            first = run_native_ingest(
                source_object_id=source_object_id,
                bundle=bundle,
                pipeline_id=_PIPELINE_ID,
                parser_version="XX_PARSER_V1",
                records=_records(),
                batch_size=2,
                max_records=2,
            )
            assert first.status == "PARTIAL"
            assert first.processed_records == 2
            assert first.cumulative_records == 2
            assert first.checkpoint == 2
            assert first.inserted_observations == 4

            second = run_native_ingest(
                source_object_id=source_object_id,
                bundle=bundle,
                pipeline_id=_PIPELINE_ID,
                parser_version="XX_PARSER_V1",
                records=_records(),
                batch_size=2,
                max_records=2,
            )
            assert second.status == "PARTIAL"
            assert second.processed_records == 2
            assert second.cumulative_records == 4
            assert second.checkpoint == 4

            third = run_native_ingest(
                source_object_id=source_object_id,
                bundle=bundle,
                pipeline_id=_PIPELINE_ID,
                parser_version="XX_PARSER_V1",
                records=_records(),
                batch_size=2,
                max_records=2,
            )
            assert third.status == "COMPLETE"
            assert third.processed_records == 1
            assert third.cumulative_records == 5
            assert third.checkpoint == 5

            state = get_ingest_run_state(
                source_object_id=source_object_id,
                pipeline_id=_PIPELINE_ID,
            )
            assert state is not None and state.complete
            assert state.checkpoint == 5
            assert state.rows_committed == 5

            replay_complete = run_native_ingest(
                source_object_id=source_object_id,
                bundle=bundle,
                pipeline_id=_PIPELINE_ID,
                parser_version="XX_PARSER_V1",
                records=(),
            )
            assert replay_complete.status == "COMPLETE"
            assert replay_complete.processed_records == 0
            assert replay_complete.cumulative_records == 5

            with postgres_conn() as conn:
                with conn.cursor() as cur:
                    for table in ("record_observation", "party_observation"):
                        cur.execute(f"SELECT COUNT(*) AS count FROM {_SCHEMA}.{table}")
                        assert cur.fetchone()["count"] == 5

            drift_blocked = False
            try:
                run_native_ingest(
                    source_object_id=source_object_id,
                    bundle=bundle,
                    pipeline_id=_PIPELINE_ID,
                    parser_version="XX_PARSER_V2",
                    records=_records(),
                )
            except RuntimeError as exc:
                drift_blocked = "native ingest contract changed" in str(exc)
            assert drift_blocked

            gap_path = root / "gap.json"
            gap_path.write_text('{"fixture":"native-ingest-gap"}\n', encoding="utf-8")
            gap_source_object_id = register_source_object(
                jurisdiction="XX",
                source_id=_SOURCE_ID,
                path=gap_path,
                metadata={"fixture": "native-ingest-gap"},
            )
            source_object_ids.append(gap_source_object_id)
            gap_records = (
                NativeRecordEnvelope(
                    source_index=1,
                    record_key="XX-GAP-1",
                    native={"application": "G1", "mark": "GAP 1", "owner": "Owner G1"},
                ),
                NativeRecordEnvelope(
                    source_index=3,
                    record_key="XX-GAP-3",
                    native={"application": "G3", "mark": "GAP 3", "owner": "Owner G3"},
                ),
            )
            gap_blocked = False
            try:
                run_native_ingest(
                    source_object_id=gap_source_object_id,
                    bundle=bundle,
                    pipeline_id=_GAP_PIPELINE_ID,
                    parser_version="XX_PARSER_V1",
                    records=gap_records,
                    batch_size=10,
                )
            except RuntimeError as exc:
                gap_blocked = "resume gap" in str(exc)
            assert gap_blocked
            gap_state = get_ingest_run_state(
                source_object_id=gap_source_object_id,
                pipeline_id=_GAP_PIPELINE_ID,
            )
            assert gap_state is not None and gap_state.status == "FAILED"

        print(
            {
                "status": "PASS",
                "native_ingest_runner_version": NATIVE_INGEST_RUNNER_VERSION,
                "bounded_resume": True,
                "one_shot_equivalent_record_count": 5,
                "multi_domain_observations": True,
                "complete_replay_noop": True,
                "contract_drift_blocked": True,
                "source_index_gap_blocked": True,
                "ddl_implicit": False,
                "current_projection_implemented": False,
                "legal_conclusion": False,
            }
        )
        return 0
    finally:
        if source_object_ids:
            _cleanup(tuple(source_object_ids))


if __name__ == "__main__":
    raise SystemExit(main())
