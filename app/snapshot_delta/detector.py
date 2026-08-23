"""Snapshot delta detection primitives.

Changes represent source observations only and do not assert legal conclusions.
"""

from dataclasses import dataclass

from .models import DeltaEvent
from .fingerprint import record_fingerprint


@dataclass(frozen=True)
class Observation:
    entity_type: str
    entity_id: str
    payload: dict


def compare_observations(previous: Observation | None, current: Observation | None) -> DeltaEvent | None:
    if previous is None and current is not None:
        return DeltaEvent(
            event_type="CREATE_DETECTED",
            entity_type=current.entity_type,
            entity_id=current.entity_id,
        )
    if previous is not None and current is None:
        return DeltaEvent(
            event_type="DELETE_DETECTED",
            entity_type=previous.entity_type,
            entity_id=previous.entity_id,
        )
    if previous is None or current is None:
        return None
    if record_fingerprint(previous.payload) != record_fingerprint(current.payload):
        return DeltaEvent(
            event_type="UPDATE_DETECTED",
            entity_type=current.entity_type,
            entity_id=current.entity_id,
        )
    return None
