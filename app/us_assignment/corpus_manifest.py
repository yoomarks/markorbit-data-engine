from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any
import zipfile

from app.us_assignment.repository import VALID_SOURCE_KINDS


MANIFEST_VERSION = "US_ASSIGNMENT_CORPUS_MANIFEST_V1"
SNAPSHOT_KIND = "ASSIGNMENT_SNAPSHOT_XML"
DAILY_KIND = "DAILY_ASSIGNMENT_XML"


@dataclass(frozen=True)
class ManifestSource:
    path: str
    source_kind: str
    effective_date: date


@dataclass(frozen=True)
class AssignmentCorpusManifest:
    expected_snapshot_packages: int
    expected_daily_packages: int
    daily_through: date | None
    sources: tuple[ManifestSource, ...]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _xml_members(path: Path) -> list[str]:
    if path.suffix.lower() == ".xml":
        return [path.name]
    if path.suffix.lower() != ".zip":
        raise ValueError("Assignment corpus source must be .xml or .zip")
    with zipfile.ZipFile(path) as archive:
        members = sorted(
            item.filename
            for item in archive.infolist()
            if not item.is_dir() and item.filename.lower().endswith(".xml")
        )
    if not members:
        raise ValueError("Assignment ZIP contains no XML member")
    return members


def _nonnegative_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{key} must be a non-negative integer")
    return value


def load_manifest(path: Path) -> AssignmentCorpusManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Assignment corpus manifest root must be an object")
    if payload.get("manifest_version") != MANIFEST_VERSION:
        raise ValueError(f"manifest_version must be {MANIFEST_VERSION}")

    snapshot_count = _nonnegative_int(payload, "expected_snapshot_packages")
    daily_count = _nonnegative_int(payload, "expected_daily_packages")
    if snapshot_count != 1:
        raise ValueError(
            "Assignment corpus V1 requires exactly one authoritative historical snapshot package"
        )

    daily_through_raw = payload.get("daily_through")
    if daily_through_raw in (None, ""):
        daily_through = None
    elif isinstance(daily_through_raw, str):
        daily_through = date.fromisoformat(daily_through_raw)
    else:
        raise ValueError("daily_through must be YYYY-MM-DD or null")

    items = payload.get("sources")
    if not isinstance(items, list) or not items:
        raise ValueError("sources must be a non-empty array")
    sources: list[ManifestSource] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"sources[{index}] must be an object")
        source_path = item.get("path")
        source_kind = item.get("source_kind")
        effective_raw = item.get("effective_date")
        if not isinstance(source_path, str) or not source_path.strip():
            raise ValueError(f"sources[{index}].path is required")
        if not isinstance(source_kind, str):
            raise ValueError(f"sources[{index}].source_kind is required")
        source_kind = source_kind.strip().upper()
        if source_kind not in VALID_SOURCE_KINDS:
            raise ValueError(
                f"sources[{index}].source_kind must be one of {sorted(VALID_SOURCE_KINDS)}"
            )
        if not isinstance(effective_raw, str):
            raise ValueError(
                f"sources[{index}].effective_date must be explicit YYYY-MM-DD; "
                "it is never inferred from the filename"
            )
        sources.append(
            ManifestSource(
                path=source_path.strip().replace("\\", "/"),
                source_kind=source_kind,
                effective_date=date.fromisoformat(effective_raw),
            )
        )
    return AssignmentCorpusManifest(
        expected_snapshot_packages=snapshot_count,
        expected_daily_packages=daily_count,
        daily_through=daily_through,
        sources=tuple(sources),
    )


def _inside_raw_root(raw_root: Path, candidate: Path) -> Path:
    raw_root = raw_root.resolve()
    resolved = candidate.resolve()
    try:
        resolved.relative_to(raw_root)
    except ValueError as exc:
        raise ValueError("Assignment manifest source path must stay inside RAW_DATA_PATH") from exc
    return resolved


def resolve_source_path(raw_root: Path, manifest_path: str) -> Path:
    """Resolve one manifest source after normal incoming->archive movement.

    The manifest identity is stable across replay. A source originally declared in
    incoming may therefore be found in archive on a later dry-run/retry. No file is
    copied or moved here, and basename recovery is limited to the Assignment domain.
    """
    raw_root = raw_root.resolve()
    relative = Path(manifest_path)
    declared = _inside_raw_root(
        raw_root,
        relative if relative.is_absolute() else raw_root / relative,
    )
    if declared.is_file():
        return declared

    candidates: list[Path] = []
    if not relative.is_absolute():
        parts = relative.parts
        if len(parts) >= 3 and parts[0] in {"incoming", "archive"} and parts[1] == "us_assignment":
            other = "archive" if parts[0] == "incoming" else "incoming"
            candidates.append(raw_root / other / "us_assignment" / Path(*parts[2:]))
        candidates.extend(
            [
                raw_root / "incoming" / "us_assignment" / relative.name,
                raw_root / "archive" / "us_assignment" / relative.name,
            ]
        )
    for candidate in candidates:
        resolved = _inside_raw_root(raw_root, candidate)
        if resolved.is_file():
            return resolved
    return declared


