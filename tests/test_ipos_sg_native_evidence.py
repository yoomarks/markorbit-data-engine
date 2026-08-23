from datetime import datetime, timezone

import pytest

from app.snapshot_delta.detector import Observation
from app.snapshot_delta.ipos_sg_native_evidence import native_family_evidence_for_updates


def observation(entity_id: str, **payload):
    return Observation(
        jurisdiction="SG",
        entity_type="application",
        entity_id=entity_id,
        payload={
            "applicationNumber": entity_id,
            "markStatus": payload.pop("markStatus", "Pending"),
            **payload,
        },
    )


def test_native_family_evidence_scans_only_update_identities_and_is_deterministic():
    detected_at = datetime(2026, 8, 24, 1, 0, tzinfo=timezone.utc)
    previous = [
        observation("SG1", markStatus="Pending"),
        observation("SG2", markStatus="Registered"),
        observation("SG3", markStatus="Pending"),
    ]
    current = [
        observation("SG1", markStatus="Registered"),
        observation("SG4", markStatus="Pending"),
        observation("SG3", markStatus="Pending", transferData_json='[{"to":"A"}]'),
    ]

    evidence = list(
        native_family_evidence_for_updates(
            previous,
            current,
            {"SG3", "SG1"},
            detected_at=detected_at,
            before_evidence_reference="snapshot:ipos:before",
            after_evidence_reference="snapshot:ipos:after",
        )
    )

    assert [(item.application_number, item.family) for item in evidence] == [
        ("SG1", "status"),
        ("SG3", "transfer"),
    ]
    assert evidence[0].changed_fields == ("mark_status",)
    assert evidence[0].before["mark_status"] == "Pending"
    assert evidence[0].after["mark_status"] == "Registered"
    assert evidence[1].before["transfer_data"] == ()
    assert evidence[1].after["transfer_data"] == ({"to": "A"},)
    assert all(item.detected_at == detected_at for item in evidence)
    assert all(
        item.before_evidence_reference == "snapshot:ipos:before" for item in evidence
    )
    assert all(item.after_evidence_reference == "snapshot:ipos:after" for item in evidence)


def test_native_family_evidence_does_not_emit_create_or_delete_families():
    evidence = list(
        native_family_evidence_for_updates(
            [observation("SG1")],
            [observation("SG2")],
            set(),
            detected_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
            before_evidence_reference="before",
            after_evidence_reference="after",
        )
    )

    assert evidence == []


def test_native_family_evidence_rejects_missing_or_duplicate_update_identity():
    kwargs = {
        "detected_at": datetime(2026, 8, 24, tzinfo=timezone.utc),
        "before_evidence_reference": "before",
        "after_evidence_reference": "after",
    }

    with pytest.raises(ValueError, match="missing from previous"):
        list(
            native_family_evidence_for_updates(
                [observation("SG1")],
                [observation("SG1")],
                {"SG9"},
                **kwargs,
            )
        )

    with pytest.raises(ValueError, match="duplicate updated IPOS application identity"):
        list(
            native_family_evidence_for_updates(
                [observation("SG1"), observation("SG1")],
                [observation("SG1", markStatus="Registered")],
                {"SG1"},
                **kwargs,
            )
        )
