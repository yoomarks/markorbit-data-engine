from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json

import pytest

from app.us.status_reference import (
    AUTHORITY,
    CURRENT_OFFICIAL_DOCUMENT_DATE,
    CURRENT_OFFICIAL_DOCUMENT_NAME,
    CURRENT_OFFICIAL_DOCUMENT_URL,
    REFERENCE_KIND,
    REFERENCE_PAYLOAD_SCHEMA,
    enrich_status_counts,
    normalize_reference_payload,
)


def _payload() -> dict:
    return {
        "schema": REFERENCE_PAYLOAD_SCHEMA,
        "authority": AUTHORITY,
        "reference_kind": REFERENCE_KIND,
        "reference_version": "USPTO_STATUS_CODES_TEST_20250813",
        "source": {
            "document_name": CURRENT_OFFICIAL_DOCUMENT_NAME,
            "document_date": CURRENT_OFFICIAL_DOCUMENT_DATE.isoformat(),
            "url": CURRENT_OFFICIAL_DOCUMENT_URL,
            "sha256": "a" * 64,
            "evidence_note": "Synthetic test payload; source metadata shape only.",
        },
        "records": [
            {
                "code": "700",
                "official_description": "Fixture description 700",
                "official_definition": "Fixture definition 700",
                "official_category": "",
                "source_locator": "fixture-row-2",
            },
            {
                "code": "630",
                "official_description": "Fixture description 630",
                "official_definition": "",
                "official_category": "",
                "source_locator": "fixture-row-1",
            },
        ],
    }


def test_current_official_status_document_metadata_is_frozen() -> None:
    assert CURRENT_OFFICIAL_DOCUMENT_NAME == "Table1TrademarkStatusCodes_20250813.doc"
    assert CURRENT_OFFICIAL_DOCUMENT_DATE.isoformat() == "2025-08-13"
    assert CURRENT_OFFICIAL_DOCUMENT_URL.startswith("https://data.uspto.gov/")


def test_reference_payload_is_normalized_deterministically() -> None:
    first = normalize_reference_payload(_payload())
    second = normalize_reference_payload(deepcopy(_payload()))

    assert first == second
    assert [row["code"] for row in first["records"]] == ["630", "700"]
    assert len(first["normalized_payload_sha256"]) == 64
    assert first["source"]["sha256"] == "a" * 64


def test_payload_hash_changes_when_official_text_changes() -> None:
    first = normalize_reference_payload(_payload())
    changed = _payload()
    changed["records"][0]["official_description"] = "Different official text"
    second = normalize_reference_payload(changed)
    assert first["normalized_payload_sha256"] != second["normalized_payload_sha256"]


def test_duplicate_code_is_rejected() -> None:
    payload = _payload()
    payload["records"].append(deepcopy(payload["records"][0]))
    with pytest.raises(ValueError, match="duplicate status code"):
        normalize_reference_payload(payload)


def test_non_numeric_status_code_is_rejected() -> None:
    payload = _payload()
    payload["records"][0]["code"] = "ACTIVE"
    with pytest.raises(ValueError, match="digits only"):
        normalize_reference_payload(payload)


def test_non_uspto_source_url_is_rejected() -> None:
    payload = _payload()
    payload["source"]["url"] = "https://example.com/status.doc"
    with pytest.raises(ValueError, match="USPTO domain"):
        normalize_reference_payload(payload)


def test_invalid_source_sha_is_rejected() -> None:
    payload = _payload()
    payload["source"]["sha256"] = "abc"
    with pytest.raises(ValueError, match="SHA-256"):
        normalize_reference_payload(payload)


def test_empty_official_description_is_rejected() -> None:
    payload = _payload()
    payload["records"][0]["official_description"] = "   "
    with pytest.raises(ValueError, match="official_description"):
        normalize_reference_payload(payload)


def test_payload_can_roundtrip_json_before_normalization(tmp_path: Path) -> None:
    path = tmp_path / "reference.json"
    path.write_text(json.dumps(_payload()), encoding="utf-8")
    parsed = json.loads(path.read_text(encoding="utf-8"))
    normalized = normalize_reference_payload(parsed)
    assert normalized["reference_version"] == "USPTO_STATUS_CODES_TEST_20250813"


def test_enrichment_keeps_unknown_codes_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_lookup(codes):
        assert set(codes) == {"630", "999"}
        return {
            "reference": {"reference_version": "TEST"},
            "mappings": {
                "630": {
                    "raw_code": "630",
                    "official_description": "Fixture 630",
                    "official_definition": "",
                    "official_category": "",
                    "source_locator": "fixture",
                }
            },
        }

    monkeypatch.setattr(
        "app.us.status_reference.lookup_active_status_codes",
        fake_lookup,
    )
    result = enrich_status_counts(
        [
            {"status_code": "630", "case_count": 4},
            {"status_code": "999", "case_count": 2},
        ]
    )
    assert result["mapped_code_count"] == 1
    assert result["unmapped_code_count"] == 1
    assert result["unmapped_status_codes"] == [
        {"status_code": "999", "case_count": 2}
    ]
    assert result["status_codes"][1]["official_status_reference"] is None
    assert result["semantics"] == "USPTO_OFFICIAL_REFERENCE_NOT_MARKORBIT_LEGAL_CONCLUSION"
