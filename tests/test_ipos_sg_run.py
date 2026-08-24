import sys
from datetime import datetime, timezone
from pathlib import Path

from app.snapshot_delta import ipos_sg_run
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


def test_cycle_result_payload_exposes_native_family_evidence_and_cleanup_state():
    result = SnapshotCycleResult(
        status="CHANGED",
        manifest=manifest(),
        event_count=4,
        events_path=Path("events/previous__current.jsonl"),
        native_change_count=7,
        native_changes_path=Path("native_changes/previous__current.jsonl"),
        cleanup_pending_paths=(Path("snapshots/previous.csv"),),
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
    assert payload["cleanup_pending_paths"] == ["snapshots/previous.csv"]
    assert payload["storage_reference"] == f"snapshots/{'b' * 64}.csv"


def test_cycle_result_payload_preserves_empty_evidence_and_cleanup_paths():
    payload = cycle_result_payload(
        SnapshotCycleResult(status="BOOTSTRAPPED", manifest=manifest())
    )

    assert payload["event_count"] == 0
    assert payload["events_path"] is None
    assert payload["native_change_count"] == 0
    assert payload["native_changes_path"] is None
    assert payload["cleanup_pending_paths"] == []


def test_operator_cycle_uses_api_key_from_environment_without_exposing_it(
    monkeypatch, tmp_path: Path, capsys
):
    seen = {}
    api_key = "operator-secret-key"

    class FakeDownloader:
        def __init__(self, *, api_key=None):
            seen["api_key"] = api_key

    def fake_run(state_dir, *, downloader):
        seen["state_dir"] = state_dir
        seen["downloader"] = downloader
        return SnapshotCycleResult(status="BOOTSTRAPPED", manifest=manifest())

    monkeypatch.setattr(ipos_sg_run, "DataGovSgSnapshotDownloader", FakeDownloader)
    monkeypatch.setattr(ipos_sg_run, "run_ipos_snapshot_cycle", fake_run)
    monkeypatch.setenv("DATA_GOV_SG_API_KEY", api_key)
    monkeypatch.setattr(
        sys,
        "argv",
        ["ipos_sg_run", "--state-dir", str(tmp_path)],
    )

    assert ipos_sg_run.main() == 0
    assert seen["api_key"] == api_key
    assert seen["state_dir"] == tmp_path
    assert isinstance(seen["downloader"], FakeDownloader)
    assert api_key not in capsys.readouterr().out
