from datetime import datetime, timezone

import pytest

from app.snapshot_delta.detector import Observation
from app.snapshot_delta.models import DeltaEvent
from app.snapshot_delta.projection import (
    apply_delta_events,
    projection_from_observations,
)


def _event(
    entity_id: str,
    event_type: str,
    *,
    after: dict | None = None,
    evidence: str | None = None,
) -> DeltaEvent:
    return DeltaEvent(
        jurisdiction="SG",
        entity_type="application",
        entity_id=entity_id,
        event_type=event_type,
        detected_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
        after=after,
        after_evidence_reference=evidence,
    )


def test_projection_replays_create_update_delete_and_is_idempotent():
    base = projection_from_observations(
        [
            Observation("application", "SG1", {"status": "Pending"}),
            Observation("application", "SG2", {"status": "Registered"}),
        ],
        evidence_reference="snapshot:previous",
    )
    events = [
        _event(
            "SG1",
            "UPDATE_DETECTED",
            after={"status": "Registered"},
            evidence="snapshot:current",
        ),
        _event(
            "SG3",
            "CREATE_DETECTED",
            after={"status": "Pending"},
            evidence="snapshot:current",
        ),
        _event("SG2", "DELETE_DETECTED"),
    ]

    projected = apply_delta_events(base, events)
    replayed = apply_delta_events(projected, events)

    assert projected == replayed
    assert set(projected) == {
        ("SG", "application", "SG1"),
        ("SG", "application", "SG3"),
    }
    assert projected[("SG", "application", "SG1")].payload == {
        "status": "Registered"
    }
    assert projected[("SG", "application", "SG1")].evidence_reference == (
        "snapshot:current"
    )


def test_projection_rejects_duplicate_snapshot_identity():
    observations = [
        Observation("application", "SG1", {"status": "Pending"}),
        Observation("application", "SG1", {"status": "Registered"}),
    ]

    with pytest.raises(ValueError, match="duplicate observation identity"):
        projection_from_observations(
            observations,
            evidence_reference="snapshot:current",
        )


def test_projection_requires_after_evidence_for_upserts():
    base = {}
    event = _event("SG1", "CREATE_DETECTED", after={"status": "Pending"})

    with pytest.raises(ValueError, match="requires after payload and evidence"):
        apply_delta_events(base, [event])


def test_projection_rejects_unsupported_event_types():
    event = _event("SG1", "STATUS_CHANGED")

    with pytest.raises(ValueError, match="unsupported projection event type"):
        apply_delta_events({}, [event])
