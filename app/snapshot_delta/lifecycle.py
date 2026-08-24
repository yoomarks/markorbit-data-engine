"""Operational lifecycle for snapshot-first Singapore IPOS activation."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from .acquisition import AcquiredSnapshot, DataGovSgSnapshotDownloader
from .ipos_sg import IPOS_SG_TRADEMARK_APPLICATIONS
from .ipos_sg_native_evidence import (
    IposNativeFamilyEvidence,
    native_family_evidence_for_updates,
)
from .ipos_sg_observation import (
    observations_from_ipos_snapshot,
    validate_ipos_snapshot_schema,
)
from .ipos_sg_pipeline import manifest_evidence_reference
from .ipos_sg_schema_contract import validate_ipos_native_snapshot_schema
from .loader import SnapshotCsvLoader
from .manifest import build_snapshot_manifest
from .models import DeltaEvent, SnapshotManifest
from .runtime import detect_snapshot_deltas


class SnapshotDownloader(Protocol):
    def download(self, destination_directory: str | Path) -> AcquiredSnapshot: ...


SnapshotCandidateValidator = Callable[[SnapshotManifest], None]


@dataclass(frozen=True)
class SnapshotCycleResult:
    status: str
    manifest: SnapshotManifest
    event_count: int = 0
    events_path: Path | None = None
    native_change_count: int = 0
    native_changes_path: Path | None = None
    cleanup_pending_paths: tuple[Path, ...] = ()


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


def _native_evidence_payload(evidence: IposNativeFamilyEvidence) -> dict[str, Any]:
    payload = asdict(evidence)
    payload["detected_at"] = evidence.detected_at.isoformat()
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


def _current_manifest(state_directory: Path) -> tuple[SnapshotManifest, Path, Path] | None:
    pointer_path = state_directory / "current.json"
    if not pointer_path.exists():
        return None

    pointer = _read_json(pointer_path)
    content_hash = str(pointer["content_hash"])
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


def _persist_version(
    state_directory: Path,
    acquired: AcquiredSnapshot,
    manifest: SnapshotManifest,
) -> tuple[SnapshotManifest, Path, Path]:
    snapshot_path, manifest_path = _version_paths(state_directory, manifest.content_hash)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)

    if manifest_path.exists() and snapshot_path.exists():
        existing = _manifest_from_payload(_read_json(manifest_path))
        if existing.content_hash != manifest.content_hash:
            raise ValueError("existing snapshot version has inconsistent manifest")
        acquired.path.unlink(missing_ok=True)
        return existing, snapshot_path, manifest_path

    os.replace(acquired.path, snapshot_path)
    _atomic_write_json(manifest_path, _manifest_payload(manifest))
    return manifest, snapshot_path, manifest_path


def _discard_unaccepted_version(
    state_directory: Path,
    manifest: SnapshotManifest,
    *,
    protected_content_hash: str | None,
) -> None:
    """Remove a candidate version that never became the accepted current pointer."""
    if manifest.content_hash == protected_content_hash:
        return
    snapshot_path, manifest_path = _version_paths(state_directory, manifest.content_hash)
    snapshot_path.unlink(missing_ok=True)
    manifest_path.unlink(missing_ok=True)


def _remove_snapshot(path: Path) -> None:
    path.unlink(missing_ok=True)


def _cleanup_unreferenced_snapshots(
    state_directory: Path,
    *,
    current_content_hash: str,
) -> tuple[Path, ...]:
    """Best-effort cleanup after commit; failed removals remain observable and retryable."""
    snapshots = state_directory / "snapshots"
    current_snapshot, _ = _version_paths(state_directory, current_content_hash)
    pending: list[Path] = []

    for snapshot_path in sorted(snapshots.glob("*.csv")):
        if snapshot_path == current_snapshot:
            continue
        try:
            _remove_snapshot(snapshot_path)
        except OSError:
            pending.append(snapshot_path)

    return tuple(pending)


def _write_events(
    path: Path,
    previous_manifest: SnapshotManifest,
    current_manifest: SnapshotManifest,
    previous_snapshot: Path,
    current_snapshot: Path,
) -> tuple[int, frozenset[str]]:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.part")
    partial.unlink(missing_ok=True)
    count = 0
    updated_application_numbers: set[str] = set()

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
                if event.event_type == "UPDATE_DETECTED":
                    updated_application_numbers.add(event.entity_id)
            target.flush()
            os.fsync(target.fileno())
        os.replace(partial, path)
    except Exception:
        partial.unlink(missing_ok=True)
        raise

    return count, frozenset(updated_application_numbers)


def _write_native_changes(
    path: Path,
    updated_application_numbers: frozenset[str],
    previous_manifest: SnapshotManifest,
    current_manifest: SnapshotManifest,
    previous_snapshot: Path,
    current_snapshot: Path,
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.part")
    partial.unlink(missing_ok=True)
    count = 0

    evidence = native_family_evidence_for_updates(
        observations_from_ipos_snapshot(SnapshotCsvLoader(previous_snapshot)),
        observations_from_ipos_snapshot(SnapshotCsvLoader(current_snapshot)),
        updated_application_numbers,
        detected_at=current_manifest.retrieved_at,
        before_evidence_reference=manifest_evidence_reference(previous_manifest),
        after_evidence_reference=manifest_evidence_reference(current_manifest),
    )

    try:
        with partial.open("w", encoding="utf-8", newline="\n") as target:
            for item in evidence:
                json.dump(
                    _native_evidence_payload(item),
                    target,
                    ensure_ascii=False,
                    sort_keys=True,
                )
                target.write("\n")
                count += 1
            target.flush()
            os.fsync(target.fileno())
        if count:
            os.replace(partial, path)
        else:
            partial.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
    except Exception:
        partial.unlink(missing_ok=True)
        raise

    return count


def run_ipos_snapshot_cycle(
    state_directory: str | Path,
    *,
    downloader: SnapshotDownloader | None = None,
    candidate_validator: SnapshotCandidateValidator | None = None,
) -> SnapshotCycleResult:
    """Acquire one IPOS snapshot and commit only a validated authoritative version."""
    state = Path(state_directory)
    state.mkdir(parents=True, exist_ok=True)
    incoming = state / "incoming"
    active_downloader = downloader or DataGovSgSnapshotDownloader()
    acquired = active_downloader.download(incoming)

    # Everything before _persist_version is still unaccepted incoming state. If a
    # lifecycle-level schema/manifest/candidate check fails, remove that owned input
    # so a failed run cannot masquerade as a durable candidate on the next attempt.
    try:
        loader = SnapshotCsvLoader(acquired.path)
        validate_ipos_snapshot_schema(loader)
        validate_ipos_native_snapshot_schema(loader.fieldnames())

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
        if candidate_validator is not None:
            candidate_validator(candidate)
        current = _current_manifest(state)
    except Exception:
        acquired.path.unlink(missing_ok=True)
        raise

    if current is not None and current[0].content_hash == candidate.content_hash:
        acquired.path.unlink(missing_ok=True)
        cleanup_pending_paths = _cleanup_unreferenced_snapshots(
            state,
            current_content_hash=current[0].content_hash,
        )
        return SnapshotCycleResult(
            status="UNCHANGED",
            manifest=current[0],
            cleanup_pending_paths=cleanup_pending_paths,
        )

    candidate, candidate_snapshot, _ = _persist_version(
        state,
        acquired,
        candidate,
    )
    protected_content_hash = current[0].content_hash if current is not None else None

    if current is None:
        try:
            _publish_pointer(state, candidate)
        except Exception:
            _discard_unaccepted_version(
                state,
                candidate,
                protected_content_hash=protected_content_hash,
            )
            raise
        cleanup_pending_paths = _cleanup_unreferenced_snapshots(
            state,
            current_content_hash=candidate.content_hash,
        )
        return SnapshotCycleResult(
            status="BOOTSTRAPPED",
            manifest=candidate,
            cleanup_pending_paths=cleanup_pending_paths,
        )

    previous_manifest, previous_snapshot, _ = current
    evidence_stem = f"{previous_manifest.content_hash}__{candidate.content_hash}"
    events_path = state / "events" / f"{evidence_stem}.jsonl"
    native_changes_path = state / "native_changes" / f"{evidence_stem}.jsonl"

    try:
        event_count, updated_application_numbers = _write_events(
            events_path,
            previous_manifest,
            candidate,
            previous_snapshot,
            candidate_snapshot,
        )
        native_change_count = _write_native_changes(
            native_changes_path,
            updated_application_numbers,
            previous_manifest,
            candidate,
            previous_snapshot,
            candidate_snapshot,
        )
        if native_change_count == 0:
            native_changes_path = None
        _publish_pointer(state, candidate)
    except Exception:
        # Candidate evidence is committed only with the accepted-current pointer.
        # A pre-pointer failure must leave exactly the previously accepted version.
        events_path.unlink(missing_ok=True)
        if native_changes_path is not None:
            native_changes_path.unlink(missing_ok=True)
        _discard_unaccepted_version(
            state,
            candidate,
            protected_content_hash=protected_content_hash,
        )
        raise

    # Pointer publication is the commit boundary. Old full snapshots are
    # best-effort post-commit cleanup: a filesystem failure must not report the
    # already-committed source version as failed or roll back durable evidence.
    cleanup_pending_paths = _cleanup_unreferenced_snapshots(
        state,
        current_content_hash=candidate.content_hash,
    )

    return SnapshotCycleResult(
        status="CHANGED",
        manifest=candidate,
        event_count=event_count,
        events_path=events_path,
        native_change_count=native_change_count,
        native_changes_path=native_changes_path,
        cleanup_pending_paths=cleanup_pending_paths,
    )
