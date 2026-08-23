import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import app.snapshot_delta.lifecycle as lifecycle
from app.snapshot_delta.acquisition import AcquiredSnapshot
from app.snapshot_delta.ipos_sg import IPOS_SG_TRADEMARK_APPLICATIONS
from app.snapshot_delta.ipos_sg_full_acceptance import (
    FullCorpusAcceptanceError,
    run_ipos_full_corpus_acceptance,
    write_acceptance_report,
)
from app.snapshot_delta.ipos_sg_schema_contract import IPOS_NATIVE_CSV_SOURCE_FIELDS


class FakeDownloader:
    def __init__(self, payload: str, day: int) -> None:
        self.payload = payload
        self.retrieved_at = datetime(2026, 8, day, 2, 0, tzinfo=timezone.utc)

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


def snapshot_csv(rows: list[tuple[str, str]]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=IPOS_NATIVE_CSV_SOURCE_FIELDS)
    writer.writeheader()
    for entity_id, status in rows:
        writer.writerow({"Application Number": entity_id, "Mark Status": status})
    return stream.getvalue()


def clock(*values: float):
    iterator = iter(values)
    return lambda: next(iterator)


def test_full_corpus_acceptance_bootstraps_and_reports_runtime_evidence(tmp_path: Path):
    payload = snapshot_csv([("SG1", "Pending"), ("SG2", "Registered")])

    report = run_ipos_full_corpus_acceptance(
        tmp_path,
        downloader=FakeDownloader(payload, 22),
        clock=clock(10.0, 12.5),
    )

    assert report.status == "BOOTSTRAPPED"
    assert report.dataset_id == "d_6145acb2130bf781165258e76a584383"
    assert report.row_count == 2
    assert report.bytes_downloaded == len(payload.encode("utf-8"))
    assert report.current_snapshot_bytes == report.bytes_downloaded
    assert report.retained_full_snapshot_count == 1
    assert report.elapsed_seconds == 2.5
    assert report.event_count == 0
    assert report.events_path is None
    assert report.native_change_count == 0
    assert report.native_changes_path is None
    assert len(report.content_hash) == 64
    assert len(report.schema_hash) == 64


def test_full_corpus_acceptance_changed_cycle_retains_durable_delta_and_native_evidence(
    tmp_path: Path,
):
    first = snapshot_csv([("SG1", "Pending"), ("SG2", "Registered")])
    second = snapshot_csv([("SG1", "Registered"), ("SG3", "Pending")])
    run_ipos_full_corpus_acceptance(
        tmp_path,
        downloader=FakeDownloader(first, 22),
        clock=clock(1.0, 2.0),
    )

    report = run_ipos_full_corpus_acceptance(
        tmp_path,
        downloader=FakeDownloader(second, 23),
        clock=clock(3.0, 5.0),
    )

    assert report.status == "CHANGED"
    assert report.event_count == 3
    assert report.events_path is not None
    assert Path(report.events_path).exists()
    assert report.native_change_count == 1
    assert report.native_changes_path is not None
    assert Path(report.native_changes_path).exists()
    assert report.retained_full_snapshot_count == 1
    assert len(list((tmp_path / "snapshots").glob("*.csv"))) == 1


def test_full_corpus_acceptance_rejects_pending_post_commit_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    first_payload = snapshot_csv([("SG1", "Pending")])
    second_payload = snapshot_csv([("SG1", "Registered")])
    run_ipos_full_corpus_acceptance(
        tmp_path,
        downloader=FakeDownloader(first_payload, 22),
        clock=clock(1.0, 2.0),
    )
    original_remove_snapshot = lifecycle._remove_snapshot
    failures = 0

    def fail_first_removal(path: Path) -> None:
        nonlocal failures
        if failures == 0:
            failures += 1
            raise PermissionError("snapshot temporarily locked")
        original_remove_snapshot(path)

    monkeypatch.setattr(lifecycle, "_remove_snapshot", fail_first_removal)

    with pytest.raises(FullCorpusAcceptanceError, match="snapshot cleanup remains pending"):
        run_ipos_full_corpus_acceptance(
            tmp_path,
            downloader=FakeDownloader(second_payload, 23),
            clock=clock(3.0, 5.0),
        )

    assert len(list((tmp_path / "snapshots").glob("*.csv"))) == 2


def test_acceptance_report_is_machine_readable_and_atomic(tmp_path: Path):
    report = run_ipos_full_corpus_acceptance(
        tmp_path / "state",
        downloader=FakeDownloader(snapshot_csv([("SG1", "Pending")]), 22),
        clock=clock(4.0, 4.75),
    )
    report_path = tmp_path / "evidence" / "acceptance.json"

    write_acceptance_report(report_path, report)
    payload = json.loads(report_path.read_text(encoding="utf-8"))

    assert payload["content_hash"] == report.content_hash
    assert payload["row_count"] == 1
    assert payload["native_change_count"] == 0
    assert payload["native_changes_path"] is None
    assert payload["elapsed_seconds"] == 0.75
    assert payload["completed_at"].endswith("+00:00")
    assert not (report_path.parent / ".acceptance.json.part").exists()
