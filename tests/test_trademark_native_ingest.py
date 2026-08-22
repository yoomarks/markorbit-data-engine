from app.trademark_factory.mapping import MappingContract, MappingRule, SelectorKind
from app.trademark_factory.native_ingest import (
    NativeRecordEnvelope,
    native_ingest_contract_hash,
)
from app.trademark_factory.store_bundle import NativeStoreBundle, StoreBinding
from app.trademark_framework.contracts import ObservationDomain
from app.trademark_framework.native_store import NativeColumn, NativeSqlType, ObservationTableSpec


def _binding(*, reverse_rules: bool = False) -> StoreBinding:
    rules = [
        MappingRule(
            selector_kind=SelectorKind.JSON_POINTER,
            source_selector="/application",
            domain=ObservationDomain.RECORD,
            target_field="application_number",
            required=True,
        ),
        MappingRule(
            selector_kind=SelectorKind.JSON_POINTER,
            source_selector="/mark",
            domain=ObservationDomain.RECORD,
            target_field="mark_text",
        ),
    ]
    if reverse_rules:
        rules.reverse()
    return StoreBinding(
        binding_id="record",
        spec=ObservationTableSpec(
            schema_name="trademark_xx",
            table_name="record_observation",
            domain=ObservationDomain.RECORD,
            native_columns=(
                NativeColumn("application_number", NativeSqlType.TEXT, nullable=False),
                NativeColumn("mark_text", NativeSqlType.TEXT),
            ),
        ),
        contract=MappingContract(
            jurisdiction="XX",
            source_id="XX_SOURCE",
            version="XX_MAPPING_V1",
            rules=tuple(rules),
            notes="human note that is not ingest semantics",
        ),
    )


def _bundle(*, reverse_rules: bool = False) -> NativeStoreBundle:
    return NativeStoreBundle(
        jurisdiction="XX",
        source_id="XX_SOURCE",
        store_schema="trademark_xx",
        bindings=(_binding(reverse_rules=reverse_rules),),
    )


def test_native_ingest_contract_hash_ignores_harmless_rule_order() -> None:
    first = native_ingest_contract_hash(
        bundle=_bundle(),
        pipeline_id="XX_PIPELINE_V1",
        parser_version="XX_PARSER_V1",
    )
    reordered = native_ingest_contract_hash(
        bundle=_bundle(reverse_rules=True),
        pipeline_id="XX_PIPELINE_V1",
        parser_version="XX_PARSER_V1",
    )
    assert first == reordered


def test_native_ingest_contract_hash_changes_on_reviewed_semantics() -> None:
    first = native_ingest_contract_hash(
        bundle=_bundle(),
        pipeline_id="XX_PIPELINE_V1",
        parser_version="XX_PARSER_V1",
    )
    parser_changed = native_ingest_contract_hash(
        bundle=_bundle(),
        pipeline_id="XX_PIPELINE_V1",
        parser_version="XX_PARSER_V2",
    )
    pipeline_changed = native_ingest_contract_hash(
        bundle=_bundle(),
        pipeline_id="XX_PIPELINE_V2",
        parser_version="XX_PARSER_V1",
    )
    assert first != parser_changed
    assert first != pipeline_changed


def test_native_record_envelope_requires_contiguous_index_compatible_values() -> None:
    assert NativeRecordEnvelope(
        source_index=0,
        record_key="XX-1",
        native={},
    ).validate() == ("source_index must be >= 1",)
    assert NativeRecordEnvelope(
        source_index=1,
        record_key="  ",
        native={},
    ).validate() == ("record_key must not be blank",)
