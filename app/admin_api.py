from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.config import get_settings
from app.db import clickhouse_client, postgres_conn
from app.version import engine_version


router = APIRouter(prefix="/api/admin", tags=["admin"])

DOMAIN_BY_JURISDICTION = {
    "CN": "CN",
    "US": "US_APPLICATION",
    "US_ASSIGNMENT": "US_ASSIGNMENT",
    "US_TTAB": "US_TTAB",
}
JURISDICTION_BY_DOMAIN = {value: key for key, value in DOMAIN_BY_JURISDICTION.items()}
RAW_FOLDER_DOMAINS = {
    "cn": "CN",
    "us": "US_APPLICATION",
    "us_assignment": "US_ASSIGNMENT",
    "us_ttab": "US_TTAB",
}
SUPPORTED_DOMAINS = tuple(JURISDICTION_BY_DOMAIN)
RAW_SUFFIXES = {".zip", ".xml", ".csv", ".xls", ".xlsx", ".json"}
_US_HISTORY_RE = re.compile(r"^apc\d{8}-\d{8}-\d{2}\.zip$", re.I)
_US_DAILY_RE = re.compile(r"^apc\d{6}\.zip$", re.I)
_CN_MONTH_RE = re.compile(r"^\d{4}_\d{1,2}\.zip$", re.I)


def _domain_for_jurisdiction(jurisdiction: str | None) -> str:
    return DOMAIN_BY_JURISDICTION.get(str(jurisdiction or "").upper(), "OTHER")


def _job_domain(job_type: str) -> str:
    value = (job_type or "").upper()
    if "ASSIGNMENT" in value:
        return "US_ASSIGNMENT"
    if "TTAB" in value:
        return "US_TTAB"
    if value.startswith("US_") or "US_APPLICATION" in value:
        return "US_APPLICATION"
    if value.startswith("CN_") or "_CN_" in value:
        return "CN"
    return "SYSTEM"


def _raw_domain(relative_path: Path) -> str:
    parts = relative_path.parts
    if len(parts) < 2:
        return "OTHER"
    return RAW_FOLDER_DOMAINS.get(parts[1].lower(), "OTHER")


def _raw_class(domain: str, relative_path: Path) -> str:
    name = relative_path.name
    lower_parts = {part.lower() for part in relative_path.parts}
    if domain == "US_APPLICATION":
        if _US_HISTORY_RE.fullmatch(name):
            return "APPLICATION_HISTORICAL"
        if _US_DAILY_RE.fullmatch(name):
            return "APPLICATION_DAILY"
        return "APPLICATION_OTHER"
    if domain == "CN":
        if _CN_MONTH_RE.fullmatch(name):
            return "CN_MONTHLY"
        return "CN_OTHER"
    if domain == "US_ASSIGNMENT":
        if "daily" in lower_parts:
            return "ASSIGNMENT_DAILY"
        if "historical" in lower_parts or "snapshot" in lower_parts:
            return "ASSIGNMENT_HISTORICAL"
        return "ASSIGNMENT_PACKAGE"
    if domain == "US_TTAB":
        if "daily" in lower_parts:
            return "TTAB_DAILY"
        if "historical" in lower_parts or "snapshot" in lower_parts:
            return "TTAB_HISTORICAL"
        return "TTAB_PACKAGE"
    return "OTHER"


def _raw_inventory(limit: int = 500) -> dict[str, Any]:
    raw_root = get_settings().raw_data_root
    files: list[dict[str, Any]] = []
    buckets: dict[tuple[str, str, str], dict[str, Any]] = {}
    total_files = 0
    total_bytes = 0

    for area in ("incoming", "archive", "quarantine"):
        area_root = raw_root / area
        if not area_root.exists():
            continue
        for path in area_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in RAW_SUFFIXES:
                continue
            stat = path.stat()
            relative = path.relative_to(raw_root)
            domain = _raw_domain(relative)
            raw_class = _raw_class(domain, relative)
            size = int(stat.st_size)
            total_files += 1
            total_bytes += size
            key = (domain, area, raw_class)
            bucket = buckets.setdefault(
                key,
                {
                    "domain": domain,
                    "area": area,
                    "raw_class": raw_class,
                    "file_count": 0,
                    "bytes": 0,
                },
            )
            bucket["file_count"] += 1
            bucket["bytes"] += size
            files.append(
                {
                    "domain": domain,
                    "area": area,
                    "raw_class": raw_class,
                    "file_name": path.name,
                    "relative_path": str(relative).replace("\\", "/"),
                    "suffix": path.suffix.lower(),
                    "file_size": size,
                    "modified_at": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat(),
                }
            )

    files.sort(key=lambda item: (item["modified_at"], item["relative_path"]), reverse=True)
    return {
        "raw_root": str(raw_root),
        "classification_semantics": "PATH_AND_FILENAME_INVENTORY_ONLY_NOT_SOURCE_PRECEDENCE",
        "total_files": total_files,
        "total_bytes": total_bytes,
        "buckets": sorted(
            buckets.values(),
            key=lambda item: (item["domain"], item["area"], item["raw_class"]),
        ),
        "files": files[:limit],
        "files_returned": min(len(files), limit),
    }


