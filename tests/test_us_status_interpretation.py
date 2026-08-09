from copy import deepcopy

import pytest

from app.us.status_interpretation import RULESET_PAYLOAD_SCHEMA, normalize_ruleset_payload


def _payload() -> dict:
    return {
        "schema": RULESET_PAYLOAD_SCHEMA,
        "ruleset_version": "TEST_RULESET_V1",
        "status_reference_version": "STATUS_REF_V1",
        "event_reference_version": "EVENT_REF_V1",
        "source": {
            "document_name": "rules.md",
            "sha256": "a" * 64,
            "evidence_note": "test",
        },
        "rules": [
            {
                "rule_id": "RULE_1",
                "priority": 10,
                "status_codes": ["700"],
                "event_codes_any": ["newap"],
                "event_codes_all": [],
                "result_label": "TEST_ONLY",
                "confidence": "high",
                "rationale": "Test-only rationale.",
                "source_refs": ["test-ref"],
            }
        ],
    }


def test_ruleset_normalizes_deterministically_and_uppercases_events() -> None:
    first = normalize_ruleset_payload(_payload())
    second = normalize_ruleset_payload(deepcopy(_payload()))
    assert first == second
    assert first["rules"][0]["event_codes_any"] == ["NEWAP"]
    assert first["rules"][0]["confidence"] == "HIGH"
    assert len(first["normalized_payload_sha256"]) == 64


def test_ruleset_rejects_duplicate_rule_ids() -> None:
    payload = _payload()
    payload["rules"].append(deepcopy(payload["rules"][0]))
    with pytest.raises(ValueError, match="duplicate rule_id"):
        normalize_ruleset_payload(payload)


def test_ruleset_requires_both_official_reference_versions() -> None:
    payload = _payload()
    payload["event_reference_version"] = ""
    with pytest.raises(ValueError, match="event_reference_version"):
        normalize_ruleset_payload(payload)
