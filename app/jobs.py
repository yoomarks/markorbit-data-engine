from __future__ import annotations

from pathlib import Path
from typing import Any

from app.cn.ingest import ingest_cn_package
from app.config import get_settings
from app.repository import (
    create_job_run,
    finish_job_run,
    pending_packages,
    register_package,
    update_package_status,
)
from app.scanner import discover_packages


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
        "packages": [],
    }

    statuses = ("FAILED",) if include_failed else ("REGISTERED",)
    candidates = pending_packages(
        "CN",
        limit=max(limit * 20, 100),
        statuses=statuses,
    )

    for package in candidates:
        if result["attempted"] >= limit:
            break

        path = Path(package["file_path"])
        if not path.exists():
            message = f"Source package file is missing: {path}"
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
            metrics = ingest_cn_package(
                str(package["package_id"]),
                path,
                settings.raw_data_root,
                trigger_type=trigger_type,
                retrying=package["status"] == "FAILED",
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
