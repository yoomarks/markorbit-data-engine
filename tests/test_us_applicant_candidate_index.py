from __future__ import annotations

from app.us.applicant_candidate_index import (
    applicant_candidate_key,
    applicant_source_id,
    candidate_key_from_source_id,
)


def _owner(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "party_name_norm": "Acme Holdings LLC",
        "party_type": "10",
        "legal_entity_type_code": "16",
        "entity_statement": "Limited Liability Company",
        "nationality_country": "US",
        "nationality_state": "DE",
        "nationality_other": "",
        "address_1": "1 Main Street",
        "address_2": "",
        "city": "Wilmington",
        "state": "DE",
        "country": "US",
        "postcode": "19801",
        "dba_aka_text": "",
        "composed_of_statement": "",
    }
    value.update(overrides)
    return value


def test_same_canonical_identity_is_cross_serial_stable():
    left = _owner(serial_number="12345678", owner_key="a" * 64)
    right = _owner(serial_number="87654321", owner_key="b" * 64)
    assert applicant_candidate_key(left) == applicant_candidate_key(right)


def test_material_address_difference_splits_review_candidate():
    left = applicant_candidate_key(_owner(address_1="1 Main Street"))
    right = applicant_candidate_key(_owner(address_1="99 Other Street"))
    assert left != right


def test_entity_type_difference_splits_review_candidate():
    left = applicant_candidate_key(_owner(legal_entity_type_code="16"))
    right = applicant_candidate_key(_owner(legal_entity_type_code="13"))
    assert left != right


def test_identity_text_is_nfkc_whitespace_and_casefold_stable():
    left = applicant_candidate_key(
        _owner(party_name_norm="  ACME   ＨＯＬＤＩＮＧＳ LLC  ")
    )
    right = applicant_candidate_key(_owner(party_name_norm="acme holdings llc"))
    assert left == right


def test_source_id_round_trip_and_forgery_rejection():
    key = applicant_candidate_key(_owner())
    source_id = applicant_source_id(key)
    assert candidate_key_from_source_id(source_id) == key
    assert candidate_key_from_source_id("US_APPLICANT:not-a-digest") is None
    assert candidate_key_from_source_id("OTHER:" + key) is None
