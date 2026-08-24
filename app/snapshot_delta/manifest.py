"""Snapshot manifest construction for current-state source files."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from .fingerprint import fingerprint
from .loader import SnapshotCsvLoader
from .models import SnapshotManifest
from .source import SnapshotSource


def file_sha256(path: str | Path) -> str:
    """Return the physical SHA-256 for one retained or incoming snapshot file."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_snapshot_manifest(
    loader: SnapshotCsvLoader,
    source: SnapshotSource,
    *,
    jurisdiction: str,
    retrieved_at: datetime,
    source_uri: str,
    storage_reference: str,
) -> SnapshotManifest:
    """Build evidence identity for one authoritative current snapshot."""
    return SnapshotManifest(
        jurisdiction=jurisdiction,
        source_id=source.source_id,
        dataset_id=source.dataset_id,
        retrieved_at=retrieved_at,
        source_uri=source_uri,
        schema_hash=fingerprint(loader.fieldnames()),
        content_hash=file_sha256(loader.path),
        row_count=loader.count(),
        storage_reference=storage_reference,
    )
