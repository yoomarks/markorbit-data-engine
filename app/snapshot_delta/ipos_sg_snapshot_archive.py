"""Independent, explicit SG snapshot archive; never alters accepted current.

Weekly restore points deduplicate the same content within the weekly archive.
Monthly objects live on a different filesystem and require a separate approved
physical target. Both verify the accepted operator receipt and source bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .ipos_sg import IPOS_SG_TRADEMARK_APPLICATIONS
from .ipos_sg_metadata_probe import SG_TIMEZONE
from .ipos_sg_operator import ipos_operator_lease

ARCHIVE_CONTRACT = "IPOS_SG_ACCEPTED_ARCHIVE_V1"
_TIERS = {"weekly": 4, "monthly": 3}
_SHA = re.compile(r"^[0-9a-f]{64}$")
_CHUNK = 8 * 1024 * 1024


class IposArchiveError(RuntimeError):
    """Archive failure leaves the accepted active snapshot unchanged."""


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise IposArchiveError("Required accepted JSON evidence is missing or oversized")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise IposArchiveError("Accepted JSON evidence cannot be parsed") from exc
    if not isinstance(payload, dict):
        raise IposArchiveError("Accepted JSON evidence is not an object")
    return payload


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(_CHUNK):
            sha.update(block)
    return sha.hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temp = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".part")
    try:
        with temp.open("x", encoding="utf-8", newline="\n") as target:
            json.dump(payload, target, sort_keys=True, ensure_ascii=False)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _accepted_current(state: Path) -> tuple[Path, dict[str, Any], str]:
    pointer = _read_json(state / "current.json")
    content_hash = pointer.get("content_hash")
    if not isinstance(content_hash, str) or not _SHA.fullmatch(content_hash):
        raise IposArchiveError("Accepted current hash is invalid")
    expected_ref = f"snapshots/{content_hash}.csv"
    if pointer.get("storage_reference") != expected_ref:
        raise IposArchiveError("Accepted current pointer is not the one-snapshot source")
    manifest_path = state / "snapshots" / f"{content_hash}.manifest.json"
    manifest = _read_json(manifest_path)
    if any(
        (
            manifest.get("content_hash") != content_hash,
            manifest.get("storage_reference") != expected_ref,
            manifest.get("dataset_id") != IPOS_SG_TRADEMARK_APPLICATIONS.dataset_id,
            manifest.get("source_id") != IPOS_SG_TRADEMARK_APPLICATIONS.source_id,
            manifest.get("jurisdiction") != "SG",
        )
    ):
        raise IposArchiveError("Accepted manifest disagrees with source and pointer")
    receipt_path = state / "acceptance" / "operator_latest.json"
    receipt = _read_json(receipt_path)
    full = receipt.get("full_corpus")
    after = receipt.get("state_after")
    if (
        receipt.get("status") != "PASS"
        or not isinstance(full, dict)
        or full.get("status") not in ("BOOTSTRAPPED", "UNCHANGED", "CHANGED")
        or full.get("content_hash") != content_hash
        or full.get("schema_hash") != manifest.get("schema_hash")
        or full.get("row_count") != manifest.get("row_count")
        or not isinstance(after, dict)
        or after.get("status") != "READY"
        or after.get("current_content_hash") != content_hash
        or after.get("retained_full_snapshot_count") != 1
    ):
        raise IposArchiveError("No successful matching accepted operator receipt")
    snapshot = state / expected_ref
    if not snapshot.is_file() or snapshot.is_symlink():
        raise IposArchiveError("Accepted current snapshot is missing or a symlink")
    if _digest(snapshot) != content_hash:
        raise IposArchiveError("Accepted current CSV bytes do not match manifest SHA-256")
    return snapshot, manifest, _digest(receipt_path)


def _slot(tier: str, now: datetime) -> str:
    if tier not in _TIERS:
        raise IposArchiveError("Archive tier must be weekly or monthly")
    if now.tzinfo is None or now.utcoffset() is None:
        raise IposArchiveError("Archive date requires an explicit timezone")
    local = now.astimezone(SG_TIMEZONE)
    if tier == "monthly":
        return local.strftime("%Y-%m")
    iso = local.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _point_path(root: Path, tier: str, slot: str, revision_sha256: str | None = None) -> Path:
    """A source version never overwrites an immutable point in the same calendar slot."""
    if tier not in _TIERS or not re.fullmatch(
        r"[0-9]{4}-W[0-9]{2}" if tier == "weekly" else r"[0-9]{4}-[0-9]{2}", slot
    ):
        raise IposArchiveError("Invalid archive tier or slot")
    if revision_sha256 is not None and not _SHA.fullmatch(revision_sha256):
        raise IposArchiveError("Revision must be an exact accepted SHA-256")
    stem = slot if revision_sha256 is None else f"{slot}--{revision_sha256}"
    return root / "points" / tier / f"{stem}.json"


def _guard_roots(state: Path, archive: Path, tier: str) -> None:
    if not state.is_dir() or not archive.is_dir():
        raise IposArchiveError("State and operator-approved archive roots must exist")
    if archive.is_symlink() or state.is_symlink():
        raise IposArchiveError("Archive and state roots must not be symlinks")
    for child in (archive / "objects", archive / "points", archive / "points" / tier):
        if child.is_symlink():
            raise IposArchiveError("Archive object and point directories must not be symlinks")
    s, a = state.resolve(strict=True), archive.resolve(strict=True)
    if a == s or a.is_relative_to(s) or s.is_relative_to(a):
        raise IposArchiveError("Archive root must be outside active lifecycle")
    if tier == "monthly" and s.stat().st_dev == a.stat().st_dev:
        raise IposArchiveError("Monthly backup requires an independent filesystem")
    # Separate Windows volumes can still be partitions of one physical disk.
    # Physical-device and recovery-target approval remains an operator gate.


def _copy_verified(snapshot: Path, destination: Path, content_hash: str, root: Path) -> None:
    if destination.is_symlink():
        raise IposArchiveError("Archive object cannot be a symlink")
    if destination.exists():
        if _digest(destination) != content_hash:
            raise IposArchiveError("Existing archive object has invalid content")
        return
    size = snapshot.stat().st_size
    volume = shutil.disk_usage(root)
    if volume.free - size < max(16 * 1024 * 1024, volume.total // 5):
        raise IposArchiveError("Archive capacity would violate 20 percent reserve")
    partial = destination.with_name("." + destination.name + "." + uuid.uuid4().hex + ".part")
    try:
        sha = hashlib.sha256()
        with snapshot.open("rb") as source, partial.open("xb") as target:
            while block := source.read(_CHUNK):
                target.write(block)
                sha.update(block)
            target.flush()
            os.fsync(target.fileno())
        if sha.hexdigest() != content_hash or partial.stat().st_size != size:
            raise IposArchiveError("Copied archive bytes differ from accepted snapshot")
        if destination.exists():
            raise IposArchiveError("Archive object appeared during copy")
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)


def verify_archive_point(
    root: str | Path, tier: str, slot: str, *, revision_sha256: str | None = None
) -> dict[str, Any]:
    archive = Path(root)
    point_path = _point_path(archive, tier, slot, revision_sha256)
    if any(
        path.is_symlink()
        for path in (
            archive,
            archive / "objects",
            archive / "points",
            point_path.parent,
            point_path,
        )
    ):
        raise IposArchiveError("Archive restore point cannot use symlink paths")
    point = _read_json(point_path)
    content_hash = point.get("content_hash")
    expected = f"objects/{content_hash}.csv"
    if (
        point.get("contract") != ARCHIVE_CONTRACT
        or point.get("tier") != tier
        or point.get("slot") != slot
        or (revision_sha256 is not None and content_hash != revision_sha256)
        or not isinstance(content_hash, str)
        or not _SHA.fullmatch(content_hash)
        or point.get("object_reference") != expected
        or not isinstance(point.get("source_manifest"), dict)
        or point["source_manifest"].get("content_hash") != content_hash
        or point["source_manifest"].get("dataset_id") != IPOS_SG_TRADEMARK_APPLICATIONS.dataset_id
    ):
        raise IposArchiveError("Archive restore point metadata is inconsistent")
    obj = archive / expected
    if not obj.is_file() or obj.is_symlink() or _digest(obj) != content_hash:
        raise IposArchiveError("Archive restore point object is missing or corrupt")
    return point


def restore_archive_point(
    root: str | Path,
    tier: str,
    slot: str,
    destination: str | Path,
    *,
    revision_sha256: str | None = None,
) -> Path:
    """Verify and restore into a new scratch file; never mutate production current."""
    archive = Path(root)
    target = Path(destination)
    if (
        target.exists()
        or target.is_symlink()
        or not target.parent.is_dir()
        or target.resolve().is_relative_to(archive.resolve())
    ):
        raise IposArchiveError("Restore target must be a new file outside archive")
    point = verify_archive_point(archive, tier, slot, revision_sha256=revision_sha256)
    source = archive / point["object_reference"]
    partial = target.with_name("." + target.name + "." + uuid.uuid4().hex + ".part")
    try:
        digest = hashlib.sha256()
        with source.open("rb") as original, partial.open("xb") as restored:
            while block := original.read(_CHUNK):
                restored.write(block)
                digest.update(block)
            restored.flush()
            os.fsync(restored.fileno())
        if digest.hexdigest() != point["content_hash"]:
            raise IposArchiveError("Restored bytes do not match accepted archive hash")
        if target.exists():
            raise IposArchiveError("Restore target appeared during copy")
        os.replace(partial, target)
        return target
    finally:
        partial.unlink(missing_ok=True)


def _point_slot(path: Path, tier: str) -> tuple[str, str | None]:
    """Resolve strict V1 slot or immutable same-slot content revision identity."""
    stem = path.stem
    slot, separator, revision = stem.partition("--")
    _point_path(Path("."), tier, slot, revision if separator else None)
    if separator and not revision:
        raise IposArchiveError("Archive revision has no SHA-256")
    return slot, revision if separator else None


def _retention(root: Path, tier: str) -> None:
    """Retain last four weekly/three monthly points; collect only unreferenced objects."""
    points_dir = root / "points" / tier
    points = sorted(points_dir.glob("*.json"))
    slots = sorted({_point_slot(point, tier)[0] for point in points})
    # Validate all tier references before destructive cleanup, including a
    # second tier if an operator deliberately configured a shared archive root.
    all_points: dict[str, list[Path]] = {}
    for existing_tier in _TIERS:
        directory = root / "points" / existing_tier
        if directory.is_symlink():
            raise IposArchiveError("Archive point directory cannot be a symlink")
        candidates = sorted(directory.glob("*.json"))
        for candidate in candidates:
            slot, revision = _point_slot(candidate, existing_tier)
            if candidate.is_symlink():
                raise IposArchiveError("Archive point cannot be a symlink")
            record = _read_json(candidate)
            digest = record.get("content_hash")
            if (
                record.get("contract") != ARCHIVE_CONTRACT
                or record.get("tier") != existing_tier
                or record.get("slot") != slot
                or (revision is not None and digest != revision)
                or not isinstance(digest, str)
                or not _SHA.fullmatch(digest)
                or record.get("object_reference") != f"objects/{digest}.csv"
                or not (root / "objects" / f"{digest}.csv").is_file()
            ):
                raise IposArchiveError("Invalid existing archive point prevents pruning")
        all_points[existing_tier] = candidates
    expired_slots = set(slots[: -_TIERS[tier]])
    for expired in points:
        if _point_slot(expired, tier)[0] in expired_slots:
            expired.unlink()
    referenced = {
        _read_json(point)["content_hash"]
        for existing_tier in _TIERS
        for point in (root / "points" / existing_tier).glob("*.json")
    }
    for object_path in (root / "objects").glob("*.csv"):
        if (
            re.fullmatch(r"[0-9a-f]{64}", object_path.stem)
            and object_path.stem not in referenced
            and not object_path.is_symlink()
        ):
            object_path.unlink()


def archive_accepted_current(
    state_root: str | Path,
    archive_root: str | Path,
    *,
    tier: str,
    now: datetime,
) -> dict[str, Any]:
    """Explicit one-shot archive under the existing SG operator lease."""
    state, archive = Path(state_root), Path(archive_root)
    _guard_roots(state, archive, tier)
    slot = _slot(tier, now)
    with ipos_operator_lease(state):
        snapshot, manifest, report_sha = _accepted_current(state)
        content_hash = manifest["content_hash"]
        base_path = _point_path(archive, tier, slot)
        if base_path.is_symlink():
            raise IposArchiveError("Archive point cannot be a symlink")
        revision_sha256 = None
        if base_path.exists():
            previous = verify_archive_point(archive, tier, slot)
            if previous["content_hash"] != content_hash:
                revision_sha256 = content_hash
        point_path = _point_path(archive, tier, slot, revision_sha256)
        if point_path.is_symlink():
            raise IposArchiveError("Archive revision cannot be a symlink")
        if point_path.exists():
            previous = verify_archive_point(archive, tier, slot, revision_sha256=revision_sha256)
            if previous["content_hash"] != content_hash:
                raise IposArchiveError("Archive revision conflicts with accepted source")
            _retention(archive, tier)
            return previous
        objects = archive / "objects"
        points = point_path.parent
        objects.mkdir(parents=True, exist_ok=True)
        points.mkdir(parents=True, exist_ok=True)
        object_path = objects / f"{content_hash}.csv"
        _copy_verified(snapshot, object_path, content_hash, archive)
        if _read_json(state / "current.json").get("content_hash") != content_hash:
            raise IposArchiveError("Accepted current changed during archive")
        point = {
            "contract": ARCHIVE_CONTRACT,
            "tier": tier,
            "slot": slot,
            "content_hash": content_hash,
            "object_reference": f"objects/{content_hash}.csv",
            "source_manifest": manifest,
            "operator_receipt_sha256": report_sha,
            "archived_at": now.astimezone(timezone.utc).isoformat(),
            "source_revision_trusted": False,
        }
        _atomic_json(point_path, point)
        verify_archive_point(archive, tier, slot, revision_sha256=revision_sha256)
        _retention(archive, tier)
        return point


def main() -> None:
    parser = argparse.ArgumentParser(description="Explicit accepted SG weekly/monthly archive")
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--archive-root", required=True)
    parser.add_argument("--tier", required=True, choices=sorted(_TIERS))
    parser.add_argument("--operator-approved-target", action="store_true")
    args = parser.parse_args()
    if not args.operator_approved_target:
        parser.error("explicit operator-approved archive destination required")
    result = archive_accepted_current(
        args.state_root,
        args.archive_root,
        tier=args.tier,
        now=datetime.now(timezone.utc),
    )
    print(
        json.dumps(
            {
                "tier": result["tier"],
                "slot": result["slot"],
                "content_hash": result["content_hash"],
                "object_reference": result["object_reference"],
                "status": "VERIFIED",
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
