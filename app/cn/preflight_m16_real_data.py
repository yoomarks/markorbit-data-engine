from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from app.cn.package_meta import infer_package_descriptor
from app.config import get_settings
from app.db import clickhouse_client, postgres_conn
from app.version import engine_version


PREFLIGHT_NAME = "CN_M16_REAL_DATA_PREFLIGHT"
PREFLIGHT_VERSION = "CN_M16_REAL_DATA_PREFLIGHT_V1_NON_DESTRUCTIVE"
REQUIRED_ENGINE_VERSION = "M1.6"
REQUIRED_M16_COLUMNS = {
    ("cn_goods_item_current", "goods_item_key"),
    ("cn_goods_item_current", "operational_effect"),
    ("cn_goods_item_current", "first_source_package_id"),
    ("cn_goods_item_observation", "transition_type"),
    ("cn_goods_scope_lifecycle_current", "all_known_goods_inactive"),
    ("cn_goods_scope_lifecycle_current", "all_known_goods_final_inactive"),
    ("cn_goods_scope_lifecycle_current", "code_2_item_count"),
}


@dataclass(frozen=True)
class RawPackage:
    path: Path
    location: str
    file_name: str
    package_kind: str
    source_period_end: date | None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inventory_raw_packages(raw_root: Path) -> list[RawPackage]:
    rows: list[RawPackage] = []
    for location in ("incoming", "archive"):
        folder = raw_root / location / "cn"
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*.zip"), key=lambda item: item.name.lower()):
            descriptor = infer_package_descriptor(path)
            rows.append(
                RawPackage(
                    path=path,
                    location=location,
                    file_name=path.name,
                    package_kind=descriptor.package_kind,
                    source_period_end=descriptor.source_period_end,
                )
            )
    return rows


def _candidate_paths(raw_root: Path, file_name: str, archived_path: str | None) -> list[Path]:
    candidates: list[Path] = []
    if archived_path:
        candidates.append(Path(archived_path))
    candidates.extend(
        [
            raw_root / "incoming" / "cn" / file_name,
            raw_root / "archive" / "cn" / file_name,
        ]
    )
    stem = Path(file_name).stem
    suffix = Path(file_name).suffix
    archive = raw_root / "archive" / "cn"
    if archive.exists():
        candidates.extend(sorted(archive.glob(f"{stem}_*{suffix}")))

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def verify_registered_sources(
    packages: Iterable[dict[str, Any]],
    *,
    raw_root: Path,
) -> dict[str, Any]:
    checked = 0
    resolved = 0
    missing: list[dict[str, Any]] = []
    mismatched: list[dict[str, Any]] = []
    hash_cache: dict[Path, str] = {}

    for package in packages:
        checked += 1
        expected = str(package.get("sha256") or "").strip().lower()
        file_name = str(package.get("file_name") or "")
        package_id = str(package.get("package_id") or "")
        candidates = _candidate_paths(
            raw_root,
            file_name,
            str(package.get("archived_path") or "") or None,
        )
        existing = [path for path in candidates if path.is_file()]
        if not existing:
            missing.append(
                {
                    "package_id": package_id,
                    "file_name": file_name,
                    "expected_sha256": expected,
                }
            )
            continue

        matched_path: Path | None = None
        observed: list[dict[str, str]] = []
        for path in existing:
            if path not in hash_cache:
                hash_cache[path] = _sha256(path)
            actual = hash_cache[path].lower()
            observed.append({"path": str(path), "sha256": actual})
            if not expected or actual == expected:
                matched_path = path
                break

        if matched_path is None:
            mismatched.append(
                {
                    "package_id": package_id,
                    "file_name": file_name,
                    "expected_sha256": expected,
                    "observed": observed,
                }
            )
            continue
        resolved += 1

    return {
        "checked": checked,
        "resolved": resolved,
        "missing": missing,
        "sha256_mismatch": mismatched,
    }


