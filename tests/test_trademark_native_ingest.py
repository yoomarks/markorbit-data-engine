import uuid

import pytest

from app.trademark_factory.mapping import MappingContract, MappingRule, SelectorKind
from app.trademark_factory.native_ingest import (
    NATIVE_INGEST_EXECUTOR_VERSION,
    NativeIngestResult,
    NativeRecordEnvelope,
    native_ingest_lineage_sha256,
)
from app.trademark_factory.store_bundle import NativeStoreBundle, StoreBinding
from app.trademark_framework.contracts import ObservationDomain
from app.trademark_framework.native_store import NativeColumn, NativeSqlType, ObservationTableSpec


def _bundle(mapping_version: str = "XX_MAPPING_V1") -> NativeStoreBundle:
    contract = MappingContract(
        jurisdiction="XX",
        source_id="XX_NATIVE_INGEST_FIXTURE",
        version=mapping_version,
        rules=(
            MappingRule(
                selector_kind=SelectorKind.JSON_POINTER,
                source_selector="/application_number",
                domain=ObservationDomain.RECORD,
                target_field="application_number",
                required=True,
            ),
            MappingRule(
                selector_kind=SelectorKind.JSON_POINTER,
                source_selector="/mark_text",
                domain=ObservationDomain.RECORD,
                target_field="mark_text",
            ),
        ),
    )
    return NativeStoreBundle(
        jurisdiction="XX",
        source_id="XX_NATIVE_INGEST_FIXTURE",
        store_schema="trademark_native_ingest_fixture",
        bindings=(
            StoreBinding(
                binding_id="record",
                spec=ObservationTableSpec(
                    schema_name="trademark_native_ingest_fixture",
                    table_name="record_observation",
                    domain=ObservationDomain.RECORD,
                    native_columns=(
                        NativeColumn("application_number", NativeSqlType.TEXT, nullable=False),
                        NativeColumn("mark_text", NativeSqlType.TEXT),
                    ),
                ),
                contract=contract,
            ),
        ),
    )


def test_native_record_envelope_validation() -> None:
    assert not NativeRecordEnvelope(
        source_index=1,
        record_key="XX-1",
        native={"application_number": "1"},
    ).validate()
    assert NativeRecordEnvelope(source_index=0, record_key="", native={}).validate()


def test_native_ingest_lineage_is_deterministic_and_version_sensitive() -> None:
    first = native_ingest_lineage_sha256(_bundle(), "XX_PARSER_V1")
    repeated = native_ingest_lineage_sha256(_bundle(), "XX_PARSER_V1")
    changed_mapping = native_ingest_lineage_sha256(_bundle("XX_MAPPING_V2"), "XX_PARSER_V1")
    changed_parser = native_ingest_lineage_sha256(_bundle(), "XX_PARSER_V2")

    assert first == repeated
    assert len(first) == 64
    assert first != changed_mapping
    assert first != changed_parser


def test_native_ingest_lineage_requires_parser_version() -> None:
    with pytest.raises(ValueError, match="parser_version"):
        native_ingest_lineage_sha256(_bundle(), "")


def test_native_ingest_result_contract() -> None:
    result = NativeIngestResult(
        run_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        status="COMPLETE",
        processed_records=2,
        inserted_observations=2,
        replayed_observations=0,
        checkpoint=2,
        cumulative_records=2,
        bounded=True,
    )
    assert result.complete is True
    assert result.as_dict()["executor_version"] == NATIVE_INGEST_EXECUTOR_VERSION
    assert result.as_dict()["bounded"] is True
