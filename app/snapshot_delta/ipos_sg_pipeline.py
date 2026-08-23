"""End-to-end Singapore IPOS snapshot comparison wiring."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from .ipos_sg import IPOS_SG_TRADEMARK_APPLICATIONS
from .ipos_sg_observation import observations_from_ipos_snapshot
from .loader import SnapshotCsvLoader
from .manifest import build_snapshot_manifest
from .models import DeltaEvent, SnapshotManifest
from .runtime import detect_snapshot_deltas


def manifest_evidence_reference(manifest: SnapshotManifest) -> str:
    """Return a deterministic source-evidence identity for one snapshot manifest."""
    return f"snapshot:{manifest.source_id}:{manifest.content_hash}"


def compare_ipos_snapshots(
    previous_loader: SnapshotCsvLoader,
    current_loader: SnapshotCsvLoader,
    *,
    previous_retrieved_at: datetime,
    current_retrieved_at: datetime,
    previous_source_uri: str,
    current_source_uri: str,
    previous_storage_reference: str,
    current_storage_reference: str,
    detected_at: datetime | None = None,
) -> tuple[SnapshotManifest, SnapshotManifest, Iterator[DeltaEvent]]:
    """Build manifests and stream observation deltas for two IPOS snapshots."""
    previous_manifest = build_snapshot_manifest(
        previous_loader,
        IPOS_SG_TRADEMARK_APPLICATIONS,
        jurisdiction="SG",
        retrieved_at=previous_retrieved_at,
        source_uri=previous_source_uri,
        storage_reference=previous_storage_reference,
    )
    current_manifest = build_snapshot_manifest(
        current_loader,
        IPOS_SG_TRADEMARK_APPLICATIONS,
        jurisdiction="SG",
        retrieved_at=current_retrieved_at,
        source_uri=current_source_uri,
        storage_reference=current_storage_reference,
    )

    events = detect_snapshot_deltas(
        observations_from_ipos_snapshot(previous_loader),
        observations_from_ipos_snapshot(current_loader),
        previous_evidence_reference=manifest_evidence_reference(previous_manifest),
        current_evidence_reference=manifest_evidence_reference(current_manifest),
        detected_at=detected_at,
    )
    return previous_manifest, current_manifest, events
