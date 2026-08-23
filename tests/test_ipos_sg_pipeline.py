from datetime import datetime, timezone

from app.snapshot_delta.ipos_sg_pipeline import (
    compare_ipos_snapshots,
    manifest_evidence_reference,
)
from app.snapshot_delta.loader import SnapshotCsvLoader


def test_compare_ipos_snapshots_wires_manifests_and_deltas(tmp_path):
    previous = tmp_path / "previous.csv"
    current = tmp_path / "current.csv"
    previous.write_text(
        "Application Number,Mark Status\nSG1,Pending\nSG2,Registered\n",
        encoding="utf-8",
    )
    current.write_text(
        "Application Number,Mark Status\nSG1,Registered\nSG3,Pending\n",
        encoding="utf-8",
    )
    previous_time = datetime(2026, 8, 22, tzinfo=timezone.utc)
    current_time = datetime(2026, 8, 23, tzinfo=timezone.utc)
    detected_at = datetime(2026, 8, 23, 1, 0, tzinfo=timezone.utc)

    previous_manifest, current_manifest, event_stream = compare_ipos_snapshots(
        SnapshotCsvLoader(previous),
        SnapshotCsvLoader(current),
        previous_retrieved_at=previous_time,
        current_retrieved_at=current_time,
        previous_source_uri="https://data.gov.sg/previous",
        current_source_uri="https://data.gov.sg/current",
        previous_storage_reference="snapshot://sg/previous",
        current_storage_reference="snapshot://sg/current",
        detected_at=detected_at,
    )
    events = list(event_stream)

    assert previous_manifest.row_count == 2
    assert current_manifest.row_count == 2
    assert previous_manifest.dataset_id == current_manifest.dataset_id
    assert previous_manifest.content_hash != current_manifest.content_hash
    assert [(event.entity_id, event.event_type) for event in events] == [
        ("SG1", "UPDATE_DETECTED"),
        ("SG3", "CREATE_DETECTED"),
        ("SG2", "DELETE_DETECTED"),
    ]
    assert events[0].before_evidence_reference == manifest_evidence_reference(
        previous_manifest
    )
    assert events[0].after_evidence_reference == manifest_evidence_reference(
        current_manifest
    )
    assert all(event.detected_at == detected_at for event in events)


def test_manifest_evidence_reference_depends_on_content_hash(tmp_path):
    snapshot = tmp_path / "snapshot.csv"
    snapshot.write_text("Application Number,Mark Status\nSG1,Pending\n", encoding="utf-8")
    loader = SnapshotCsvLoader(snapshot)
    manifest, current_manifest, events = compare_ipos_snapshots(
        loader,
        SnapshotCsvLoader(snapshot),
        previous_retrieved_at=datetime(2026, 8, 22, tzinfo=timezone.utc),
        current_retrieved_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
        previous_source_uri="source:previous",
        current_source_uri="source:current",
        previous_storage_reference="storage:previous",
        current_storage_reference="storage:current",
    )

    assert manifest_evidence_reference(manifest) == manifest_evidence_reference(
        current_manifest
    )
    assert list(events) == []
