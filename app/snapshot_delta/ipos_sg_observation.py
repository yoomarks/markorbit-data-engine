"""Singapore IPOS row-to-observation mapping."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from .detector import Observation
from .loader import SnapshotCsvLoader

_APPLICATION_NUMBER_FIELDS = ("Application Number", "applicationNumber")


def observation_from_ipos_row(row: Mapping[str, Any]) -> Observation:
    """Map one authoritative IPOS trademark row to a source observation."""
    entity_id = next(
        (
            str(row[field]).strip()
            for field in _APPLICATION_NUMBER_FIELDS
            if row.get(field) is not None and str(row[field]).strip()
        ),
        "",
    )
    if not entity_id:
        raise ValueError("IPOS trademark row is missing Application Number")

    return Observation(
        entity_type="application",
        entity_id=entity_id,
        payload=dict(row),
        jurisdiction="SG",
    )


def observations_from_ipos_snapshot(loader: SnapshotCsvLoader) -> Iterator[Observation]:
    for row in loader.rows():
        yield observation_from_ipos_row(row)
