import pytest

from app.snapshot_delta.ipos_sg_native_facts import IPOS_NATIVE_SOURCE_FIELDS
from app.snapshot_delta.ipos_sg_schema_contract import (
    IPOS_NATIVE_CSV_SOURCE_FIELDS,
    ipos_snapshot_schema_drift,
    validate_ipos_native_snapshot_schema,
)


def test_csv_schema_contract_matches_all_39_native_source_fields():
    assert len(IPOS_NATIVE_CSV_SOURCE_FIELDS) == 39
    assert len(set(IPOS_NATIVE_CSV_SOURCE_FIELDS)) == 39
    assert len(IPOS_NATIVE_SOURCE_FIELDS) == len(IPOS_NATIVE_CSV_SOURCE_FIELDS)
    assert IPOS_NATIVE_CSV_SOURCE_FIELDS[0] == "Application Number"
    assert IPOS_NATIVE_CSV_SOURCE_FIELDS[-1] == "Agent Correspondence Details"


def test_schema_contract_accepts_official_api_field_ids_and_provider_row_id():
    missing, unknown = ipos_snapshot_schema_drift((*IPOS_NATIVE_SOURCE_FIELDS, "_id"))

    assert missing == ()
    assert unknown == ()
    validate_ipos_native_snapshot_schema((*IPOS_NATIVE_SOURCE_FIELDS, "_id"))


def test_schema_contract_accepts_official_csv_display_headings():
    missing, unknown = ipos_snapshot_schema_drift(IPOS_NATIVE_CSV_SOURCE_FIELDS)

    assert missing == ()
    assert unknown == ()
    validate_ipos_native_snapshot_schema(IPOS_NATIVE_CSV_SOURCE_FIELDS)


def test_schema_contract_reports_missing_and_unknown_fields_in_canonical_names():
    observed = (*IPOS_NATIVE_CSV_SOURCE_FIELDS[:-1], "Future Source Field")

    missing, unknown = ipos_snapshot_schema_drift(observed)

    assert missing == ("agentCorrespondenceDetails_json",)
    assert unknown == ("Future Source Field",)

    with pytest.raises(
        ValueError,
        match=(
            "missing=agentCorrespondenceDetails_json; "
            "unknown=Future Source Field"
        ),
    ):
        validate_ipos_native_snapshot_schema(observed)
