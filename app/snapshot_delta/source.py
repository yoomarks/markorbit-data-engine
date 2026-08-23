"""Jurisdiction-neutral source metadata for snapshot acquisition."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SnapshotSource:
    source_id: str
    dataset_id: str
    filename: str
    dataset_url: str
    api_url: str
    initiate_download_url: str
    poll_download_url: str
    source_type: str = "current_snapshot"
