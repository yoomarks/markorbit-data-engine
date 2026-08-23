"""Snapshot delta detection primitives.

Changes represent source observations only and do not assert legal conclusions.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from .fingerprint import record_fingerprint
from .models import DeltaEvent


@dataclass(frozen=True)
class Observation:
    entity_type: str
    entity_id: str
    payload: dict
    jurisdiction: str = "SG"


def _event_time(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _event(
    event_type: str,
    observation: Observation,
    detected_at: datetime | None = None,
    evidence_reference: str | None = None,
) -> DeltaEvent:
    return DeltaEvent(
        jurisdiction=observation.jurisdiction,
        entity_type=observation.entity_type,
        entity_id=observation.entity_id,
        event_type=event_type,
        detected_at=_event_time(detected_at),
        after=observation.payload,
        after_evidence_reference=evidence_reference,
    )


def compare_observations(
    previous: Observation | None,
    current: Observation | None,
    detected_at: datetime | None = None,
    *,
    previous_evidence_reference: str | None = None,
    current_evidence_reference: str | None = None,
) -> DeltaEvent | None:
    if previous is None and current is not None:
        return _event(
            "CREATE_DETECTED",
            current,
            detected_at,
            current_evidence_reference,
        )

    if previous is not None and current is None:
        return DeltaEvent(
            jurisdiction=previous.jurisdiction,
            entity_type=previous.entity_type,
            entity_id=previous.entity_id,
            event_type="DELETE_DETECTED",
            detected_at=_event_time(detected_at),
            before=previous.payload,
            before_evidence_reference=previous_evidence_reference,
        )

    if previous is None or current is None:
        return None

    if previous.jurisdiction != current.jurisdiction:
        raise ValueError("cannot compare observations from different jurisdictions")

    if record_fingerprint(previous.entity_type, previous.entity_id, previous.payload) != record_fingerprint(current.entity_type, current.entity_id, current.payload):
        return DeltaEvent(
            jurisdiction=current.jurisdiction,
            entity_type=current.entity_type,
            entity_id=current.entity_id,
            event_type="UPDATE_DETECTED",
            detected_at=_event_time(detected_at),
            before=previous.payload,
            after=current.payload,
            before_evidence_reference=previous_evidence_reference,
            after_evidence_reference=current_evidence_reference,
        )

    return None
