"""Jurisdiction- and provider-neutral snapshot source metadata."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SnapshotSource:
    source_id: str
    dataset_id: str
    filename: str
    source_type: str
