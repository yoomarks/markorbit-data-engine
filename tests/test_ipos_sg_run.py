from datetime import datetime, timezone
from pathlib import Path

from app.snapshot_delta.ipos_sg_run import cycle_result_payload
from app.snapshot_delta.lifecycle import SnapshotCycleResult
from app.snapshot_delta.models import SnapshotManifest


def manifest() -> SnapshotManifest:
    return SnapshotManifest(
        jurisdiction="SG",
        source_id="IPOS_SG_TRADEMARK_APPLICATIONS",
        dataset_id="d_6145acb2130bf781165258e76a584383",
        retrieved_at=datetime(2026, 8, 24, 1, 2, 3, tzinfo=timezone.utc),
        source_uri="https://download.example/ipos.csv",
        schema_hash="a" * 64,
        content_hash="b" * 64,
        row_count=875000,
        storage_reference=f"snapshots/{'b' * 64}.csv",
    )


def test_cycle_result_payload_exposes_native_family_evidence():
    result = SnapshotCycleResult(
        status="CHANGED",
        manifest=manifest(),
        event_count=4,
        events_path=Path("events/previous__current.jsonl"),
        native_change_count=7,
        native_changes_path=Path("native_changes/previous__current.jsonl"),
    )

    payload = cycle_result_payload(result)

    assert payload["status"] == "CHANGED"
    assert payload["content_hash"] == "b" * 64
    assert payload["row_count"] == 875000
    assert payload["retrieved_at"] == "2026-08-24T01:02:03+00:00"
    assert payload["event_count"] == 4
    assert payload["events_path"] == "events/previous__current.jsonl"
    assert payload["native_change_count"] == 7
    assert payload["native_changes_path"] == "native_changes/previous__current.jsonl"
    assert payload["storage_reference"] == f"snapshots/{'b' * 64}.csv"


def test_cycle_result_payload_preserves_empty_evidence_paths():
    payload = cycle_result_payload(
        SnapshotCycleResult(status="BOOTSTRAPPED", manifest=manifest())
    )

    assert payload["event_count"] == 0
    assert payload["events_path"] is None
    assert payload["native_change_count"] == 0
    assert payload["native_changes_path"] is None
