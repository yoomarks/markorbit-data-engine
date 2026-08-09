from copy import deepcopy

import pytest

from app.us.event_roles import EVENT_ROLE_PAYLOAD_SCHEMA, normalize_event_role_payload


def _payload() -> dict:
    return {
        "schema": EVENT_ROLE_PAYLOAD_SCHEMA,
        "ruleset_version": "TEST_EVENT_ROLES_V1",
        "event_reference_version": "EVENT_REF_V1",
        "source": {
            "document_name": "event-role-review.md",
            "sha256": "a" * 64,
            "evidence_note": "reviewed test mapping",
        },
        "rules": [
            {
                "rule_id": "OA_ISSUE",
                "event_code": "nrap",
                "role": "OFFICE_ACTION_NONFINAL_ISSUED",
                "rationale": "reviewed mapping",
                "source_refs": ["review row 1"],
            }
        ],
    }


def test_event_role_payload_normalizes_code_and_role() -> None:
    first = normalize_event_role_payload(_payload())
    second = normalize_event_role_payload(deepcopy(_payload()))
    assert first == second
    assert first["rules"][0]["event_code"] == "NRAP"
    assert first["rules"][0]["role"] == "OFFICE_ACTION_NONFINAL_ISSUED"
    assert len(first["normalized_payload_sha256"]) == 64


def test_event_role_payload_rejects_duplicate_event_code_mapping() -> None:
    payload = _payload()
    other = deepcopy(payload["rules"][0])
    other["rule_id"] = "SECOND"
    payload["rules"].append(other)
    with pytest.raises(ValueError, match="duplicate event_code"):
        normalize_event_role_payload(payload)


def test_event_role_payload_rejects_unknown_role() -> None:
    payload = _payload()
    payload["rules"][0]["role"] = "GUESS_ACTIVE_STATUS"
    with pytest.raises(ValueError, match="role is not allowed"):
        normalize_event_role_payload(payload)
