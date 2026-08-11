from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any
import zipfile

from app.us_ttab.repository import normalize_snapshot_at


MANIFEST_VERSION = "US_TTAB_CORPUS_MANIFEST_V1"
HISTORICAL_KIND = "TTAB_BULK_HISTORICAL_XML"
DAILY_KIND = "TTAB_BULK_DAILY_XML"
CORPUS_SOURCE_KINDS = {HISTORICAL_KIND, DAILY_KIND}


@dataclass(frozen=True)
class ManifestSource:
    path: str
    source_kind: str
    snapshot_at: datetime


@dataclass(frozen=True)
class TTABCorpusManifest:
    expected_historical_packages: int
    expected_daily_packages: int
    daily_through: str | None
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
        raise ValueError("TTAB corpus source must be .xml or .zip")
    with zipfile.ZipFile(path) as archive:
        members = sorted(
            item.filename
            for item in archive.infolist()
            if not item.is_dir() and item.filename.lower().endswith(".xml")
        )
    if not members:
        raise ValueError("TTAB ZIP contains no XML member")
    return members


def _nonnegative_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{key} must be a non-negative integer")
    return value


def _parse_snapshot_at(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"{label} must be an explicit timezone-aware ISO-8601 timestamp; "
            "it is never inferred from the filename"
        )
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} is not valid ISO-8601: {value}") from exc
    return normalize_snapshot_at(parsed)


def _snapshot_text(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def load_manifest(path: Path) -> TTABCorpusManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("TTAB corpus manifest root must be an object")
    if payload.get("manifest_version") != MANIFEST_VERSION:
        raise ValueError(f"manifest_version must be {MANIFEST_VERSION}")

    historical_count = _nonnegative_int(payload, "expected_historical_packages")
    daily_count = _nonnegative_int(payload, "expected_daily_packages")
    if historical_count != 1:
        raise ValueError(
            "TTAB corpus V1 requires exactly one authoritative historical bulk package"
        )

    daily_through_raw = payload.get("daily_through")
    if daily_through_raw in (None, ""):
        daily_through = None
    elif isinstance(daily_through_raw, str):
        try:
            datetime.fromisoformat(daily_through_raw + "T00:00:00")
        except ValueError as exc:
            raise ValueError("daily_through must be YYYY-MM-DD or null") from exc
        if len(daily_through_raw) != 10:
            raise ValueError("daily_through must be YYYY-MM-DD or null")
        daily_through = daily_through_raw
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
        if not isinstance(source_path, str) or not source_path.strip():
            raise ValueError(f"sources[{index}].path is required")
        if not isinstance(source_kind, str):
            raise ValueError(f"sources[{index}].source_kind is required")
        source_kind = source_kind.strip().upper()
        if source_kind not in CORPUS_SOURCE_KINDS:
            raise ValueError(
                f"sources[{index}].source_kind must be one of {sorted(CORPUS_SOURCE_KINDS)}; "
                "per-proceeding TTABVUE snapshots are not full-corpus replay sources"
            )
        sources.append(
            ManifestSource(
                path=source_path.strip().replace("\\", "/"),
                source_kind=source_kind,
                snapshot_at=_parse_snapshot_at(
                    item.get("snapshot_at"), label=f"sources[{index}].snapshot_at"
                ),
            )
        )
    return TTABCorpusManifest(
        expected_historical_packages=historical_count,
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
        raise ValueError("TTAB manifest source path must stay inside RAW_DATA_PATH") from exc
    return resolved


def resolve_source_path(raw_root: Path, manifest_path: str) -> Path:
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
        if len(parts) >= 3 and parts[0] in {"incoming", "archive"} and parts[1] == "us_ttab":
            other = "archive" if parts[0] == "incoming" else "incoming"
            candidates.append(raw_root / other / "us_ttab" / Path(*parts[2:]))
        candidates.extend(
            [
                raw_root / "incoming" / "us_ttab" / relative.name,
                raw_root / "archive" / "us_ttab" / relative.name,
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

    historical = [s for s in manifest.sources if s.source_kind == HISTORICAL_KIND]
    dailies = [s for s in manifest.sources if s.source_kind == DAILY_KIND]
    if len(historical) != manifest.expected_historical_packages:
        issues.append(
            {
                "type": "HISTORICAL_PACKAGE_COUNT_MISMATCH",
                "expected": manifest.expected_historical_packages,
                "observed": len(historical),
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

    timestamps = [source.snapshot_at for source in manifest.sources]
    duplicate_timestamps = sorted(
        {value for value in timestamps if timestamps.count(value) > 1}
    )
    if duplicate_timestamps:
        issues.append(
            {
                "type": "DUPLICATE_SNAPSHOT_AT_NOT_MODELED",
                "timestamps": [_snapshot_text(item) for item in duplicate_timestamps],
            }
        )

    historical_at = historical[0].snapshot_at if len(historical) == 1 else None
    if historical_at is not None:
        invalid_daily = sorted(s.snapshot_at for s in dailies if s.snapshot_at <= historical_at)
        if invalid_daily:
            issues.append(
                {
                    "type": "DAILY_NOT_AFTER_HISTORICAL_SNAPSHOT",
                    "historical_snapshot_at": _snapshot_text(historical_at),
                    "daily_snapshot_at": [_snapshot_text(item) for item in invalid_daily],
                }
            )

    if manifest.expected_daily_packages == 0:
        if manifest.daily_through is not None:
            issues.append({"type": "DAILY_THROUGH_WITH_ZERO_DAILY_PACKAGES"})
    else:
        latest_daily = max((source.snapshot_at for source in dailies), default=None)
        latest_date = latest_daily.date().isoformat() if latest_daily else None
        if manifest.daily_through is None:
            issues.append({"type": "DAILY_THROUGH_REQUIRED"})
        elif latest_date != manifest.daily_through:
            issues.append(
                {
                    "type": "DAILY_THROUGH_MISMATCH",
                    "expected": manifest.daily_through,
                    "observed": latest_date,
                }
            )

    plan: list[dict[str, Any]] = []
    seen_declared_paths: set[str] = set()
    seen_resolved_paths: set[str] = set()
    seen_sha: dict[str, str] = {}
    for source in sorted(
        manifest.sources,
        key=lambda item: (item.snapshot_at, item.source_kind, item.path),
    ):
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
                    "snapshot_at": _snapshot_text(source.snapshot_at),
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
                    "snapshot_at": _snapshot_text(source.snapshot_at),
                    "error": str(exc),
                }
            )

    safe = not issues and len(plan) == len(manifest.sources)
    return {
        "preflight_version": MANIFEST_VERSION,
        "status": "READY" if safe else "NOT_READY",
        "safe": safe,
        "manifest_path": str(manifest_path.resolve()),
        "expected_historical_packages": manifest.expected_historical_packages,
        "expected_daily_packages": manifest.expected_daily_packages,
        "daily_through": manifest.daily_through,
        "historical_snapshot_at": _snapshot_text(historical_at) if historical_at else None,
        "source_count": len(manifest.sources),
        "issues": issues,
        "plan": plan,
        "semantics": "USPTO_TTAB_PROCEDURAL_FACTS_NOT_OUTCOME_OR_SUBSTANTIVE_RIGHTS_CONCLUSION",
        "snapshot_at_inferred_from_filename": False,
        "calendar_gap_inference": False,
        "deadline_validity_inference": False,
        "legal_outcome_conclusion": False,
        "substantive_rights_conclusion": False,
    }
