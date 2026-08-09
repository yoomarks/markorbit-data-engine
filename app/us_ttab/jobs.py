from __future__ import annotations

from pathlib import Path
from typing import Any

from app.repository import update_package_status
from app.us_ttab.ingest import ingest_ttab_package
from app.us_ttab.repository import list_ttab_blocking_failures, list_ttab_packages
from app.us_ttab.run_guard import ttab_ingestion_guard, recover_interrupted_ttab_ingestions


def _resolve_package_path(row: dict[str, Any], raw_root: Path) -> Path | None:
    candidates: list[Path] = []
    for value in (row.get("file_path"), row.get("archived_path")):
        if value:
            candidates.append(Path(str(value)))
    file_name = str(row.get("file_name") or "")
    if file_name:
        candidates.append(raw_root / "archive" / "us_ttab" / file_name)
    for path in candidates:
        if path.is_file():
            return path
    return None


def run_ttab_once(raw_root: Path, *, retry: bool = False) -> dict[str, object]:
    with ttab_ingestion_guard() as acquired:
        if not acquired:
            return {"status": "BUSY", "processed": 0}
        recover_interrupted_ttab_ingestions()
        rows = list_ttab_packages()
        if not retry:
            failures = list_ttab_blocking_failures()
            if failures:
                raise RuntimeError(
                    "US TTAB replay is blocked by FAILED/MISSING_FILE package(s). "
                    "Run scripts/retry-us-ttab.ps1 first."
                )
            candidates = [row for row in rows if row["status"] in {"REGISTERED", "INTERRUPTED"}]
        else:
            candidates = [
                row
                for row in rows
                if row["status"] in {"INTERRUPTED", "FAILED", "MISSING_FILE"}
            ]
        if not candidates:
            return {"status": "IDLE", "processed": 0}

        row = candidates[0]
        path = _resolve_package_path(row, raw_root)
        if path is None:
            update_package_status(
                str(row["package_id"]),
                "MISSING_FILE",
                error_message="Registered US TTAB source is missing from file_path/archive.",
            )
            if retry:
                return {
                    "status": "MISSING_FILE",
                    "processed": 0,
                    "package_id": str(row["package_id"]),
                    "file_name": row["file_name"],
                }
            raise RuntimeError(f"US TTAB source file is missing: {row['file_name']}")

        totals = ingest_ttab_package(
            str(row["package_id"]),
            path,
            raw_root,
            trigger_type="RETRY_US_TTAB" if retry else "RUN_US_TTAB",
            retrying=retry or row["status"] == "INTERRUPTED",
        )
        return {
            "status": "SUCCESS",
            "processed": 1,
            "package_id": str(row["package_id"]),
            "file_name": row["file_name"],
            "totals": totals,
        }
