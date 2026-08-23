"""Durable, interpretation-free evidence for Singapore IPOS native family changes."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .detector import Observation
from .ipos_sg_native_changes import diff_ipos_native_families
from .ipos_sg_native_facts import (
    IposNativeApplicationFacts,
    native_facts_from_ipos_observation,
)


@dataclass(frozen=True)
class IposNativeFamilyEvidence:
    """One source-native family change with deterministic snapshot evidence links."""

    application_number: str
    family: str
    changed_fields: tuple[str, ...]
    detected_at: datetime
    before: dict[str, Any]
    after: dict[str, Any]
    before_evidence_reference: str
    after_evidence_reference: str


def native_family_evidence_for_updates(
    previous: Iterable[Observation],
    current: Iterable[Observation],
    updated_application_numbers: Collection[str],
    *,
    detected_at: datetime,
    before_evidence_reference: str,
    after_evidence_reference: str,
) -> Iterator[IposNativeFamilyEvidence]:
    """Decompose only already-detected updates without retaining the full corpus in memory.

    The previous scan retains facts only for application identities already classified as
    updates. The current scan then emits evidence in current-source order and native-family
    order. Create/delete observations remain outside this source-family evidence layer.
    """

    update_ids = frozenset(updated_application_numbers)
    if not update_ids:
        return

    previous_facts: dict[str, IposNativeApplicationFacts] = {}
    for observation in previous:
        if observation.entity_id not in update_ids:
            continue
        if observation.entity_id in previous_facts:
            raise ValueError(
                "duplicate updated IPOS application identity in previous snapshot: "
                f"{observation.entity_id}"
            )
        previous_facts[observation.entity_id] = native_facts_from_ipos_observation(observation)

    missing_previous = sorted(update_ids.difference(previous_facts))
    if missing_previous:
        raise ValueError(
            "updated IPOS application missing from previous snapshot: "
            f"{', '.join(missing_previous)}"
        )

    current_seen: set[str] = set()
    for observation in current:
        entity_id = observation.entity_id
        if entity_id not in update_ids:
            continue
        if entity_id in current_seen:
            raise ValueError(
                "duplicate updated IPOS application identity in current snapshot: "
                f"{entity_id}"
            )
        current_seen.add(entity_id)
        current_facts = native_facts_from_ipos_observation(observation)
        for change in diff_ipos_native_families(previous_facts[entity_id], current_facts):
            yield IposNativeFamilyEvidence(
                application_number=entity_id,
                family=change.family,
                changed_fields=change.changed_fields,
                detected_at=detected_at,
                before=change.before,
                after=change.after,
                before_evidence_reference=before_evidence_reference,
                after_evidence_reference=after_evidence_reference,
            )

    missing_current = sorted(update_ids.difference(current_seen))
    if missing_current:
        raise ValueError(
            "updated IPOS application missing from current snapshot: "
            f"{', '.join(missing_current)}"
        )
