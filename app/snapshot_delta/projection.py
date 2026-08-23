"""Current-state projection primitives for snapshot delta events."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .detector import Observation
from .models import DeltaEvent

ProjectionKey = tuple[str, str, str]


@dataclass(frozen=True)
class ProjectionRecord:
    jurisdiction: str
    entity_type: str
    entity_id: str
    payload: dict[str, Any]
    evidence_reference: str


def _key(jurisdiction: str, entity_type: str, entity_id: str) -> ProjectionKey:
    return jurisdiction, entity_type, entity_id


def projection_from_observations(
    observations: Iterable[Observation],
    *,
    evidence_reference: str,
) -> dict[ProjectionKey, ProjectionRecord]:
    """Build a current projection from one authoritative snapshot."""
    projection: dict[ProjectionKey, ProjectionRecord] = {}
    for observation in observations:
        key = _key(
            observation.jurisdiction,
            observation.entity_type,
            observation.entity_id,
        )
        if key in projection:
            raise ValueError(f"duplicate observation identity in projection source: {key}")
        projection[key] = ProjectionRecord(
            jurisdiction=observation.jurisdiction,
            entity_type=observation.entity_type,
            entity_id=observation.entity_id,
            payload=dict(observation.payload),
            evidence_reference=evidence_reference,
        )
    return projection


def apply_delta_events(
    projection: Mapping[ProjectionKey, ProjectionRecord],
    events: Iterable[DeltaEvent],
) -> dict[ProjectionKey, ProjectionRecord]:
    """Apply replay-safe CREATE/UPDATE/DELETE deltas to a current projection."""
    current = dict(projection)

    for event in events:
        key = _key(event.jurisdiction, event.entity_type, event.entity_id)

        if event.event_type in {"CREATE_DETECTED", "UPDATE_DETECTED"}:
            if event.after is None or event.after_evidence_reference is None:
                raise ValueError(
                    f"{event.event_type} requires after payload and evidence: {key}"
                )
            current[key] = ProjectionRecord(
                jurisdiction=event.jurisdiction,
                entity_type=event.entity_type,
                entity_id=event.entity_id,
                payload=dict(event.after),
                evidence_reference=event.after_evidence_reference,
            )
            continue

        if event.event_type == "DELETE_DETECTED":
            current.pop(key, None)
            continue

        raise ValueError(f"unsupported projection event type: {event.event_type}")

    return current
