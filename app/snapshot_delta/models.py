"""Lightweight snapshot-first domain models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class SnapshotManifest:
    jurisdiction: str
    source_id: str
    dataset_id: str
    retrieved_at: datetime
    source_uri: str
    schema_hash: str
    content_hash: str
    row_count: int
    storage_reference: str


@dataclass(frozen=True)
class DeltaEvent:
    jurisdiction: str
    entity_type: str
    entity_id: str
    event_type: str
    detected_at: datetime
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