def evaluate_preflight(snapshot: dict[str, Any]) -> dict[str, Any]:
    hard_fail_reasons: list[str] = []
    warning_reasons: list[str] = []

    if snapshot.get("engine_version") != REQUIRED_ENGINE_VERSION:
        hard_fail_reasons.append("unexpected_engine_version")
    if not snapshot.get("postgres_ok"):
        hard_fail_reasons.append("postgres_unavailable")
    if not snapshot.get("clickhouse_ok"):
        hard_fail_reasons.append("clickhouse_unavailable")

    missing_columns = snapshot.get("missing_m16_columns") or []
    if missing_columns:
        hard_fail_reasons.append("m16_schema_incomplete")
    if not snapshot.get("ingestion_lock_available", False):
        hard_fail_reasons.append("cn_ingestion_lock_busy")
    if int(snapshot.get("processing_packages") or 0):
        hard_fail_reasons.append("processing_packages_present")
    if int(snapshot.get("running_ingest_jobs") or 0):
        hard_fail_reasons.append("running_cn_ingest_jobs_present")

    source_verification = snapshot.get("registered_source_verification") or {}
    if source_verification.get("missing"):
        hard_fail_reasons.append("registered_source_file_missing")
    if source_verification.get("sha256_mismatch"):
        hard_fail_reasons.append("registered_source_sha256_mismatch")

    scope_count = int(snapshot.get("current_scope_count") or 0)
    item_count = int(snapshot.get("current_goods_item_count") or 0)
    lifecycle_count = int(snapshot.get("current_goods_lifecycle_count") or 0)
    if scope_count > 0 and item_count == 0:
        hard_fail_reasons.append("m15_scope_without_m16_durable_items")
    if item_count > 0 and lifecycle_count == 0:
        hard_fail_reasons.append("durable_items_without_lifecycle_scope")

    incoming_zips = int(snapshot.get("incoming_zip_count") or 0)
    registered_packages = int(snapshot.get("registered_package_count") or 0)
    successful_packages = int(snapshot.get("successful_package_count") or 0)
    if incoming_zips == 0 and registered_packages == 0:
        hard_fail_reasons.append("no_cn_raw_packages_available")
    elif incoming_zips == 0 and successful_packages == 0:
        hard_fail_reasons.append("no_replayable_incoming_packages")

    unknown_raw = int(snapshot.get("unknown_raw_package_count") or 0)
    if unknown_raw:
        warning_reasons.append("unknown_cn_raw_package_filename_pattern")
    if int(snapshot.get("duplicate_raw_filename_count") or 0):
        warning_reasons.append("raw_package_present_in_multiple_locations")
    if int(snapshot.get("failed_or_interrupted_packages") or 0):
        warning_reasons.append("retryable_failed_or_interrupted_packages_present")
    if not snapshot.get("latest_monthly_coverage_date"):
        warning_reasons.append("no_successful_monthly_coverage_clock")
    if registered_packages == 0 and incoming_zips > 0:
        warning_reasons.append("clean_registry_waiting_for_replay")

    mode = "UNKNOWN"
    if registered_packages == 0 and incoming_zips > 0 and scope_count == 0 and item_count == 0:
        mode = "CLEAN_RESET_READY_FOR_REPLAY"
    elif successful_packages > 0 and item_count > 0:
        mode = "M16_DATA_PRESENT_STABLE_SNAPSHOT"
    elif registered_packages > 0:
        mode = "PARTIAL_OR_PENDING_REPLAY"

    status = "FAIL" if hard_fail_reasons else ("PASS_WITH_WARNINGS" if warning_reasons else "PASS")
    return {
        "status": status,
        "audit": PREFLIGHT_NAME,
        "preflight_version": PREFLIGHT_VERSION,
        "mode": mode,
        "hard_fail_reasons": hard_fail_reasons,
        "warning_reasons": warning_reasons,
        "safe_to_run_replay_command": not hard_fail_reasons,
        "safe_to_run_inference_audit": (
            not hard_fail_reasons
            and bool(snapshot.get("latest_monthly_coverage_date"))
            and item_count > 0
            and lifecycle_count > 0
        ),
        "snapshot": snapshot,
    }


def _postgres_snapshot() -> dict[str, Any]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            postgres_ok = cur.fetchone()["ok"] == 1

            cur.execute(
                """
                SELECT
                    count(*) AS registered_package_count,
                    count(*) FILTER (WHERE status = 'SUCCESS') AS successful_package_count,
                    count(*) FILTER (WHERE status = 'PROCESSING') AS processing_packages,
                    count(*) FILTER (WHERE status IN ('FAILED', 'INTERRUPTED', 'MISSING_FILE'))
                        AS failed_or_interrupted_packages,
                    max(COALESCE(dataset_release_date, source_period_end)) FILTER (
                        WHERE status = 'SUCCESS' AND package_kind = 'MONTHLY_PATCH'
                    ) AS latest_monthly_coverage_date
                FROM control.source_package
                WHERE jurisdiction = 'CN'
                """
            )
            inventory = dict(cur.fetchone())

            cur.execute(
                """
                SELECT count(*) AS running_ingest_jobs
                FROM control.job_run
                WHERE job_type = 'CN_PACKAGE_INGESTION'
                  AND status = 'RUNNING'
                """
            )
            running_jobs = int(cur.fetchone()["running_ingest_jobs"] or 0)

            cur.execute(
                """
                SELECT pg_try_advisory_lock(
                    hashtext('markorbit:cn:package-ingestion')::bigint
                ) AS acquired
                """
            )
            lock_available = bool(cur.fetchone()["acquired"])
            if lock_available:
                cur.execute(
                    """
                    SELECT pg_advisory_unlock(
                        hashtext('markorbit:cn:package-ingestion')::bigint
                    )
                    """
                )
            conn.commit()

            cur.execute(
                """
                SELECT package_id, file_name, sha256, archived_path
                FROM control.source_package
                WHERE jurisdiction = 'CN'
                ORDER BY source_rank, package_sequence
                """
            )
            packages = [dict(row) for row in cur.fetchall()]

    return {
        "postgres_ok": postgres_ok,
        **inventory,
        "running_ingest_jobs": running_jobs,
        "ingestion_lock_available": lock_available,
        "registered_packages": packages,
    }


