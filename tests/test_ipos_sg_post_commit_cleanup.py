import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import app.snapshot_delta.lifecycle as lifecycle
from app.snapshot_delta.acquisition import AcquiredSnapshot
from app.snapshot_delta.ipos_sg import IPOS_SG_TRADEMARK_APPLICATIONS
from app.snapshot_delta.ipos_sg_schema_contract import IPOS_NATIVE_CSV_SOURCE_FIELDS


class FakeDownloader:
    def __init__(self, payload: str, day: int) -> None:
        self.payload = payload
        self.retrieved_at = datetime(2026, 8, day, 3, 0, tzinfo=timezone.utc)

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


def snapshot_csv(status: str) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=IPOS_NATIVE_CSV_SOURCE_FIELDS)
    writer.writeheader()
    writer.writerow({"Application Number": "SG1", "Mark Status": status})
    return stream.getvalue()


def current_hash(state: Path) -> str:
    return json.loads((state / "current.json").read_text(encoding="utf-8"))[
        "content_hash"
    ]


def test_post_commit_cleanup_failure_is_reported_and_retried_on_unchanged_cycle(
    tmp_path: Path, monkeypatch
):
    first = lifecycle.run_ipos_snapshot_cycle(
        tmp_path,
        downloader=FakeDownloader(snapshot_csv("Pending"), 22),
    )
    old_snapshot = tmp_path / first.manifest.storage_reference
    original_remove_snapshot = lifecycle._remove_snapshot
    failures: list[Path] = []

    def fail_first_removal(path: Path) -> None:
        if not failures:
            failures.append(path)
            raise PermissionError("snapshot temporarily locked")
        original_remove_snapshot(path)

    monkeypatch.setattr(lifecycle, "_remove_snapshot", fail_first_removal)

    changed_payload = snapshot_csv("Registered")
    second = lifecycle.run_ipos_snapshot_cycle(
        tmp_path,
        downloader=FakeDownloader(changed_payload, 23),
    )

    assert second.status == "CHANGED"
    assert current_hash(tmp_path) == second.manifest.content_hash
    assert second.cleanup_pending_paths == (old_snapshot,)
    assert old_snapshot.exists()
    assert (tmp_path / second.manifest.storage_reference).exists()
    assert len(list((tmp_path / "snapshots").glob("*.csv"))) == 2
    assert second.events_path is not None and second.events_path.exists()
    assert second.native_changes_path is not None and second.native_changes_path.exists()

    third = lifecycle.run_ipos_snapshot_cycle(
        tmp_path,
        downloader=FakeDownloader(changed_payload, 24),
    )

    assert third.status == "UNCHANGED"
    assert third.manifest.content_hash == second.manifest.content_hash
    assert third.cleanup_pending_paths == ()
    assert not old_snapshot.exists()
    assert len(list((tmp_path / "snapshots").glob("*.csv"))) == 1
