from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.repository import (
    create_job_run,
    finish_job_run,
    pending_packages,
    update_package_status,
)
from app.scanner import discover_packages, sha256_file
from app.us.ingest import ingest_us_package
from app.us.migrations import ensure_us_m1_schema
from app.us.package_meta import infer_us_package_descriptor
from app.us.repository import (
    list_us_blocking_failures,
    list_us_packages,
    register_us_package,
)
from app.us.run_guard import recover_interrupted_us_ingestions, us_ingestion_guard


US_SOURCE_SUFFIXES = {".zip", ".xml"}


def ensure_us_raw_directories() -> None:
    root = get_settings().raw_data_root
    (root / "incoming" / "us").mkdir(parents=True, exist_ok=True)
    (root / "archive" / "us").mkdir(parents=True, exist_ok=True)


def us_input_policy_issues(incoming: Path) -> list[dict[str, Any]]:
    registered = list_us_packages()
    issues: list[dict[str, Any]] = []
    files = sorted(path for path in incoming.iterdir() if path.is_file()) if incoming.exists() else []
    by_partition: dict[str, list[tuple[Path, str]]] = defaultdict(list)

    for path in files:
        suffix = path.suffix.lower()
        if suffix not in US_SOURCE_SUFFIXES:
            if suffix in {".gz"}:
                issues.append(
                    {
                        "type": "UNSUPPORTED_US_M1_SOURCE_SUFFIX",
                        "file_name": path.name,
                        "suffix": suffix,
                    }
                )
            continue
        descriptor = infer_us_package_descriptor(path)
        if descriptor.package_kind == "UNKNOWN":
            issues.append(
                {
                    "type": "UNKNOWN_US_PACKAGE_PRECEDENCE",
                    "file_name": path.name,
                }
            )
            continue
        by_partition[descriptor.partition_value].append((path, sha256_file(path)))

    for partition, entries in sorted(by_partition.items()):
        if len(entries) > 1:
            issues.append(
                {
                    "type": "AMBIGUOUS_US_UPDATE_DATE",
                    "partition_value": partition,
                    "file_names": [path.name for path, _sha in entries],
                }
            )

    registered_by_partition: dict[str, set[str]] = defaultdict(set)
    for row in registered:
        value = str(row.get("partition_value") or "")
        digest = str(row.get("sha256") or "").lower()
        if value and digest:
            registered_by_partition[value].add(digest)

    for partition, entries in sorted(by_partition.items()):
        known = registered_by_partition.get(partition, set())
        for path, digest in entries:
            if known and digest.lower() not in known:
                issues.append(
                    {
                        "type": "US_DAILY_REVISION_POLICY_REQUIRED",
                        "partition_value": partition,
                        "file_name": path.name,
                        "sha256": digest,
                        "registered_sha256": sorted(known),
                    }
                )
    return issues


def scan_us_incoming(trigger_type: str = "MANUAL_US") -> dict[str, int]:
    ensure_us_m1_schema()
    ensure_us_raw_directories()
    incoming = get_settings().raw_data_root / "incoming" / "us"
    issues = us_input_policy_issues(incoming)
    if issues:
        raise RuntimeError(f"US M1 input policy blocked scan: {issues}")

    run_id = create_job_run(
        job_type="US_PACKAGE_DISCOVERY",
        trigger_type=trigger_type,
        payload={"directory": str(incoming)},
    )
    metrics = {"discovered": 0, "registered": 0, "duplicate": 0, "failed": 0}
    try:
        for package in discover_packages(incoming, jurisdiction="US"):
            if package.path.suffix.lower() not in US_SOURCE_SUFFIXES:
                continue
            metrics["discovered"] += 1
            try:
                _, inserted = register_us_package(package)
                metrics["registered" if inserted else "duplicate"] += 1
            except Exception:
                metrics["failed"] += 1
                raise
        finish_job_run(run_id, "SUCCESS", metrics=metrics)
        return metrics
    except Exception as exc:
        finish_job_run(run_id, "FAILED", metrics=metrics, error_message=str(exc))
        raise


def _resolve_us_package_path(package: dict[str, Any], raw_root: Path) -> Path | None:
    declared = Path(str(package["file_path"]))
    file_name = str(package["file_name"])
    expected_sha = str(package.get("sha256") or "").lower()
    candidates = [
        declared,
        raw_root / "incoming" / "us" / file_name,
        raw_root / "archive" / "us" / file_name,
    ]
    archive = raw_root / "archive" / "us"
    if archive.exists():
        stem = Path(file_name).stem
        suffix = Path(file_name).suffix
        candidates.extend(sorted(archive.glob(f"{stem}_*{suffix}")))

    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen or not candidate.is_file():
            continue
        seen.add(candidate)
        if expected_sha and sha256_file(candidate).lower() != expected_sha:
            continue
        return candidate
    return None


def ingest_pending_us(
    trigger_type: str = "MANUAL_US",
    *,
    include_failed: bool = False,
    limit: int = 1,
) -> dict[str, Any]:
    settings = get_settings()
    result: dict[str, Any] = {
        "attempted": 0,
        "success": 0,
        "failed": 0,
        "skipped_missing": 0,
        "busy": False,
        "recovered_interrupted": [],
        "packages": [],
    }
    with us_ingestion_guard() as acquired:
        if not acquired:
            result["busy"] = True
            return result
        result["recovered_interrupted"] = recover_interrupted_us_ingestions()
        statuses = (
            ("INTERRUPTED", "FAILED", "MISSING_FILE")
            if include_failed
            else ("INTERRUPTED", "REGISTERED")
        )
        candidates = pending_packages(
            "US",
            limit=max(limit * 20, 100),
            statuses=statuses,
        )
        for package in candidates:
            if result["attempted"] >= limit:
                break
            path = _resolve_us_package_path(package, settings.raw_data_root)
            if path is None:
                message = (
                    "Registered USPTO package is missing and no SHA-256-matching "
                    f"incoming/archive copy was found: {package['file_name']}"
                )
                update_package_status(
                    str(package["package_id"]),
                    "MISSING_FILE",
                    error_message=message,
                )
                result["skipped_missing"] += 1
                result["packages"].append(
                    {
                        "package_id": str(package["package_id"]),
                        "file_name": package["file_name"],
                        "status": "MISSING_FILE",
                        "error": message,
                    }
                )
                continue
            result["attempted"] += 1
            try:
                metrics = ingest_us_package(
                    str(package["package_id"]),
                    path,
                    settings.raw_data_root,
                    trigger_type=trigger_type,
                    retrying=str(package["status"])
                    in {"INTERRUPTED", "FAILED", "MISSING_FILE"},
                )
                result["success"] += 1
                result["packages"].append(
                    {
                        "package_id": str(package["package_id"]),
                        "file_name": package["file_name"],
                        "status": "SUCCESS",
                        "metrics": metrics,
                    }
                )
            except Exception as exc:
                result["failed"] += 1
                result["packages"].append(
                    {
                        "package_id": str(package["package_id"]),
                        "file_name": package["file_name"],
                        "status": "FAILED",
                        "error": str(exc),
                    }
                )
    return result


def scan_and_ingest_us(trigger_type: str = "MANUAL_US") -> dict[str, Any]:
    failures = list_us_blocking_failures()
    if failures:
        first = failures[0]
        raise RuntimeError(
            "US replay has a failed/missing earlier package. Run retry-us.ps1 first: "
            f"{first['file_name']} ({first['status']})"
        )
    return {
        "scan": scan_us_incoming(trigger_type=trigger_type),
        "ingest": ingest_pending_us(trigger_type=trigger_type, limit=1),
    }