def _clickhouse_snapshot() -> dict[str, Any]:
    client = clickhouse_client()
    clickhouse_ok = client.command("SELECT 1") == 1
    columns = client.query(
        """
        SELECT table, name
        FROM system.columns
        WHERE database = 'markorbit_facts'
        """
    ).result_rows
    available = {(str(table), str(name)) for table, name in columns}
    missing = sorted(REQUIRED_M16_COLUMNS - available)

    counts = client.query(
        """
        SELECT
            (SELECT count() FROM markorbit_facts.cn_case_scope_current FINAL
             WHERE is_deleted = 0) AS current_scope_count,
            (SELECT count() FROM markorbit_facts.cn_goods_item_current FINAL
             WHERE is_deleted = 0) AS current_goods_item_count,
            (SELECT count() FROM markorbit_facts.cn_goods_scope_lifecycle_current FINAL
             WHERE is_deleted = 0) AS current_goods_lifecycle_count,
            (SELECT count() FROM markorbit_facts.cn_stage_goods) AS stage_goods_count
        """
    ).result_rows[0]

    return {
        "clickhouse_ok": clickhouse_ok,
        "missing_m16_columns": [f"{table}.{column}" for table, column in missing],
        "current_scope_count": int(counts[0] or 0),
        "current_goods_item_count": int(counts[1] or 0),
        "current_goods_lifecycle_count": int(counts[2] or 0),
        "stage_goods_count": int(counts[3] or 0),
    }


def build_preflight() -> dict[str, Any]:
    settings = get_settings()
    raw_packages = inventory_raw_packages(settings.raw_data_root)
    names = Counter(row.file_name for row in raw_packages)
    raw_kind_counts = Counter(row.package_kind for row in raw_packages)

    snapshot: dict[str, Any] = {
        "engine_version": engine_version(),
        "raw_data_root": str(settings.raw_data_root),
        "incoming_zip_count": sum(row.location == "incoming" for row in raw_packages),
        "archive_zip_count": sum(row.location == "archive" for row in raw_packages),
        "unknown_raw_package_count": raw_kind_counts.get("UNKNOWN", 0),
        "duplicate_raw_filename_count": sum(count > 1 for count in names.values()),
        "raw_package_kind_counts": dict(sorted(raw_kind_counts.items())),
        "raw_packages": [
            {
                "location": row.location,
                "file_name": row.file_name,
                "package_kind": row.package_kind,
                "source_period_end": row.source_period_end,
            }
            for row in raw_packages
        ],
    }

    try:
        pg = _postgres_snapshot()
        packages = pg.pop("registered_packages")
        snapshot.update(pg)
        snapshot["registered_source_verification"] = verify_registered_sources(
            packages,
            raw_root=settings.raw_data_root,
        )
    except Exception as exc:
        snapshot.update(
            {
                "postgres_ok": False,
                "postgres_error": str(exc),
                "registered_package_count": 0,
                "successful_package_count": 0,
                "processing_packages": 0,
                "failed_or_interrupted_packages": 0,
                "running_ingest_jobs": 0,
                "ingestion_lock_available": False,
                "latest_monthly_coverage_date": None,
                "registered_source_verification": {
                    "checked": 0,
                    "resolved": 0,
                    "missing": [],
                    "sha256_mismatch": [],
                },
            }
        )

    try:
        snapshot.update(_clickhouse_snapshot())
    except Exception as exc:
        snapshot.update(
            {
                "clickhouse_ok": False,
                "clickhouse_error": str(exc),
                "missing_m16_columns": [
                    f"{table}.{column}" for table, column in sorted(REQUIRED_M16_COLUMNS)
                ],
                "current_scope_count": 0,
                "current_goods_item_count": 0,
                "current_goods_lifecycle_count": 0,
                "stage_goods_count": 0,
            }
        )

    return evaluate_preflight(snapshot)


def main() -> None:
    print(json.dumps(build_preflight(), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
