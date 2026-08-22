import uuid

import pytest

from app.trademark_factory.mapping import (
    MappingContract,
    MappingRule,
    SelectorKind,
)
from app.trademark_factory.writer import (
    append_mapped_observation,
    build_observation_row,
    extract_domain_values,
)
from app.trademark_framework.contracts import ObservationDomain
from app.trademark_framework.native_store import NativeColumn, NativeSqlType, ObservationTableSpec


SOURCE_OBJECT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _contract() -> MappingContract:
    return MappingContract(
        jurisdiction="XX",
        source_id="XX_API",
        version="XX_MAPPING_V1",
        rules=(
            MappingRule(
                selector_kind=SelectorKind.JSON_POINTER,
                source_selector="/application/number",
                domain=ObservationDomain.RECORD,
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
                selector_kind=SelectorKind.FIELD,
                source_selector="optional_note",
                domain=ObservationDomain.PARTY,
                target_field="party_note",
            ),
            MappingRule(
                selector_kind=SelectorKind.FIELD,
                source_selector="classes",
                domain=ObservationDomain.PARTY,
                target_field="class_numbers",
                repeated=True,
                transform_id="strip_classes",
            ),
        ),
        identity_targets=("application_number",),
    )


def test_extract_domain_values_maps_json_and_optional_fields() -> None:
    native = {
        "application": {"number": "1001"},
        "owner": {"name": "Example Owner"},
        "classes": [" 9 ", "42"],
    }
    values = extract_domain_values(
        _contract(),
        native,
        ObservationDomain.PARTY,
        transforms={"strip_classes": lambda value: [str(item).strip() for item in value]},
    )
    assert values == {
        "party_name": "Example Owner",
        "class_numbers": ["9", "42"],
    }


def test_required_selector_and_transform_fail_closed() -> None:
    contract = _contract()
    with pytest.raises(ValueError, match="required source selector missing"):
        extract_domain_values(
            contract,
            {"classes": ["9"]},
            ObservationDomain.PARTY,
            transforms={"strip_classes": lambda value: value},
        )

    with pytest.raises(ValueError, match="mapping transform not supplied"):
        extract_domain_values(
            contract,
            {
                "owner": {"name": "Example Owner"},
                "classes": ["9"],
            },
            ObservationDomain.PARTY,
        )


def test_repeated_rule_requires_sequence() -> None:
    with pytest.raises(ValueError, match="repeated mapping requires list/tuple"):
        extract_domain_values(
            _contract(),
            {
                "owner": {"name": "Example Owner"},
                "classes": "9",
            },
            ObservationDomain.PARTY,
            transforms={"strip_classes": lambda value: value},
        )


def test_mapping_contract_blocks_ambiguous_target_writers() -> None:
    contract = MappingContract(
        jurisdiction="XX",
        source_id="XX_API",
        version="XX_MAPPING_V1",
        rules=(
            MappingRule(
                selector_kind=SelectorKind.FIELD,
                source_selector="owner_name",
                domain=ObservationDomain.PARTY,
                target_field="party_name",
            ),
            MappingRule(
                selector_kind=SelectorKind.FIELD,
                source_selector="holder_name",
                domain=ObservationDomain.PARTY,
                target_field="party_name",
            ),
        ),
    )
    assert any("ambiguous mapping target" in error for error in contract.validate())


def test_build_observation_row_preserves_source_identity_and_mapping_lineage() -> None:
    row = build_observation_row(
        contract=_contract(),
        domain=ObservationDomain.RECORD,
        native={
            "application": {"number": "1001"},
            "owner": {"name": "Example Owner"},
            "classes": ["9"],
        },
        record_key="XX-1001",
        source_object_id=SOURCE_OBJECT_ID,
        source_index=7,
        parser_version="XX_PARSER_V3",
    )
    assert row.record_key == "XX-1001"
    assert row.source_object_id == SOURCE_OBJECT_ID
    assert row.source_index == 7
    assert row.parser_version == "XX_PARSER_V3"
    assert row.mapping_version == "XX_MAPPING_V1"
    assert row.native_values == {"application_number": "1001"}
    assert row.source_payload["owner"] == {"name": "Example Owner"}


def test_xml_mapping_stays_parser_owned() -> None:
    contract = MappingContract(
        jurisdiction="XX",
        source_id="XX_XML",
        version="XX_XML_MAPPING_V1",
        rules=(
            MappingRule(
                selector_kind=SelectorKind.XPATH,
                source_selector="//Applicant/Name",
                domain=ObservationDomain.PARTY,
                target_field="party_name",
                required=True,
            ),
        ),
    )
    with pytest.raises(NotImplementedError, match="source-parser-owned"):
        extract_domain_values(contract, {}, ObservationDomain.PARTY)


def test_append_blocks_mapping_targets_not_declared_by_native_store() -> None:
    spec = ObservationTableSpec(
        schema_name="trademark_xx",
        table_name="party_observation",
        domain=ObservationDomain.PARTY,
        native_columns=(NativeColumn("party_name", NativeSqlType.TEXT),),
    )
    with pytest.raises(ValueError, match="mapping targets missing from native observation table"):
        append_mapped_observation(
            object(),
            spec,
            contract=_contract(),
            native={
                "owner": {"name": "Example Owner"},
                "classes": ["9"],
            },
            record_key="XX-1001",
            source_object_id=SOURCE_OBJECT_ID,
            source_index=1,
            parser_version="XX_PARSER_V1",
            transforms={"strip_classes": lambda value: value},
        )
