import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.snapshot_delta.acquisition import AcquiredSnapshot
from app.snapshot_delta.ipos_sg import IPOS_SG_TRADEMARK_APPLICATIONS
from app.snapshot_delta.ipos_sg_schema_contract import IPOS_NATIVE_CSV_SOURCE_FIELDS
from app.snapshot_delta.lifecycle import run_ipos_snapshot_cycle


class FakeDownloader:
    def __init__(self, payload: str, *, retrieved_at: datetime):
        self.payload = payload
        self.retrieved_at = retrieved_at

    def download(self, destination_directory: str | Path) -> AcquiredSnapshot:
        destination = Path(destination_directory)
        destination.mkdir(parents=True, exist_ok=True)
        path = destination / IPOS_SG_TRADEMARK_APPLICATIONS.filename
        path.write_text(self.payload, encoding="utf-8")
        return AcquiredSnapshot(
            path=path,
            source_uri="https://download.example/ipos.csv",
            retrieved_at=self.retrieved_at,
            bytes_written=path.stat().st_size,
        )


def downloader(payload: str, day: int) -> FakeDownloader:
    return FakeDownloader(
        payload,
        retrieved_at=datetime(2026, 8, day, 1, 0, tzinfo=timezone.utc),
    )


def snapshot_csv(rows: list[tuple[str, str]]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=IPOS_NATIVE_CSV_SOURCE_FIELDS)
    writer.writeheader()
    for entity_id, status in rows:
        writer.writerow({"Application Number": entity_id, "Mark Status": status})
    return stream.getvalue()


def read_pointer(state: Path) -> dict:
    return json.loads((state / "current.json").read_text(encoding="utf-8"))


def test_first_snapshot_bootstraps_current_version_without_events(tmp_path: Path):
    result = run_ipos_snapshot_cycle(
        tmp_path,
        downloader=downloader(snapshot_csv([("SG1", "Pending")]), 22),
    )

    assert result.status == "BOOTSTRAPPED"
    assert result.event_count == 0
    assert result.events_path is None
    pointer = read_pointer(tmp_path)
    assert pointer["content_hash"] == result.manifest.content_hash
    snapshot_path = tmp_path / result.manifest.storage_reference
    assert snapshot_path.exists()
    assert len(list((tmp_path / "snapshots").glob("*.csv"))) == 1
    assert not (tmp_path / "events").exists()


def test_unchanged_snapshot_keeps_existing_authoritative_version(tmp_path: Path):
    payload = snapshot_csv([("SG1", "Pending")])
    first = run_ipos_snapshot_cycle(tmp_path, downloader=downloader(payload, 22))
    second = run_ipos_snapshot_cycle(tmp_path, downloader=downloader(payload, 23))

    assert second.status == "UNCHANGED"
    assert second.manifest.content_hash == first.manifest.content_hash
    assert second.manifest.retrieved_at == first.manifest.retrieved_at
    assert len(list((tmp_path / "snapshots").glob("*.csv"))) == 1
    assert not list((tmp_path / "events").glob("*.jsonl"))


def test_changed_snapshot_emits_deltas_then_discards_old_full_snapshot(tmp_path: Path):
    previous = snapshot_csv([("SG1", "Pending"), ("SG2", "Registered")])
    current = snapshot_csv([("SG1", "Registered"), ("SG3", "Pending")])
    first = run_ipos_snapshot_cycle(tmp_path, downloader=downloader(previous, 22))
    second = run_ipos_snapshot_cycle(tmp_path, downloader=downloader(current, 23))

    assert second.status == "CHANGED"
    assert second.event_count == 3
    assert second.events_path is not None
    events = [json.loads(line) for line in second.events_path.read_text().splitlines()]
    assert [(event["entity_id"], event["event_type"]) for event in events] == [
        ("SG1", "UPDATE_DETECTED"),
        ("SG3", "CREATE_DETECTED"),
        ("SG2", "DELETE_DETECTED"),
    ]
    assert all(event["detected_at"].startswith("2026-08-23T01:00:00") for event in events)
    assert read_pointer(tmp_path)["content_hash"] == second.manifest.content_hash
    assert not (tmp_path / "snapshots" / f"{first.manifest.content_hash}.csv").exists()
    assert len(list((tmp_path / "snapshots").glob("*.csv"))) == 1


def test_changed_cycle_keeps_manifest_and_event_evidence_after_snapshot_rotation(tmp_path: Path):
    first = run_ipos_snapshot_cycle(
        tmp_path,
        downloader=downloader(snapshot_csv([("SG1", "Pending")]), 22),
    )
    second = run_ipos_snapshot_cycle(
        tmp_path,
        downloader=downloader(snapshot_csv([("SG1", "Registered")]), 23),
    )

    assert second.events_path is not None
    event = json.loads(second.events_path.read_text(encoding="utf-8").strip())
    assert event["before_evidence_reference"].endswith(first.manifest.content_hash)
    assert event["after_evidence_reference"].endswith(second.manifest.content_hash)
    current_manifest_path = tmp_path / "snapshots" / (
        f"{second.manifest.content_hash}.manifest.json"
    )
    assert current_manifest_path.exists()


def test_lifecycle_rejects_custom_downloader_that_bypasses_full_schema_gate(tmp_path: Path):
    first = run_ipos_snapshot_cycle(
        tmp_path,
        downloader=downloader(snapshot_csv([("SG1", "Registered")]), 22),
    )
    pointer_before = read_pointer(tmp_path)
    accepted_snapshot = tmp_path / first.manifest.storage_reference
    accepted_bytes = accepted_snapshot.read_bytes()

    incomplete = "Application Number,Mark Status\nSG2,Pending\n"
    with pytest.raises(ValueError, match="IPOS snapshot schema drift: missing="):
        run_ipos_snapshot_cycle(
            tmp_path,
            downloader=downloader(incomplete, 23),
        )

    assert read_pointer(tmp_path) == pointer_before
    assert accepted_snapshot.read_bytes() == accepted_bytes
    assert len(list((tmp_path / "snapshots").glob("*.csv"))) == 1
    assert not list((tmp_path / "events").glob("*.jsonl"))
