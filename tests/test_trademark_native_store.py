import uuid

import pytest

from app.trademark_framework.contracts import ObservationDomain
from app.trademark_framework.native_store import (
    NativeColumn,
    NativeSqlType,
    ObservationRow,
    ObservationTableSpec,
    observation_row_hash,
)


def _spec() -> ObservationTableSpec:
    return ObservationTableSpec(
        schema_name="trademark_xx",
        table_name="party_observation",
        domain=ObservationDomain.PARTY,
        native_columns=(
            NativeColumn("application_number", NativeSqlType.TEXT, nullable=False),
            NativeColumn("party_name", NativeSqlType.TEXT),
            NativeColumn("source_meta", NativeSqlType.JSONB),
        ),
    )


def test_native_store_ddl_preserves_native_columns_and_provenance() -> None:
    statements = "\n".join(_spec().create_statements())
    assert "source_row_hash text PRIMARY KEY" in statements
    assert "record_key text NOT NULL" in statements
    assert (
        "source_object_id uuid NOT NULL REFERENCES acquisition.global_trademark_source_object"
        in statements
    )
    assert "source_index integer NOT NULL" in statements
    assert "parser_version text NOT NULL" in statements
    assert "mapping_version text NOT NULL" in statements
    assert "application_number text NOT NULL" in statements
    assert "party_name text" in statements
    assert "source_meta jsonb" in statements
    assert "source_payload jsonb NOT NULL" in statements
    assert (
        "UNIQUE (source_object_id, record_key, source_index, parser_version, mapping_version)"
        in statements
    )


def test_native_store_identifiers_and_reserved_columns_fail_closed() -> None:
    invalid_identifier = ObservationTableSpec(
        schema_name="trademark-xx",
        table_name="party_observation",
        domain=ObservationDomain.PARTY,
        native_columns=(),
    )
    assert invalid_identifier.validate()

    reserved = ObservationTableSpec(
        schema_name="trademark_xx",
        table_name="party_observation",
        domain=ObservationDomain.PARTY,
        native_columns=(NativeColumn("source_payload", NativeSqlType.JSONB),),
    )
    assert any("collides" in error for error in reserved.validate())

    duplicate = ObservationTableSpec(
        schema_name="trademark_xx",
        table_name="party_observation",
        domain=ObservationDomain.PARTY,
        native_columns=(
            NativeColumn("party_name", NativeSqlType.TEXT),
            NativeColumn("party_name", NativeSqlType.TEXT),
        ),
    )
    assert "native column names must be unique" in duplicate.validate()


def test_observation_hash_is_canonical_but_source_identity_sensitive() -> None:
    spec = _spec()
    source_object_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    first = ObservationRow(
        record_key="XX-1",
        source_object_id=source_object_id,
        source_index=1,
        native_values={
            "application_number": "1",
            "party_name": "Example Owner",
            "source_meta": {"language": "en", "sequence": 1},
        },
        source_payload={"owner": {"name": "Example Owner", "sequence": 1}},
        parser_version="XX_PARSER_V1",
        mapping_version="XX_MAPPING_V1",
    )
    reordered = ObservationRow(
        record_key="XX-1",
        source_object_id=source_object_id,
        source_index=1,
        native_values={
            "source_meta": {"sequence": 1, "language": "en"},
            "party_name": "Example Owner",
            "application_number": "1",
        },
        source_payload={"owner": {"sequence": 1, "name": "Example Owner"}},
        parser_version="XX_PARSER_V1",
        mapping_version="XX_MAPPING_V1",
    )
    changed_source = ObservationRow(
        record_key="XX-1",
        source_object_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        source_index=1,
        native_values=first.native_values,
        source_payload=first.source_payload,
        parser_version=first.parser_version,
        mapping_version=first.mapping_version,
    )
    changed_mapping = ObservationRow(
        record_key=first.record_key,
        source_object_id=first.source_object_id,
        source_index=first.source_index,
        native_values=first.native_values,
        source_payload=first.source_payload,
        parser_version=first.parser_version,
        mapping_version="XX_MAPPING_V2",
    )

    assert observation_row_hash(spec, first) == observation_row_hash(spec, reordered)
    assert observation_row_hash(spec, first) != observation_row_hash(spec, changed_source)
    assert observation_row_hash(spec, first) != observation_row_hash(spec, changed_mapping)


def test_invalid_spec_refuses_ddl_rendering() -> None:
    with pytest.raises(ValueError):
        ObservationTableSpec(
            schema_name="trademark_xx",
            table_name="bad table",
            domain=ObservationDomain.RECORD,
            native_columns=(),
        ).create_statements()
