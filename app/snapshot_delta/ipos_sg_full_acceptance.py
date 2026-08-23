"""Operator-grade full-corpus acceptance for Singapore IPOS snapshots."""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .acquisition import AcquiredSnapshot, DataGovSgSnapshotDownloader
from .ipos_sg import IPOS_SG_TRADEMARK_APPLICATIONS
from .lifecycle import SnapshotCycleResult, SnapshotDownloader, run_ipos_snapshot_cycle


class FullCorpusAcceptanceError(RuntimeError):
    """Raised when a full-corpus run violates snapshot lifecycle invariants."""


@dataclass(frozen=True)
class FullCorpusAcceptanceReport:
    dataset_id: str
    completed_at: datetime
    status: str
    content_hash: str
    schema_hash: str
    row_count: int
    bytes_downloaded: int
    current_snapshot_bytes: int
    event_count: int
    native_change_count: int
    retained_full_snapshot_count: int
    elapsed_seconds: float
    storage_reference: str
    events_path: str | None
    native_changes_path: str | None


class _CapturingDownloader:
    def __init__(self, delegate: SnapshotDownloader) -> None:
        self.delegate = delegate
        self.acquired: AcquiredSnapshot | None = None

    def download(self, destination_directory: str | Path) -> AcquiredSnapshot:
        acquired = self.delegate.download(destination_directory)
        self.acquired = acquired
        return acquired


def _validate_committed_state(
    state: Path,
    result: SnapshotCycleResult,
    acquired: AcquiredSnapshot,
) -> tuple[int, int]:
    if result.cleanup_pending_paths:
        pending = ", ".join(str(path) for path in result.cleanup_pending_paths)
        raise FullCorpusAcceptanceError(f"snapshot cleanup remains pending: {pending}")

    snapshots_directory = state / "snapshots"
    full_snapshots = list(snapshots_directory.glob("*.csv"))
    if len(full_snapshots) != 1:
        raise FullCorpusAcceptanceError(
            f"expected exactly one retained full snapshot, found {len(full_snapshots)}"
        )

    current_snapshot = state / result.manifest.storage_reference
    if not current_snapshot.exists():
        raise FullCorpusAcceptanceError("current pointer references a missing snapshot")

    current_manifest = snapshots_directory / f"{result.manifest.content_hash}.manifest.json"
    if not current_manifest.exists():
        raise FullCorpusAcceptanceError("current snapshot manifest is missing")

    pointer_path = state / "current.json"
    if not pointer_path.exists():
        raise FullCorpusAcceptanceError("current snapshot pointer is missing")
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    if not isinstance(pointer, dict) or pointer.get("content_hash") != result.manifest.content_hash:
        raise FullCorpusAcceptanceError("current snapshot pointer content hash is inconsistent")

    if result.manifest.row_count < 1:
        raise FullCorpusAcceptanceError("accepted corpus contains no rows")
    if acquired.bytes_written < 1:
        raise FullCorpusAcceptanceError("accepted corpus reports zero downloaded bytes")

    if result.status == "CHANGED":
        if result.events_path is None or not result.events_path.exists():
            raise FullCorpusAcceptanceError("changed corpus is missing durable delta evidence")
        if result.native_change_count > 0:
            if result.native_changes_path is None or not result.native_changes_path.exists():
                raise FullCorpusAcceptanceError(
                    "changed corpus is missing durable native-family evidence"
                )
        elif result.native_changes_path is not None:
            raise FullCorpusAcceptanceError(
                "zero native-family changes unexpectedly produced an evidence path"
            )
    else:
        if result.events_path is not None:
            raise FullCorpusAcceptanceError(
                "non-changed corpus unexpectedly produced an event path"
            )
        if result.native_change_count or result.native_changes_path is not None:
            raise FullCorpusAcceptanceError(
                "non-changed corpus unexpectedly produced native-family evidence"
            )

    return current_snapshot.stat().st_size, len(full_snapshots)


def run_ipos_full_corpus_acceptance(
    state_directory: str | Path,
    *,
    downloader: SnapshotDownloader | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> FullCorpusAcceptanceReport:
    """Run one real-sized lifecycle and return machine-readable acceptance evidence."""
    state = Path(state_directory)
    delegate = downloader or DataGovSgSnapshotDownloader(
        api_key=os.getenv("DATA_GOV_SG_API_KEY") or None
    )
    capture = _CapturingDownloader(delegate)

    started = clock()
    result = run_ipos_snapshot_cycle(state, downloader=capture)
    elapsed = max(0.0, clock() - started)

    acquired = capture.acquired
    if acquired is None:
        raise FullCorpusAcceptanceError("snapshot downloader returned no acquisition evidence")

    current_snapshot_bytes, retained_count = _validate_committed_state(
        state,
        result,
        acquired,
    )

    return FullCorpusAcceptanceReport(
        dataset_id=IPOS_SG_TRADEMARK_APPLICATIONS.dataset_id,
        completed_at=datetime.now(timezone.utc),
        status=result.status,
        content_hash=result.manifest.content_hash,
        schema_hash=result.manifest.schema_hash,
        row_count=result.manifest.row_count,
        bytes_downloaded=acquired.bytes_written,
        current_snapshot_bytes=current_snapshot_bytes,
        event_count=result.event_count,
        native_change_count=result.native_change_count,
        retained_full_snapshot_count=retained_count,
        elapsed_seconds=round(elapsed, 6),
        storage_reference=result.manifest.storage_reference,
        events_path=str(result.events_path) if result.events_path else None,
        native_changes_path=(
            str(result.native_changes_path) if result.native_changes_path else None
        ),
    )


def report_payload(report: FullCorpusAcceptanceReport) -> dict[str, Any]:
    payload = asdict(report)
    payload["completed_at"] = report.completed_at.isoformat()
    return payload


def write_acceptance_report(path: str | Path, report: FullCorpusAcceptanceReport) -> Path:
    """Persist acceptance evidence atomically so interrupted runs cannot publish partial JSON."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f".{destination.name}.part")
    try:
        with partial.open("w", encoding="utf-8", newline="\n") as target:
            json.dump(report_payload(report), target, ensure_ascii=False, sort_keys=True)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(partial, destination)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one full Singapore IPOS corpus lifecycle and emit acceptance evidence"
    )
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument(
        "--report-path",
        type=Path,
        default=None,
        help="Defaults to <state-dir>/acceptance/latest.json",
    )
    args = parser.parse_args()

    report = run_ipos_full_corpus_acceptance(args.state_dir)
    report_path = args.report_path or args.state_dir / "acceptance" / "latest.json"
    write_acceptance_report(report_path, report)
    print(json.dumps(report_payload(report), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
