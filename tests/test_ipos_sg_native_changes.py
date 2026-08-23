import pytest

from app.snapshot_delta.ipos_sg_native_changes import (
    IPOS_NATIVE_FAMILY_FIELDS,
    diff_ipos_native_families,
    native_family_payloads,
)
from app.snapshot_delta.ipos_sg_native_facts import native_facts_from_ipos_row


def _facts(**overrides):
    row = {
        "applicationNumber": "40202600001A",
        "markStatus": "Pending",
        "filingDate": "2026-01-02",
        "lastModifiedDate": "2026-01-03",
    }
    row.update(overrides)
    return native_facts_from_ipos_row(row)


def test_identical_native_facts_produce_no_family_changes():
    facts = _facts(markData_json=[{"wordsInMark": "EXAMPLE"}])

    assert diff_ipos_native_families(facts, facts) == ()


def test_status_only_change_is_neutral_and_exact():
    previous = _facts(markStatus="Pending", markStatusDate="2026-01-04")
    current = _facts(markStatus="Registered", markStatusDate="2026-02-10")

    changes = diff_ipos_native_families(previous, current)

    assert [change.family for change in changes] == ["status"]
    change = changes[0]
    assert change.application_number == "40202600001A"
    assert change.changed_fields == ("mark_status", "mark_status_date")
    assert change.before["mark_status"] == "Pending"
    assert change.after["mark_status"] == "Registered"
    assert change.before["mark_status_date"] == "2026-01-04"
    assert change.after["mark_status_date"] == "2026-02-10"


def test_multiple_source_family_changes_follow_contract_order():
    previous = _facts(
        goodsAndServicesSpecifications_json=[{"classNum": "Class 09"}],
        currentApplicantProprietorDetails_json=[{"name": "Old Owner"}],
        transferData_json=[],
        securityInterestData_json=[],
    )
    current = _facts(
        goodsAndServicesSpecifications_json=[{"classNum": "Class 09", "item": "Software"}],
        currentApplicantProprietorDetails_json=[{"name": "New Owner"}],
        transferData_json=[{"dateOfTransferOfOwnership": "2026-03-01"}],
        securityInterestData_json=[{"securityInterestRefNo": "SI-1"}],
    )

    changes = diff_ipos_native_families(previous, current)

    assert [change.family for change in changes] == [
        "security_interest",
        "transfer",
        "goods_services",
        "applicants",
    ]
    assert changes[0].changed_fields == ("security_interest_data",)
    assert changes[1].changed_fields == ("transfer_data",)
    assert changes[2].changed_fields == ("goods_services",)
    assert changes[3].changed_fields == ("applicants",)


def test_native_family_change_preserves_nested_source_values_without_interpretation():
    source_event = {
        "events": {
            "code": "TYPE_CF_CM8",
            "description": "Full Transfer of Ownership",
            "eventDate": "2010-01-28",
        }
    }
    previous = _facts(otherEntriesData_json=[])
    current = _facts(otherEntriesData_json=[source_event])

    changes = diff_ipos_native_families(previous, current)

    assert len(changes) == 1
    assert changes[0].family == "other_entries"
    assert changes[0].after["other_entries_data"] == (source_event,)
    assert "event_type" not in changes[0].after


def test_family_payloads_cover_every_native_fact_except_application_identity():
    facts = _facts()
    payloads = native_family_payloads(facts)

    family_names = tuple(family for family, _ in IPOS_NATIVE_FAMILY_FIELDS)
    assert tuple(payloads) == family_names

    covered_fields = {
        field for _, fields in IPOS_NATIVE_FAMILY_FIELDS for field in fields
    }
    expected_fields = set(facts.__dataclass_fields__) - {"application_number"}
    assert covered_fields == expected_fields


def test_family_comparison_rejects_different_application_identity():
    previous = _facts()
    current = native_facts_from_ipos_row(
        {"applicationNumber": "DIFFERENT", "markStatus": "Pending"}
    )

    with pytest.raises(ValueError, match="different application numbers"):
        diff_ipos_native_families(previous, current)