def preflight_manifest(manifest_path: Path, raw_root: Path) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    try:
        manifest = load_manifest(manifest_path)
    except Exception as exc:
        return {
            "preflight_version": MANIFEST_VERSION,
            "status": "NOT_READY",
            "safe": False,
            "issues": [{"type": "MANIFEST_INVALID", "error": str(exc)}],
            "plan": [],
        }

    snapshots = [s for s in manifest.sources if s.source_kind == SNAPSHOT_KIND]
    dailies = [s for s in manifest.sources if s.source_kind == DAILY_KIND]
    if len(snapshots) != manifest.expected_snapshot_packages:
        issues.append(
            {
                "type": "SNAPSHOT_PACKAGE_COUNT_MISMATCH",
                "expected": manifest.expected_snapshot_packages,
                "observed": len(snapshots),
            }
        )
    if len(dailies) != manifest.expected_daily_packages:
        issues.append(
            {
                "type": "DAILY_PACKAGE_COUNT_MISMATCH",
                "expected": manifest.expected_daily_packages,
                "observed": len(dailies),
            }
        )

    dates = [source.effective_date for source in manifest.sources]
    duplicate_dates = sorted({value for value in dates if dates.count(value) > 1})
    if duplicate_dates:
        issues.append(
            {
                "type": "DUPLICATE_EFFECTIVE_DATE_NOT_MODELED",
                "dates": [item.isoformat() for item in duplicate_dates],
            }
        )

    snapshot_date = snapshots[0].effective_date if len(snapshots) == 1 else None
    if snapshot_date is not None:
        older_daily = sorted(s.effective_date for s in dailies if s.effective_date <= snapshot_date)
        if older_daily:
            issues.append(
                {
                    "type": "DAILY_NOT_AFTER_HISTORICAL_SNAPSHOT",
                    "snapshot_effective_date": snapshot_date.isoformat(),
                    "daily_dates": [item.isoformat() for item in older_daily],
                }
            )

    if manifest.expected_daily_packages == 0:
        if manifest.daily_through is not None:
            issues.append({"type": "DAILY_THROUGH_WITH_ZERO_DAILY_PACKAGES"})
    else:
        latest_daily = max((source.effective_date for source in dailies), default=None)
        if manifest.daily_through is None:
            issues.append({"type": "DAILY_THROUGH_REQUIRED"})
        elif latest_daily != manifest.daily_through:
            issues.append(
                {
                    "type": "DAILY_THROUGH_MISMATCH",
                    "expected": manifest.daily_through.isoformat(),
                    "observed": latest_daily.isoformat() if latest_daily else None,
                }
            )

    plan: list[dict[str, Any]] = []
    seen_declared_paths: set[str] = set()
    seen_resolved_paths: set[str] = set()
    seen_sha: dict[str, str] = {}
    for source in sorted(manifest.sources, key=lambda s: (s.effective_date, s.source_kind, s.path)):
        try:
            if source.path in seen_declared_paths:
                raise ValueError("Duplicate source path in manifest")
            seen_declared_paths.add(source.path)
            resolved = resolve_source_path(raw_root, source.path)
            resolved_key = str(resolved)
            if resolved_key in seen_resolved_paths:
                raise ValueError("Multiple manifest entries resolve to the same source file")
            seen_resolved_paths.add(resolved_key)
            if not resolved.is_file():
                raise FileNotFoundError(f"Source file not found: {resolved}")
            members = _xml_members(resolved)
            digest = _sha256(resolved)
            if digest in seen_sha:
                raise ValueError(
                    f"Duplicate source SHA-256 also used by {seen_sha[digest]}; one authoritative file only"
                )
            seen_sha[digest] = source.path
            plan.append(
                {
                    "path": str(resolved),
                    "manifest_path": source.path,
                    "file_name": resolved.name,
                    "source_kind": source.source_kind,
                    "effective_date": source.effective_date.isoformat(),
                    "sha256": digest,
                    "size_bytes": resolved.stat().st_size,
                    "xml_members": members,
                }
            )
        except Exception as exc:
            issues.append(
                {
                    "type": "SOURCE_INVALID",
                    "path": source.path,
                    "source_kind": source.source_kind,
                    "effective_date": source.effective_date.isoformat(),
                    "error": str(exc),
                }
            )

    safe = not issues and len(plan) == len(manifest.sources)
    return {
        "preflight_version": MANIFEST_VERSION,
        "status": "READY" if safe else "NOT_READY",
        "safe": safe,
        "manifest_path": str(manifest_path.resolve()),
        "expected_snapshot_packages": manifest.expected_snapshot_packages,
        "expected_daily_packages": manifest.expected_daily_packages,
        "daily_through": manifest.daily_through.isoformat() if manifest.daily_through else None,
        "snapshot_effective_date": snapshot_date.isoformat() if snapshot_date else None,
        "source_count": len(manifest.sources),
        "issues": issues,
        "plan": plan,
        "semantics": "USPTO_RECORDED_ASSIGNMENT_FACTS_NOT_LEGAL_TITLE_CONCLUSION",
        "effective_date_inferred_from_filename": False,
        "calendar_gap_inference": False,
        "legal_ownership_conclusion": False,
    }
