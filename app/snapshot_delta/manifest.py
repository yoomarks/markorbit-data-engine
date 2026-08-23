"""Snapshot manifest construction for current-state source files."""

from __future__ import annotations

import hashlib
from datetime import datetime

from .fingerprint import fingerprint
from .ipos_sg import SnapshotSource
from .loader import SnapshotCsvLoader
from .models import SnapshotManifest


def _file_sha256(loader: SnapshotCsvLoader) -> str:
    digest = hashlib.sha256()
    with loader.path.open("rb") as source:
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
        content_hash=_file_sha256(loader),
        row_count=loader.count(),
        storage_reference=storage_reference,
    )
