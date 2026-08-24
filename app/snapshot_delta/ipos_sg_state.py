"""Fast read-only state audit for the Singapore IPOS snapshot lifecycle."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .ipos_sg import IPOS_SG_TRADEMARK_APPLICATIONS


IPOS_SG_STATE_AUDIT_VERSION = "IPOS_SG_STATE_AUDIT_V1"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class IposStateIssue:
    code: str
    detail: str
    blocking: bool


@dataclass(frozen=True)
class IposStateAudit:
    version: str
    checked_at: datetime
    status: str
    safe_to_run: bool
    current_content_hash: str | None
    retained_full_snapshot_count: int
    orphan_full_snapshot_count: int
    transient_part_paths: tuple[str, ...]
    issues: tuple[IposStateIssue, ...]


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object at {path}")
    return payload


def _safe_state_path(state: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute():
        raise ValueError("state storage_reference must be relative")
    resolved_state = state.resolve()
    resolved = (resolved_state / relative).resolve()
    if resolved != resolved_state and resolved_state not in resolved.parents:
        raise ValueError("state storage_reference escapes state directory")
    return resolved


def audit_ipos_state(state_directory: str | Path) -> IposStateAudit:
    """Inspect lifecycle state without hashing or reading the multi-GB corpus body."""
    state = Path(state_directory)
    snapshots = state / "snapshots"
    pointer_path = state / "current.json"
    full_snapshots = tuple(sorted(snapshots.glob("*.csv"))) if snapshots.exists() else ()
    transient_parts = (
        tuple(sorted(str(path.relative_to(state)) for path in state.rglob("*.part")))
        if state.exists()
        else ()
    )

    issues: list[IposStateIssue] = []
    current_content_hash: str | None = None
    current_snapshot: Path | None = None

    if not pointer_path.exists():
        if full_snapshots:
            issues.append(
                IposStateIssue(
                    code="ORPHAN_SNAPSHOT_WITHOUT_CURRENT_POINTER",
                    detail=(
                        f"{len(full_snapshots)} full snapshot(s) exist without current.json; "
                        "the lifecycle can replace or reuse them on the next authenticated run"
                    ),
                    blocking=False,
                )
            )
    else:
        try:
            pointer = _read_json(pointer_path)
            content_hash = str(pointer.get("content_hash") or "")
            storage_reference = str(pointer.get("storage_reference") or "")
            if not _HASH_RE.fullmatch(content_hash):
                raise ValueError("current pointer content_hash is not a lowercase SHA-256")
            if storage_reference != f"snapshots/{content_hash}.csv":
                raise ValueError("current pointer storage_reference does not match content_hash")
            current_snapshot = _safe_state_path(state, storage_reference)
            if not current_snapshot.exists():
                raise ValueError("current pointer references a missing full snapshot")

            manifest_path = snapshots / f"{content_hash}.manifest.json"
            if not manifest_path.exists():
                raise ValueError("current pointer references a missing manifest")
            manifest = _read_json(manifest_path)
            if str(manifest.get("content_hash") or "") != content_hash:
                raise ValueError("current manifest content_hash disagrees with current pointer")
            if str(manifest.get("storage_reference") or "") != storage_reference:
                raise ValueError("current manifest storage_reference disagrees with current pointer")
            if str(manifest.get("jurisdiction") or "") != "SG":
                raise ValueError("current manifest jurisdiction is not SG")
            if str(manifest.get("source_id") or "") != IPOS_SG_TRADEMARK_APPLICATIONS.source_id:
                raise ValueError("current manifest source_id is not the canonical IPOS source")
            if str(manifest.get("dataset_id") or "") != IPOS_SG_TRADEMARK_APPLICATIONS.dataset_id:
                raise ValueError("current manifest dataset_id is not the canonical IPOS dataset")
            if int(manifest.get("row_count") or 0) < 1:
                raise ValueError("current manifest row_count is not positive")
            current_content_hash = content_hash
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            issues.append(
                IposStateIssue(
                    code="CURRENT_STATE_INTEGRITY_FAILURE",
                    detail=str(exc),
                    blocking=True,
                )
            )

    orphan_count = len(full_snapshots)
    if current_snapshot is not None:
        orphan_count = sum(path.resolve() != current_snapshot.resolve() for path in full_snapshots)
        if orphan_count:
            issues.append(
                IposStateIssue(
                    code="SUPERSEDED_FULL_SNAPSHOT_CLEANUP_PENDING",
                    detail=f"{orphan_count} unreferenced full snapshot(s) remain for retryable cleanup",
                    blocking=False,
                )
            )

    if transient_parts:
        issues.append(
            IposStateIssue(
                code="TRANSIENT_PART_FILES_PRESENT",
                detail=f"{len(transient_parts)} transient .part file(s) remain from an interrupted write",
                blocking=False,
            )
        )

    blocking = any(issue.blocking for issue in issues)
    if blocking:
        status = "BLOCKED"
    elif current_content_hash is None and not full_snapshots and not transient_parts:
        status = "EMPTY"
    elif issues:
        status = "RECOVERABLE"
    else:
        status = "READY"

    return IposStateAudit(
        version=IPOS_SG_STATE_AUDIT_VERSION,
        checked_at=datetime.now(timezone.utc),
        status=status,
        safe_to_run=not blocking,
        current_content_hash=current_content_hash,
        retained_full_snapshot_count=len(full_snapshots),
        orphan_full_snapshot_count=int(orphan_count),
        transient_part_paths=transient_parts,
        issues=tuple(issues),
    )


def audit_payload(audit: IposStateAudit) -> dict[str, Any]:
    payload = asdict(audit)
    payload["checked_at"] = audit.checked_at.isoformat()
    return payload


def assert_ipos_state_ready(audit: IposStateAudit) -> None:
    if audit.status != "READY":
        raise RuntimeError(
            "Singapore IPOS state is not clean after lifecycle commit: "
            + json.dumps(audit_payload(audit), ensure_ascii=False, sort_keys=True)
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Singapore IPOS lifecycle state audit")
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    audit = audit_ipos_state(args.state_dir)
    print(json.dumps(audit_payload(audit), ensure_ascii=False, sort_keys=True))
    return 0 if audit.safe_to_run else 4


if __name__ == "__main__":
    raise SystemExit(main())
