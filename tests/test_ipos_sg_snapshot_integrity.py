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
from app.snapshot_delta.manifest import file_sha256


class FakeDownloader:
    def __init__(self, payload: str, *, day: int):
        self.payload = payload
        self.retrieved_at = datetime(2026, 8, day, 1, 0, tzinfo=timezone.utc)

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


def snapshot_csv(status: str = "Pending") -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=IPOS_NATIVE_CSV_SOURCE_FIELDS)
    writer.writeheader()
    writer.writerow({"Application Number": "SG1", "Mark Status": status})
    return stream.getvalue()


def test_identical_cycle_repairs_corrupted_retained_snapshot(tmp_path: Path):
    payload = snapshot_csv()
    first = run_ipos_snapshot_cycle(tmp_path, downloader=FakeDownloader(payload, day=22))
    retained = tmp_path / first.manifest.storage_reference
    retained.write_bytes(b"corrupted retained evidence")

    second = run_ipos_snapshot_cycle(tmp_path, downloader=FakeDownloader(payload, day=23))

    assert second.status == "REPAIRED"
    assert second.manifest.content_hash == first.manifest.content_hash
    assert file_sha256(retained) == first.manifest.content_hash
    assert retained.read_bytes() == payload.encode("utf-8")
    assert not (tmp_path / "incoming" / IPOS_SG_TRADEMARK_APPLICATIONS.filename).exists()


def test_changed_cycle_refuses_delta_when_retained_current_is_corrupt(tmp_path: Path):
    first_payload = snapshot_csv("Pending")
    first = run_ipos_snapshot_cycle(
        tmp_path,
        downloader=FakeDownloader(first_payload, day=22),
    )
    pointer_before = (tmp_path / "current.json").read_bytes()
    retained = tmp_path / first.manifest.storage_reference
    retained.write_bytes(b"tampered prior evidence")

    with pytest.raises(ValueError, match="refusing changed-source delta"):
        run_ipos_snapshot_cycle(
            tmp_path,
            downloader=FakeDownloader(snapshot_csv("Registered"), day=23),
        )

    assert (tmp_path / "current.json").read_bytes() == pointer_before
    assert retained.read_bytes() == b"tampered prior evidence"
    events = tmp_path / "events"
    assert not events.exists() or not list(events.glob("*.jsonl"))
    assert not (tmp_path / "incoming" / IPOS_SG_TRADEMARK_APPLICATIONS.filename).exists()


def test_orphan_reuse_rejects_manifest_identity_drift(tmp_path: Path):
    payload = snapshot_csv()
    first = run_ipos_snapshot_cycle(tmp_path, downloader=FakeDownloader(payload, day=22))
    (tmp_path / "current.json").unlink()
    manifest_path = tmp_path / "snapshots" / f"{first.manifest.content_hash}.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["row_count"] = 2
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="fresh candidate manifest"):
        run_ipos_snapshot_cycle(tmp_path, downloader=FakeDownloader(payload, day=23))

    assert not (tmp_path / "current.json").exists()
    assert (tmp_path / first.manifest.storage_reference).exists()
    assert not (tmp_path / "incoming" / IPOS_SG_TRADEMARK_APPLICATIONS.filename).exists()


def test_orphan_reuse_rejects_physical_hash_mismatch(tmp_path: Path):
    payload = snapshot_csv()
    first = run_ipos_snapshot_cycle(tmp_path, downloader=FakeDownloader(payload, day=22))
    (tmp_path / "current.json").unlink()
    retained = tmp_path / first.manifest.storage_reference
    retained.write_bytes(b"tampered orphan snapshot")

    with pytest.raises(ValueError, match="does not match its SHA-256 identity"):
        run_ipos_snapshot_cycle(tmp_path, downloader=FakeDownloader(payload, day=23))

    assert not (tmp_path / "current.json").exists()
    assert retained.read_bytes() == b"tampered orphan snapshot"
    assert not (tmp_path / "incoming" / IPOS_SG_TRADEMARK_APPLICATIONS.filename).exists()
