"""Singapore IPOS row-to-observation mapping."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from app.jurisdictions.singapore.source import JURISDICTION

from .detector import Observation
from .loader import SnapshotCsvLoader

_APPLICATION_NUMBER_FIELDS = ("Application Number", "applicationNumber")
_MARK_STATUS_FIELDS = ("Mark Status", "markStatus")


def _has_any_field(fieldnames: set[str], aliases: tuple[str, ...]) -> bool:
    return any(field in fieldnames for field in aliases)


def validate_ipos_snapshot_schema(loader: SnapshotCsvLoader) -> None:
    """Fail fast when critical authoritative IPOS columns are absent."""
    fieldnames = set(loader.fieldnames())
    missing: list[str] = []
    if not _has_any_field(fieldnames, _APPLICATION_NUMBER_FIELDS):
        missing.append("Application Number")
    if not _has_any_field(fieldnames, _MARK_STATUS_FIELDS):
        missing.append("Mark Status")
    if missing:
        raise ValueError(f"IPOS snapshot missing required columns: {', '.join(missing)}")


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
        jurisdiction=JURISDICTION,
    )


def observations_from_ipos_snapshot(loader: SnapshotCsvLoader) -> Iterator[Observation]:
    validate_ipos_snapshot_schema(loader)
    for row in loader.rows():
        yield observation_from_ipos_row(row)