def _package_progress() -> dict[str, Any]:
    sql = """
        SELECT jurisdiction, package_kind, status,
               count(*) AS package_count,
               coalesce(sum(file_size), 0) AS total_bytes,
               min(source_period_start) AS source_period_start,
               max(source_period_end) AS source_period_end,
               max(processed_at) AS latest_processed_at
        FROM control.source_package
        GROUP BY jurisdiction, package_kind, status
        ORDER BY jurisdiction, package_kind, status
    """
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = list(cur.fetchall())

    domains: dict[str, dict[str, Any]] = {}
    for row in rows:
        domain = _domain_for_jurisdiction(row["jurisdiction"])
        item = domains.setdefault(
            domain,
            {
                "domain": domain,
                "jurisdiction": row["jurisdiction"],
                "total": 0,
                "success": 0,
                "registered": 0,
                "processing": 0,
                "failed": 0,
                "missing_file": 0,
                "interrupted": 0,
                "bytes": 0,
                "source_period_start": None,
                "source_period_end": None,
                "latest_processed_at": None,
                "kinds": [],
            },
        )
        count = int(row["package_count"] or 0)
        status = str(row["status"] or "UNKNOWN").upper()
        item["total"] += count
        item["bytes"] += int(row["total_bytes"] or 0)
        status_key = {
            "SUCCESS": "success",
            "REGISTERED": "registered",
            "PROCESSING": "processing",
            "FAILED": "failed",
            "MISSING_FILE": "missing_file",
            "INTERRUPTED": "interrupted",
        }.get(status)
        if status_key:
            item[status_key] += count
        start = row["source_period_start"]
        end = row["source_period_end"]
        processed = row["latest_processed_at"]
        if start and (item["source_period_start"] is None or start < item["source_period_start"]):
            item["source_period_start"] = start
        if end and (item["source_period_end"] is None or end > item["source_period_end"]):
            item["source_period_end"] = end
        if processed and (
            item["latest_processed_at"] is None or processed > item["latest_processed_at"]
        ):
            item["latest_processed_at"] = processed
        item["kinds"].append(
            {
                "package_kind": row["package_kind"],
                "status": status,
                "count": count,
                "bytes": int(row["total_bytes"] or 0),
                "source_period_start": start,
                "source_period_end": end,
            }
        )

    for domain in SUPPORTED_DOMAINS:
        domains.setdefault(
            domain,
            {
                "domain": domain,
                "jurisdiction": JURISDICTION_BY_DOMAIN[domain],
                "total": 0,
                "success": 0,
                "registered": 0,
                "processing": 0,
                "failed": 0,
                "missing_file": 0,
                "interrupted": 0,
                "bytes": 0,
                "source_period_start": None,
                "source_period_end": None,
                "latest_processed_at": None,
                "kinds": [],
            },
        )

    for item in domains.values():
        total = int(item["total"])
        item["progress_pct"] = round((item["success"] / total * 100), 2) if total else 0.0

    ordered = [domains[domain] for domain in SUPPORTED_DOMAINS]
    others = [item for key, item in domains.items() if key not in SUPPORTED_DOMAINS]
    return {"domains": ordered + others}


def _fact_storage() -> dict[str, Any]:
    result = clickhouse_client().query(
        """
        SELECT table, sum(rows) AS physical_rows, sum(bytes_on_disk) AS bytes_on_disk
        FROM system.parts
        WHERE database = 'markorbit_facts'
          AND active = 1
          AND (table LIKE 'cn_%' OR table LIKE 'us_%')
        GROUP BY table
        ORDER BY table
        """
    )
    tables: list[dict[str, Any]] = []
    domain_totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {"physical_rows": 0, "bytes_on_disk": 0, "table_count": 0}
    )
    for table, rows, size in result.result_rows:
        name = str(table)
        if name.startswith("cn_"):
            domain = "CN"
        elif name.startswith("us_assignment_"):
            domain = "US_ASSIGNMENT"
        elif name.startswith("us_ttab_"):
            domain = "US_TTAB"
        elif name.startswith("us_"):
            domain = "US_APPLICATION"
        else:
            domain = "OTHER"
        row = {
            "domain": domain,
            "table_name": name,
            "physical_rows": int(rows or 0),
            "bytes_on_disk": int(size or 0),
        }
        tables.append(row)
        domain_totals[domain]["physical_rows"] += row["physical_rows"]
        domain_totals[domain]["bytes_on_disk"] += row["bytes_on_disk"]
        domain_totals[domain]["table_count"] += 1
    return {
        "count_semantics": "CLICKHOUSE_ACTIVE_PART_PHYSICAL_ROWS_NOT_FINAL_LOGICAL_COUNT",
        "domains": [
            {"domain": domain, **values}
            for domain, values in sorted(domain_totals.items())
        ],
        "tables": tables,
    }


