from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from app.cn.ingest_m16 import ingest_cn_package
from app.cn.run_guard import cn_ingestion_guard, recover_interrupted_cn_ingestions
from app.config import get_settings
from app.db import clickhouse_execution_settings
from app.repository import (
    create_job_run,
    finish_job_run,
    pending_packages,
    register_package,
    update_package_status,
)
from app.scanner import discover_packages


CN_JOIN_ALGORITHM = "grace_hash"
CN_GRACE_HASH_JOIN_INITIAL_BUCKETS = 32
CN_CLICKHOUSE_SEND_RECEIVE_TIMEOUT = 3600


def ensure_raw_directories() -> None:
    root = get_settings().raw_data_root
    for relative in (
        "incoming/cn",
        "incoming/us",
        "archive/cn",
        "archive/us",
        "quarantine",
        "temp",
    ):
        (root / relative).mkdir(parents=True, exist_ok=True)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _resolve_package_path(package: dict[str, Any], raw_root: Path) -> Path | None:
    """Resolve the authoritative registered ZIP by SHA-256.

    A successful run may archive the ZIP while ``file_path`` still names the old
    incoming location. A later file can also reuse the same basename. Never trust
    a path or filename alone: when a registered SHA is available, every candidate
    (including the declared path) must match it before use.
    """
    declared = Path(str(package["file_path"]))
    file_name = str(package["file_name"])
    expected_sha = str(package.get("sha256") or "").lower()

    if declared.is_file():
        if not expected_sha or _file_sha256(declared).lower() == expected_sha:
            return declared

    incoming = raw_root / "incoming" / "cn"
    archive = raw_root / "archive" / "cn"

    candidates: list[Path] = [incoming / file_name, archive / file_name]
    stem = Path(file_name).stem
    suffix = Path(file_name).suffix
    if archive.exists():
        candidates.extend(sorted(archive.glob(f"{stem}_*{suffix}")))

    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen or not candidate.is_file():
            continue
        seen.add(candidate)
        if expected_sha and _file_sha256(candidate).lower() != expected_sha:
            continue
        return candidate
    return None


def scan_cn_incoming(trigger_type: str = "MANUAL") -> dict[str, int]:
    ensure_raw_directories()
    incoming = get_settings().raw_data_root / "incoming" / "cn"
    run_id = create_job_run(
        job_type="CN_PACKAGE_DISCOVERY",
        trigger_type=trigger_type,
        payload={"directory": str(incoming)},
    )
    metrics = {"discovered": 0, "registered": 0, "duplicate": 0, "failed": 0}

    try:
        for package in discover_packages(incoming, jurisdiction="CN"):
            metrics["discovered"] += 1
            try:
                _, inserted = register_package(package)
                metrics["registered" if inserted else "duplicate"] += 1
            except Exception:
                metrics["failed"] += 1
        finish_job_run(run_id, "SUCCESS", metrics=metrics)
        return metrics
    except Exception as exc:
        finish_job_run(run_id, "FAILED", metrics=metrics, error_message=str(exc))
        raise


def ingest_pending_cn(
    trigger_type: str = "SCHEDULED",
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

    # One session advisory lock covers candidate selection through publication.
    # If the process/container/host dies, PostgreSQL releases the lock
    # automatically. The next run can then reclaim any orphaned PROCESSING row.
    with cn_ingestion_guard() as acquired:
        if not acquired:
            result["busy"] = True
            return result

        recovered = recover_interrupted_cn_ingestions()
        result["recovered_interrupted"] = recovered

        statuses = (
            ("INTERRUPTED", "FAILED", "MISSING_FILE")
            if include_failed
            else ("INTERRUPTED", "REGISTERED")
        )
        candidates = pending_packages(
            "CN",
            limit=max(limit * 20, 100),
            statuses=statuses,
        )

        for package in candidates:
            if result["attempted"] >= limit:
                break

            path = _resolve_package_path(package, settings.raw_data_root)
            if path is None:
                declared = Path(str(package["file_path"]))
                message = (
                    f"Source package file is missing and no SHA-256-matching archive copy "
                    f"was found: {declared}"
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
                # CN real-corpus replay already proved this ClickHouse 24.8
                # resource profile on packages that exceed the in-memory hash
                # JOIN ceiling. Keep it attached to CN ingestion itself so Admin,
                # API retry, scheduled worker and PowerShell all behave the same.
                with clickhouse_execution_settings(
                    join_algorithm=CN_JOIN_ALGORITHM,
                    grace_hash_join_initial_buckets=CN_GRACE_HASH_JOIN_INITIAL_BUCKETS,
                    send_receive_timeout=CN_CLICKHOUSE_SEND_RECEIVE_TIMEOUT,
                ):
                    metrics = ingest_cn_package(
                        str(package["package_id"]),
                        path,
                        settings.raw_data_root,
                        trigger_type=trigger_type,
                        retrying=package["status"]
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


def scan_and_ingest_cn(trigger_type: str = "SCHEDULED") -> dict[str, Any]:
    return {
        "scan": scan_cn_incoming(trigger_type=trigger_type),
        "ingest": ingest_pending_cn(trigger_type=trigger_type, limit=1),
    }
