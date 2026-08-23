from datetime import datetime, timezone

import pytest

from app.snapshot_delta.detector import Observation
from app.snapshot_delta.runtime import detect_snapshot_deltas


def _observation(entity_id: str, status: str) -> Observation:
    return Observation(
        entity_type="application",
        entity_id=entity_id,
        payload={"status": status},
        jurisdiction="SG",
    )


def test_snapshot_delta_runtime_detects_create_update_delete():
    detected_at = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
    previous = [
        _observation("SG1", "PENDING"),
        _observation("SG2", "REGISTERED"),
        _observation("SG3", "PENDING"),
    ]
    current = [
        _observation("SG1", "REGISTERED"),
        _observation("SG2", "REGISTERED"),
        _observation("SG4", "PENDING"),
    ]

    events = list(
        detect_snapshot_deltas(
            previous,
            current,
            previous_evidence_reference="manifest:old",
            current_evidence_reference="manifest:new",
            detected_at=detected_at,
        )
    )

    assert [(event.entity_id, event.event_type) for event in events] == [
        ("SG1", "UPDATE_DETECTED"),
        ("SG4", "CREATE_DETECTED"),
        ("SG3", "DELETE_DETECTED"),
    ]
    assert all(event.detected_at == detected_at for event in events)
    assert events[0].before_evidence_reference == "manifest:old"
    assert events[0].after_evidence_reference == "manifest:new"
    assert events[1].after_evidence_reference == "manifest:new"
    assert events[2].before_evidence_reference == "manifest:old"


def test_snapshot_delta_runtime_rejects_duplicate_identity():
    duplicate = [_observation("SG1", "PENDING"), _observation("SG1", "REGISTERED")]

    with pytest.raises(ValueError, match="duplicate observation identity"):
        list(
            detect_snapshot_deltas(
                duplicate,
                [],
                previous_evidence_reference="manifest:old",
                current_evidence_reference="manifest:new",
            )
        )