def _recent_jobs(limit: int) -> list[dict[str, Any]]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_id, job_type, trigger_type, status, started_at, finished_at,
                       payload, metrics, error_message,
                       CASE WHEN finished_at IS NOT NULL
                            THEN extract(epoch FROM finished_at - started_at)
                            ELSE extract(epoch FROM now() - started_at)
                       END AS duration_seconds
                FROM control.job_run
                ORDER BY started_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = [dict(row) for row in cur.fetchall()]
    for row in rows:
        row["domain"] = _job_domain(str(row.get("job_type") or ""))
        row["duration_seconds"] = float(row.get("duration_seconds") or 0)
    return rows


@router.get("/overview")
def admin_overview():
    progress = _package_progress()
    raw = _raw_inventory(limit=0)
    jobs = _recent_jobs(30)
    facts = _fact_storage()
    return {
        "engine_version": engine_version(),
        "packages": progress,
        "raw": {
            "total_files": raw["total_files"],
            "total_bytes": raw["total_bytes"],
            "buckets": raw["buckets"],
        },
        "jobs": {
            "running": sum(1 for row in jobs if row["status"] == "RUNNING"),
            "failed": sum(1 for row in jobs if row["status"] == "FAILED"),
            "recent": jobs,
        },
        "facts": facts,
    }


@router.get("/raw-inventory")
def admin_raw_inventory(limit: int = Query(default=500, ge=0, le=5000)):
    return _raw_inventory(limit=limit)


@router.get("/packages")
def admin_packages(
    domain: str | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    clauses: list[str] = []
    params: list[Any] = []
    if domain:
        normalized = domain.strip().upper()
        jurisdiction = JURISDICTION_BY_DOMAIN.get(normalized)
        if jurisdiction is None:
            raise HTTPException(status_code=400, detail=f"Unsupported domain: {domain}")
        clauses.append("sp.jurisdiction = %s")
        params.append(jurisdiction)
    if status:
        clauses.append("sp.status = %s")
        params.append(status.strip().upper())
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    sql = f"""
        SELECT sp.package_id, sp.package_sequence, sp.jurisdiction, sp.file_name,
               sp.file_size, sp.sha256, sp.package_kind, sp.partition_dimension,
               sp.partition_value, sp.source_period_start, sp.source_period_end,
               sp.source_rank, sp.status, sp.schema_version, sp.first_seen_at,
               sp.last_seen_at, sp.processed_at, sp.archived_path, sp.error_message,
               coalesce(pf.internal_file_count, 0) AS internal_file_count,
               coalesce(pf.logical_rows, 0) AS internal_logical_rows,
               coalesce(pf.failed_rows, 0) AS internal_failed_rows
        FROM control.source_package AS sp
        LEFT JOIN (
            SELECT package_id, count(*) AS internal_file_count,
                   coalesce(sum(logical_rows), 0) AS logical_rows,
                   coalesce(sum(failed_rows), 0) AS failed_rows
            FROM control.source_package_file
            GROUP BY package_id
        ) AS pf USING (package_id)
        {where}
        ORDER BY sp.source_rank DESC, sp.package_sequence DESC
        LIMIT %s OFFSET %s
    """
    params.extend([limit, offset])
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = [dict(row) for row in cur.fetchall()]
    for row in rows:
        row["domain"] = _domain_for_jurisdiction(row["jurisdiction"])
    return rows


@router.get("/packages/{package_id}")
def admin_package_detail(package_id: str):
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM control.source_package WHERE package_id = %s
                """,
                (package_id,),
            )
            package = cur.fetchone()
            if not package:
                raise HTTPException(status_code=404, detail="Source package not found")
            cur.execute(
                """
                SELECT internal_name, original_internal_name, file_role, file_size,
                       compressed_size, filename_encoding, filename_repaired,
                       content_encoding, physical_rows, logical_rows,
                       continuation_rows, repaired_rows, failed_rows,
                       replacement_chars, max_record_length, max_field_length, metrics
                FROM control.source_package_file
                WHERE package_id = %s
                ORDER BY file_role, internal_name
                """,
                (package_id,),
            )
            files = [dict(row) for row in cur.fetchall()]
            cur.execute(
                """
                SELECT issue_type, severity, occurrence_count, source_file, source_row,
                       raw_excerpt, details, status, created_at, updated_at
                FROM control.data_quality_issue
                WHERE package_id = %s
                ORDER BY severity DESC, occurrence_count DESC, issue_type
                LIMIT 200
                """,
                (package_id,),
            )
            quality = [dict(row) for row in cur.fetchall()]
            cur.execute(
                """
                SELECT run_id, job_type, trigger_type, status, started_at, finished_at,
                       metrics, error_message
                FROM control.job_run
                WHERE payload ->> 'package_id' = %s
                ORDER BY started_at DESC
                LIMIT 50
                """,
                (package_id,),
            )
            jobs = [dict(row) for row in cur.fetchall()]
    package_dict = dict(package)
    package_dict["domain"] = _domain_for_jurisdiction(package_dict["jurisdiction"])
    return {"package": package_dict, "internal_files": files, "quality_issues": quality, "jobs": jobs}


@router.get("/jobs")
def admin_jobs(limit: int = Query(default=100, ge=1, le=1000)):
    return _recent_jobs(limit)
