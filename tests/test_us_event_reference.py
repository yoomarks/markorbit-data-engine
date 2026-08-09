from copy import deepcopy

import pytest

from app.us.event_reference import (
    AUTHORITY,
    CURRENT_OFFICIAL_SOURCE_PAGE_URL,
    REFERENCE_KIND,
    REFERENCE_PAYLOAD_SCHEMA,
    normalize_reference_payload,
)


def _payload() -> dict:
    return {
        "schema": REFERENCE_PAYLOAD_SCHEMA,
        "authority": AUTHORITY,
        "reference_kind": REFERENCE_KIND,
        "reference_version": "USPTO_EVENT_CODES_TEST_V1",
        "source": {
            "document_name": "event.doc",
            "document_date": "2025-08-13",
            "url": CURRENT_OFFICIAL_SOURCE_PAGE_URL,
            "sha256": "a" * 64,
            "evidence_note": "test",
        },
        "records": [
            {"code": "newap", "official_description": "Fixture event", "source_locator": "row"}
        ],
    }


def test_event_reference_normalizes_codes_and_hashes_deterministically() -> None:
    first = normalize_reference_payload(_payload())
    second = normalize_reference_payload(deepcopy(_payload()))
    assert first == second
    assert first["records"][0]["code"] == "NEWAP"
    assert len(first["normalized_payload_sha256"]) == 64


def test_event_reference_rejects_duplicate_codes() -> None:
    payload = _payload()
    payload["records"].append(deepcopy(payload["records"][0]))
    with pytest.raises(ValueError, match="duplicate event code"):
        normalize_reference_payload(payload)
