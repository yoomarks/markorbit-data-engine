import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.snapshot_delta import lifecycle
from app.snapshot_delta.acquisition import AcquiredSnapshot
from app.snapshot_delta.ipos_sg import IPOS_SG_TRADEMARK_APPLICATIONS


class FakeDownloader:
    def __init__(self, status: str, day: int):
        self.status = status
        self.day = day

    def download(self, destination_directory: str | Path) -> AcquiredSnapshot:
        destination = Path(destination_directory)
        destination.mkdir(parents=True, exist_ok=True)
        path = destination / IPOS_SG_TRADEMARK_APPLICATIONS.filename
        payload = f"Application Number,Mark Status\nSG1,{self.status}\n"
        path.write_text(payload, encoding="utf-8")
        return AcquiredSnapshot(
            path=path,
            source_uri=IPOS_SG_TRADEMARK_APPLICATIONS.dataset_url,
            retrieved_at=datetime(2026, 8, self.day, 1, 0, tzinfo=timezone.utc),
            bytes_written=path.stat().st_size,
        )


def run(state: Path, status: str, day: int):
    return lifecycle.run_ipos_snapshot_cycle(
        state,
        downloader=FakeDownloader(status, day),
    )


def pointer_hash(state: Path) -> str | None:
    path = state / "current.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))["content_hash"]


def full_snapshots(state: Path) -> list[Path]:
    return list((state / "snapshots").glob("*.csv"))


def manifests(state: Path) -> list[Path]:
    return list((state / "snapshots").glob("*.manifest.json"))


def event_files(state: Path) -> list[Path]:
    return list((state / "events").glob("*.jsonl"))


def test_bootstrap_crash_rolls_back_before_next_acquisition(monkeypatch, tmp_path: Path):
    original_publish = lifecycle._publish_pointer

    def crash_before_pointer(*args, **kwargs):
        raise RuntimeError("simulated pointer crash")

    monkeypatch.setattr(lifecycle, "_publish_pointer", crash_before_pointer)
    with pytest.raises(RuntimeError, match="pointer crash"):
        run(tmp_path, "Pending", 21)

    assert pointer_hash(tmp_path) is None
    assert (tmp_path / "pending-cycle.json").exists()
    assert len(full_snapshots(tmp_path)) == 1

    monkeypatch.setattr(lifecycle, "_publish_pointer", original_publish)
    recovered = run(tmp_path, "Registered", 22)

    assert recovered.status == "BOOTSTRAPPED"
    assert pointer_hash(tmp_path) == recovered.manifest.content_hash
    assert len(full_snapshots(tmp_path)) == 1
    assert len(manifests(tmp_path)) == 1
    assert not (tmp_path / "pending-cycle.json").exists()
    assert not event_files(tmp_path)


def test_precommit_candidate_and_events_are_rolled_back(monkeypatch, tmp_path: Path):
    first = run(tmp_path, "Pending", 21)
    original_publish = lifecycle._publish_pointer

    def crash_before_pointer(*args, **kwargs):
        raise RuntimeError("simulated pointer crash")

    monkeypatch.setattr(lifecycle, "_publish_pointer", crash_before_pointer)
    with pytest.raises(RuntimeError, match="pointer crash"):
        run(tmp_path, "Registered", 22)

    interrupted_hashes = {
        path.name.removesuffix(".csv") for path in full_snapshots(tmp_path)
    }
    assert first.manifest.content_hash in interrupted_hashes
    assert len(interrupted_hashes) == 2
    assert len(event_files(tmp_path)) == 1
    assert pointer_hash(tmp_path) == first.manifest.content_hash

    monkeypatch.setattr(lifecycle, "_publish_pointer", original_publish)
    third = run(tmp_path, "Removed", 23)

    assert third.status == "CHANGED"
    assert pointer_hash(tmp_path) == third.manifest.content_hash
    assert len(full_snapshots(tmp_path)) == 1
    assert len(manifests(tmp_path)) == 2
    assert [path.name for path in event_files(tmp_path)] == [
        f"{first.manifest.content_hash}__{third.manifest.content_hash}.jsonl"
    ]
    assert not (tmp_path / "pending-cycle.json").exists()


def test_candidate_persisted_before_event_failure_is_rolled_back(
    monkeypatch,
    tmp_path: Path,
):
    first = run(tmp_path, "Pending", 21)
    original_write_events = lifecycle._write_events

    def crash_during_events(*args, **kwargs):
        raise RuntimeError("simulated event crash")

    monkeypatch.setattr(lifecycle, "_write_events", crash_during_events)
    with pytest.raises(RuntimeError, match="event crash"):
        run(tmp_path, "Registered", 22)

    assert len(full_snapshots(tmp_path)) == 2
    assert len(manifests(tmp_path)) == 2
    assert pointer_hash(tmp_path) == first.manifest.content_hash
    assert (tmp_path / "pending-cycle.json").exists()

    monkeypatch.setattr(lifecycle, "_write_events", original_write_events)
    third = run(tmp_path, "Removed", 23)

    assert pointer_hash(tmp_path) == third.manifest.content_hash
    assert len(full_snapshots(tmp_path)) == 1
    assert len(manifests(tmp_path)) == 2
    assert [path.name for path in event_files(tmp_path)] == [
        f"{first.manifest.content_hash}__{third.manifest.content_hash}.jsonl"
    ]


def test_postcommit_recovery_finishes_previous_snapshot_retirement(
    monkeypatch,
    tmp_path: Path,
):
    first = run(tmp_path, "Pending", 21)
    original_retire = lifecycle._retire_previous_snapshot

    def crash_before_retirement(*args, **kwargs):
        raise RuntimeError("simulated cleanup crash")

    monkeypatch.setattr(lifecycle, "_retire_previous_snapshot", crash_before_retirement)
    with pytest.raises(RuntimeError, match="cleanup crash"):
        run(tmp_path, "Registered", 22)

    committed_hash = pointer_hash(tmp_path)
    assert committed_hash is not None
    assert committed_hash != first.manifest.content_hash
    assert len(full_snapshots(tmp_path)) == 2
    assert len(event_files(tmp_path)) == 1
    assert (tmp_path / "pending-cycle.json").exists()

    monkeypatch.setattr(lifecycle, "_retire_previous_snapshot", original_retire)
    third = run(tmp_path, "Removed", 23)

    assert pointer_hash(tmp_path) == third.manifest.content_hash
    assert len(full_snapshots(tmp_path)) == 1
    assert len(manifests(tmp_path)) == 3
    assert sorted(path.name for path in event_files(tmp_path)) == sorted(
        [
            f"{first.manifest.content_hash}__{committed_hash}.jsonl",
            f"{committed_hash}__{third.manifest.content_hash}.jsonl",
        ]
    )
    assert not (tmp_path / "pending-cycle.json").exists()
