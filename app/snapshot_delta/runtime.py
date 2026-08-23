"""Streaming snapshot delta runtime primitives."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import datetime, timezone

from .detector import Observation
from .fingerprint import record_fingerprint
from .models import DeltaEvent

ObservationKey = tuple[str, str, str]


def _key(observation: Observation) -> ObservationKey:
    return (
        observation.jurisdiction,
        observation.entity_type,
        observation.entity_id,
    )


def _fingerprint(observation: Observation) -> str:
    return record_fingerprint(
        observation.entity_type,
        observation.entity_id,
        observation.payload,
    )


def detect_snapshot_deltas(
    previous: Iterable[Observation],
    current: Iterable[Observation],
    *,
    previous_evidence_reference: str,
    current_evidence_reference: str,
    detected_at: datetime | None = None,
) -> Iterator[DeltaEvent]:
    """Compare two snapshots while retaining only previous record fingerprints."""
    event_time = detected_at or datetime.now(timezone.utc)
    previous_index: dict[ObservationKey, str] = {}

    for observation in previous:
        key = _key(observation)
        if key in previous_index:
            raise ValueError(f"duplicate observation identity in previous snapshot: {key}")
        previous_index[key] = _fingerprint(observation)

    current_seen: set[ObservationKey] = set()
    for observation in current:
        key = _key(observation)
        if key in current_seen:
            raise ValueError(f"duplicate observation identity in current snapshot: {key}")
        current_seen.add(key)

        previous_fingerprint = previous_index.pop(key, None)
        current_fingerprint = _fingerprint(observation)
        if previous_fingerprint is None:
            yield DeltaEvent(
                jurisdiction=observation.jurisdiction,
                entity_type=observation.entity_type,
                entity_id=observation.entity_id,
                event_type="CREATE_DETECTED",
                detected_at=event_time,
                after=observation.payload,
                after_evidence_reference=current_evidence_reference,
            )
            continue

        if previous_fingerprint != current_fingerprint:
            yield DeltaEvent(
                jurisdiction=observation.jurisdiction,
                entity_type=observation.entity_type,
                entity_id=observation.entity_id,
                event_type="UPDATE_DETECTED",
                detected_at=event_time,
                after=observation.payload,
                before_evidence_reference=previous_evidence_reference,
                after_evidence_reference=current_evidence_reference,
            )

    for jurisdiction, entity_type, entity_id in previous_index:
        yield DeltaEvent(
            jurisdiction=jurisdiction,
            entity_type=entity_type,
            entity_id=entity_id,
            event_type="DELETE_DETECTED",
            detected_at=event_time,
            before_evidence_reference=previous_evidence_reference,
        )
