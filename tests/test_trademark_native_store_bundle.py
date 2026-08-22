import uuid

import pytest

from app.trademark_factory.mapping import MappingContract, MappingRule, SelectorKind
from app.trademark_factory.store_bundle import (
    NativeStoreBundle,
    StoreBinding,
)
from app.trademark_framework.contracts import ObservationDomain
from app.trademark_framework.native_store import NativeColumn, NativeSqlType, ObservationTableSpec


def _contract(domain: ObservationDomain, *rules: tuple[str, str, bool]) -> MappingContract:
    return MappingContract(
        jurisdiction="XX",
        source_id="XX_SOURCE",
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
    record = StoreBinding(
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
        contract=_contract(
            ObservationDomain.RECORD,
            ("/application/number", "application_number", True),
            ("/mark/text", "mark_text", False),
        ),
    )
    party = StoreBinding(
        binding_id="party",
        spec=ObservationTableSpec(
            schema_name="trademark_xx",
            table_name="party_observation",
            domain=ObservationDomain.PARTY,
            native_columns=(
                NativeColumn("application_number", NativeSqlType.TEXT, nullable=False),
                NativeColumn("party_name", NativeSqlType.TEXT, nullable=False),
            ),
        ),
        contract=_contract(
            ObservationDomain.PARTY,
            ("/application/number", "application_number", True),
            ("/owner/name", "party_name", True),
        ),
    )
    return NativeStoreBundle(
        jurisdiction="XX",
        source_id="XX_SOURCE",
        store_schema="trademark_xx",
        bindings=(record, party),
    )


def test_native_store_bundle_validates_multiple_native_domains() -> None:
    bundle = _bundle()
    assert bundle.validate() == ()
    assert bundle.binding("party").spec.domain == ObservationDomain.PARTY
    assert tuple(binding.binding_id for binding in bundle.bindings_for_domain(ObservationDomain.RECORD)) == (
        "record",
    )


def test_native_store_bundle_rejects_duplicate_tables_and_bindings() -> None:
    first = _bundle().bindings[0]
    duplicate_id = NativeStoreBundle(
        jurisdiction="XX",
        source_id="XX_SOURCE",
        store_schema="trademark_xx",
        bindings=(first, StoreBinding("record", _bundle().bindings[1].spec, _bundle().bindings[1].contract)),
    )
    assert "native store bundle binding ids must be unique" in duplicate_id.validate()

    duplicate_table = NativeStoreBundle(
        jurisdiction="XX",
        source_id="XX_SOURCE",
        store_schema="trademark_xx",
        bindings=(first, StoreBinding("record_copy", first.spec, first.contract)),
    )
    assert "native store bundle tables must be unique" in duplicate_table.validate()


def test_binding_rejects_unknown_or_unmapped_required_native_columns() -> None:
    unknown_target = StoreBinding(
        binding_id="record",
        spec=ObservationTableSpec(
            schema_name="trademark_xx",
            table_name="record_observation",
            domain=ObservationDomain.RECORD,
            native_columns=(NativeColumn("application_number", NativeSqlType.TEXT, nullable=False),),
        ),
        contract=_contract(
            ObservationDomain.RECORD,
            ("/application/number", "application_number", True),
            ("/mark/text", "invented_column", False),
        ),
    )
    assert any("mapping targets missing from table" in error for error in unknown_target.validate())

    unmapped_required = StoreBinding(
        binding_id="party",
        spec=ObservationTableSpec(
            schema_name="trademark_xx",
            table_name="party_observation",
            domain=ObservationDomain.PARTY,
            native_columns=(
                NativeColumn("application_number", NativeSqlType.TEXT, nullable=False),
                NativeColumn("party_name", NativeSqlType.TEXT, nullable=False),
            ),
        ),
        contract=_contract(
            ObservationDomain.PARTY,
            ("/application/number", "application_number", True),
        ),
    )
    assert any("required table columns lack mapping rules" in error for error in unmapped_required.validate())


def test_bundle_rejects_schema_and_source_mismatch() -> None:
    binding = _bundle().bindings[0]
    wrong_schema = NativeStoreBundle(
        jurisdiction="XX",
        source_id="XX_SOURCE",
        store_schema="trademark_other",
        bindings=(binding,),
    )
    assert any("does not match bundle store_schema" in error for error in wrong_schema.validate())

    wrong_source_contract = MappingContract(
        jurisdiction="XX",
        source_id="OTHER_SOURCE",
        version="XX_MAPPING_V1",
        rules=binding.contract.rules,
    )
    wrong_source = NativeStoreBundle(
        jurisdiction="XX",
        source_id="XX_SOURCE",
        store_schema="trademark_xx",
        bindings=(StoreBinding("record", binding.spec, wrong_source_contract),),
    )
    assert any("mapping source" in error and "does not match" in error for error in wrong_source.validate())


def test_bundle_binding_lookup_fails_closed() -> None:
    with pytest.raises(ValueError):
        _bundle().binding("missing")


def test_bundle_has_no_global_identity_assumption() -> None:
    bundle = _bundle()
    assert not hasattr(bundle, "application_number")
    assert isinstance(uuid.UUID("11111111-1111-1111-1111-111111111111"), uuid.UUID)
