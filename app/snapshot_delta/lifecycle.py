"""Operational lifecycle for snapshot-first Singapore IPOS activation."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from .acquisition import AcquiredSnapshot, DataGovSgSnapshotDownloader
from .ipos_sg import IPOS_SG_TRADEMARK_APPLICATIONS
from .ipos_sg_observation import observations_from_ipos_snapshot
from .ipos_sg_pipeline import manifest_evidence_reference
from .loader import SnapshotCsvLoader
from .manifest import build_snapshot_manifest
from .models import DeltaEvent, SnapshotManifest
from .runtime import detect_snapshot_deltas

_PENDING_CYCLE_FILE = "pending-cycle.json"
_PENDING_CYCLE_VERSION = 1


class SnapshotDownloader(Protocol):
    def download(self, destination_directory: str | Path) -> AcquiredSnapshot: ...


@dataclass(frozen=True)
class SnapshotCycleResult:
    status: str
    manifest: SnapshotManifest
    event_count: int = 0
    events_path: Path | None = None


def _manifest_payload(manifest: SnapshotManifest) -> dict[str, Any]:
    payload = asdict(manifest)
    payload["retrieved_at"] = manifest.retrieved_at.isoformat()
    return payload


def _manifest_from_payload(payload: dict[str, Any]) -> SnapshotManifest:
    return SnapshotManifest(
        jurisdiction=str(payload["jurisdiction"]),
        source_id=str(payload["source_id"]),
        dataset_id=str(payload["dataset_id"]),
        retrieved_at=datetime.fromisoformat(str(payload["retrieved_at"])),
        source_uri=str(payload["source_uri"]),
        schema_hash=str(payload["schema_hash"]),
        content_hash=str(payload["content_hash"]),
        row_count=int(payload["row_count"]),
        storage_reference=str(payload["storage_reference"]),
    )


def _event_payload(event: DeltaEvent) -> dict[str, Any]:
    payload = asdict(event)
    payload["detected_at"] = event.detected_at.isoformat()
    return payload


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.part")
    try:
        with partial.open("w", encoding="utf-8", newline="\n") as target:
            json.dump(payload, target, ensure_ascii=False, sort_keys=True)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(partial, path)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object at {path}")
    return payload


def _version_paths(state_directory: Path, content_hash: str) -> tuple[Path, Path]:
    snapshots = state_directory / "snapshots"
    return (
        snapshots / f"{content_hash}.csv",
        snapshots / f"{content_hash}.manifest.json",
    )


def _events_path(
    state_directory: Path,
    previous_content_hash: str | None,
    candidate_content_hash: str,
) -> Path | None:
    if previous_content_hash is None:
        return None
    return state_directory / "events" / (
        f"{previous_content_hash}__{candidate_content_hash}.jsonl"
    )


def _current_pointer_hash(state_directory: Path) -> str | None:
    pointer_path = state_directory / "current.json"
    if not pointer_path.exists():
        return None
    return str(_read_json(pointer_path)["content_hash"])


def _current_manifest(state_directory: Path) -> tuple[SnapshotManifest, Path, Path] | None:
    content_hash = _current_pointer_hash(state_directory)
    if content_hash is None:
        return None

    snapshot_path, manifest_path = _version_paths(state_directory, content_hash)
    if not snapshot_path.exists() or not manifest_path.exists():
        raise ValueError("current snapshot pointer references missing state files")

    manifest = _manifest_from_payload(_read_json(manifest_path))
    if manifest.content_hash != content_hash:
        raise ValueError("current snapshot pointer and manifest content hash disagree")
    return manifest, snapshot_path, manifest_path


def _publish_pointer(state_directory: Path, manifest: SnapshotManifest) -> None:
    _atomic_write_json(
        state_directory / "current.json",
        {
            "content_hash": manifest.content_hash,
            "storage_reference": manifest.storage_reference,
        },
    )


def _pending_cycle_path(state_directory: Path) -> Path:
    return state_directory / _PENDING_CYCLE_FILE


def _write_pending_cycle(
    state_directory: Path,
    *,
    previous_content_hash: str | None,
    candidate_content_hash: str,
) -> None:
    candidate_snapshot, candidate_manifest = _version_paths(
        state_directory,
        candidate_content_hash,
    )
    existing_manifest = (
        _read_json(candidate_manifest) if candidate_manifest.exists() else None
    )
    _atomic_write_json(
        _pending_cycle_path(state_directory),
        {
            "version": _PENDING_CYCLE_VERSION,
            "previous_content_hash": previous_content_hash,
            "candidate_content_hash": candidate_content_hash,
            "candidate_snapshot_preexisting": candidate_snapshot.exists(),
            "candidate_manifest_preexisting": existing_manifest,
        },
    )


def _clear_pending_cycle(state_directory: Path) -> None:
    pending = _pending_cycle_path(state_directory)
    pending.unlink(missing_ok=True)
    pending.with_name(f".{pending.name}.part").unlink(missing_ok=True)


def _retire_previous_snapshot(previous_snapshot: Path) -> None:
    previous_snapshot.unlink(missing_ok=True)


def _recover_pending_cycle(state_directory: Path) -> None:
    """Finish or roll back an interrupted cycle before any new acquisition."""
    pending = _pending_cycle_path(state_directory)
    partial = pending.with_name(f".{pending.name}.part")
    if not pending.exists():
        # A partial journal can only precede candidate persistence because the
        # durable journal is written first. It is therefore safe to discard.
        partial.unlink(missing_ok=True)
        return

    payload = _read_json(pending)
    if int(payload.get("version", 0)) != _PENDING_CYCLE_VERSION:
        raise ValueError("unsupported pending snapshot cycle version")

    previous_hash_value = payload.get("previous_content_hash")
    previous_hash = str(previous_hash_value) if previous_hash_value else None
    candidate_hash = str(payload["candidate_content_hash"])
    candidate_snapshot, candidate_manifest = _version_paths(
        state_directory,
        candidate_hash,
    )
    event_path = _events_path(state_directory, previous_hash, candidate_hash)
    pointer_hash = _current_pointer_hash(state_directory)

    if pointer_hash == candidate_hash:
        if not candidate_snapshot.exists() or not candidate_manifest.exists():
            raise ValueError("committed snapshot cycle is missing candidate evidence")
        if event_path is not None and not event_path.exists():
            raise ValueError("committed snapshot cycle is missing delta event evidence")
        if previous_hash is not None and previous_hash != candidate_hash:
            previous_snapshot, _ = _version_paths(state_directory, previous_hash)
            _retire_previous_snapshot(previous_snapshot)
        _clear_pending_cycle(state_directory)
        return

    if pointer_hash != previous_hash:
        raise ValueError("pending snapshot cycle disagrees with current pointer")

    snapshot_preexisting = bool(payload.get("candidate_snapshot_preexisting", False))
    if not snapshot_preexisting:
        candidate_snapshot.unlink(missing_ok=True)

    previous_manifest_payload = payload.get("candidate_manifest_preexisting")
    if previous_manifest_payload is None:
        candidate_manifest.unlink(missing_ok=True)
    elif isinstance(previous_manifest_payload, dict):
        _atomic_write_json(candidate_manifest, previous_manifest_payload)
    else:
        raise ValueError("pending snapshot cycle has invalid manifest backup")

    if event_path is not None:
        event_path.unlink(missing_ok=True)
        event_path.with_name(f".{event_path.name}.part").unlink(missing_ok=True)
    _clear_pending_cycle(state_directory)


def _persist_version(
    state_directory: Path,
    acquired: AcquiredSnapshot,
    manifest: SnapshotManifest,
) -> tuple[SnapshotManifest, Path, Path]:
    snapshot_path, manifest_path = _version_paths(state_directory, manifest.content_hash)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)

    if snapshot_path.exists() and not manifest_path.exists():
        raise ValueError("snapshot version exists without its manifest")

    if manifest_path.exists() and snapshot_path.exists():
        existing = _manifest_from_payload(_read_json(manifest_path))
        if existing.content_hash != manifest.content_hash:
            raise ValueError("existing snapshot version has inconsistent manifest")
        acquired.path.unlink(missing_ok=True)
        return existing, snapshot_path, manifest_path

    os.replace(acquired.path, snapshot_path)
    _atomic_write_json(manifest_path, _manifest_payload(manifest))
    return manifest, snapshot_path, manifest_path


def _write_events(
    path: Path,
    previous_manifest: SnapshotManifest,
    current_manifest: SnapshotManifest,
    previous_snapshot: Path,
    current_snapshot: Path,
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.part")
    partial.unlink(missing_ok=True)
    count = 0

    events = detect_snapshot_deltas(
        observations_from_ipos_snapshot(SnapshotCsvLoader(previous_snapshot)),
        observations_from_ipos_snapshot(SnapshotCsvLoader(current_snapshot)),
        previous_evidence_reference=manifest_evidence_reference(previous_manifest),
        current_evidence_reference=manifest_evidence_reference(current_manifest),
        detected_at=current_manifest.retrieved_at,
    )

    try:
        with partial.open("w", encoding="utf-8", newline="\n") as target:
            for event in events:
                json.dump(_event_payload(event), target, ensure_ascii=False, sort_keys=True)
                target.write("\n")
                count += 1
            target.flush()
            os.fsync(target.fileno())
        os.replace(partial, path)
    except Exception:
        partial.unlink(missing_ok=True)
        raise

    return count


def run_ipos_snapshot_cycle(
    state_directory: str | Path,
    *,
    downloader: SnapshotDownloader | None = None,
) -> SnapshotCycleResult:
    """Acquire one IPOS snapshot and atomically advance authoritative state."""
    state = Path(state_directory)
    state.mkdir(parents=True, exist_ok=True)
    _recover_pending_cycle(state)

    incoming = state / "incoming"
    active_downloader = downloader or DataGovSgSnapshotDownloader()
    acquired = active_downloader.download(incoming)

    loader = SnapshotCsvLoader(acquired.path)
    provisional = build_snapshot_manifest(
        loader,
        IPOS_SG_TRADEMARK_APPLICATIONS,
        jurisdiction="SG",
        retrieved_at=acquired.retrieved_at,
        source_uri=acquired.source_uri,
        storage_reference="pending",
    )
    storage_reference = f"snapshots/{provisional.content_hash}.csv"
    candidate = replace(provisional, storage_reference=storage_reference)

    current = _current_manifest(state)
    if current is not None and current[0].content_hash == candidate.content_hash:
        acquired.path.unlink(missing_ok=True)
        return SnapshotCycleResult(status="UNCHANGED", manifest=current[0])

    previous_manifest = current[0] if current is not None else None
    previous_hash = (
        previous_manifest.content_hash if previous_manifest is not None else None
    )
    event_path = _events_path(state, previous_hash, candidate.content_hash)

    _write_pending_cycle(
        state,
        previous_content_hash=previous_hash,
        candidate_content_hash=candidate.content_hash,
    )

    candidate, candidate_snapshot, _ = _persist_version(
        state,
        acquired,
        candidate,
    )

    if current is None:
        _publish_pointer(state, candidate)
        _clear_pending_cycle(state)
        return SnapshotCycleResult(status="BOOTSTRAPPED", manifest=candidate)

    previous_manifest, previous_snapshot, _ = current
    assert event_path is not None
    event_count = _write_events(
        event_path,
        previous_manifest,
        candidate,
        previous_snapshot,
        candidate_snapshot,
    )
    _publish_pointer(state, candidate)

    # Pointer publication is the commit boundary. Recovery will finish this
    # rotation if the process stops between commit and cleanup.
    _retire_previous_snapshot(previous_snapshot)
    _clear_pending_cycle(state)

    return SnapshotCycleResult(
        status="CHANGED",
        manifest=candidate,
        event_count=event_count,
        events_path=event_path,
    )
