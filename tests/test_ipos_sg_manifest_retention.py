from datetime import datetime, timezone
from pathlib import Path

from app.snapshot_delta.acquisition import AcquiredSnapshot
from app.snapshot_delta.ipos_sg import IPOS_SG_TRADEMARK_APPLICATIONS
from app.snapshot_delta.lifecycle import run_ipos_snapshot_cycle


class FakeDownloader:
    def __init__(self, payload: str, day: int):
        self.payload = payload
        self.day = day

    def download(self, destination_directory: str | Path) -> AcquiredSnapshot:
        destination = Path(destination_directory)
        destination.mkdir(parents=True, exist_ok=True)
        path = destination / IPOS_SG_TRADEMARK_APPLICATIONS.filename
        path.write_text(self.payload, encoding="utf-8")
        return AcquiredSnapshot(
            path=path,
            source_uri=IPOS_SG_TRADEMARK_APPLICATIONS.dataset_url,
            retrieved_at=datetime(2026, 8, self.day, tzinfo=timezone.utc),
            bytes_written=path.stat().st_size,
        )


def test_rotations_keep_historical_manifests_but_only_one_full_csv(tmp_path: Path):
    results = []
    for day, status in [(21, "Pending"), (22, "Registered"), (23, "Removed")]:
        results.append(
            run_ipos_snapshot_cycle(
                tmp_path,
                downloader=FakeDownloader(
                    f"Application Number,Mark Status\nSG1,{status}\n",
                    day,
                ),
            )
        )

    snapshots = tmp_path / "snapshots"
    assert len(list(snapshots.glob("*.csv"))) == 1
    assert len(list(snapshots.glob("*.manifest.json"))) == 3
    for result in results:
        assert (snapshots / f"{result.manifest.content_hash}.manifest.json").exists()
    assert not (snapshots / f"{results[0].manifest.content_hash}.csv").exists()
    assert not (snapshots / f"{results[1].manifest.content_hash}.csv").exists()
    assert (snapshots / f"{results[2].manifest.content_hash}.csv").exists()
