from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.admin_api import (
    JURISDICTION_BY_DOMAIN,
    _domain_for_jurisdiction,
    _job_domain,
    _raw_inventory,
)
from app.db import postgres_conn


router = APIRouter(prefix="/api/admin/v2", tags=["admin-v2"])


def _page_result(
    items: list[dict[str, Any]], *, page: int, page_size: int, total: int
) -> dict[str, Any]:
    pages = max(1, math.ceil(total / page_size)) if total else 0
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": pages,
    }


def _normalize_page(page: int, page_size: int) -> tuple[int, int, int]:
    page = max(1, int(page))
    page_size = max(10, min(int(page_size), 200))
    return page, page_size, (page - 1) * page_size


@router.get("/packages")
def admin_packages_page(
    domain: str = "",
    status: str = "",
    q: str = "",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=200),
):
    page, page_size, offset = _normalize_page(page, page_size)
    clauses: list[str] = []
    params: list[Any] = []

    normalized_domain = domain.strip().upper()
    if normalized_domain:
        jurisdiction = JURISDICTION_BY_DOMAIN.get(normalized_domain)
        if jurisdiction is None:
            raise HTTPException(status_code=400, detail=f"Unsupported domain: {domain}")
        clauses.append("sp.jurisdiction = %s")
        params.append(jurisdiction)
    if status.strip():
        clauses.append("sp.status = %s")
        params.append(status.strip().upper())
    if q.strip():
        pattern = f"%{q.strip()}%"
        clauses.append(
            "(sp.file_name ILIKE %s OR sp.package_kind ILIKE %s OR "
            "sp.partition_value ILIKE %s OR sp.sha256 ILIKE %s)"
        )
        params.extend([pattern] * 4)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""

    count_sql = f"SELECT count(*) AS total FROM control.source_package AS sp {where}"
    list_sql = f"""
        SELECT sp.package_id, sp.package_sequence, sp.jurisdiction, sp.file_name,
               sp.file_size, sp.sha256, sp.package_kind, sp.partition_dimension,
               sp.partition_value, sp.source_period_start, sp.source_period_end,
               sp.status, sp.schema_version, sp.first_seen_at, sp.processed_at,
               sp.error_message,
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
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(count_sql, params)
            total = int(cur.fetchone()["total"] or 0)
            cur.execute(list_sql, [*params, page_size, offset])
            items = [dict(row) for row in cur.fetchall()]
    for item in items:
        item["domain"] = _domain_for_jurisdiction(item.get("jurisdiction"))
    return _page_result(items, page=page, page_size=page_size, total=total)


_JOB_DOMAIN_SQL = """
CASE
    WHEN position('ASSIGNMENT' in upper(job_type)) > 0 THEN 'US_ASSIGNMENT'
    WHEN position('TTAB' in upper(job_type)) > 0 THEN 'US_TTAB'
    WHEN left(upper(job_type), 3) = 'US_'
         OR position('US_APPLICATION' in upper(job_type)) > 0
    THEN 'US_APPLICATION'
    WHEN left(upper(job_type), 3) = 'CN_'
         OR position('_CN_' in upper(job_type)) > 0
    THEN 'CN'
    ELSE 'SYSTEM'
END
"""


@router.get("/jobs")
def admin_jobs_page(
    domain: str = "",
    status: str = "",
    q: str = "",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=200),
):
    page, page_size, offset = _normalize_page(page, page_size)
    clauses: list[str] = []
    params: list[Any] = []
    if domain.strip():
        clauses.append("domain = %s")
        params.append(domain.strip().upper())
    if status.strip():
        clauses.append("status = %s")
        params.append(status.strip().upper())
    if q.strip():
        pattern = f"%{q.strip()}%"
        clauses.append(
            "(job_type ILIKE %s OR trigger_type ILIKE %s OR "
            "COALESCE(error_message, '') ILIKE %s)"
        )
        params.extend([pattern] * 3)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    base = f"""
        WITH base AS (
            SELECT run_id, job_type, trigger_type, status, started_at, finished_at,
                   payload, metrics, error_message,
                   CASE WHEN finished_at IS NOT NULL
                        THEN extract(epoch FROM finished_at - started_at)
                        ELSE extract(epoch FROM now() - started_at)
                   END AS duration_seconds,
                   {_JOB_DOMAIN_SQL} AS domain
            FROM control.job_run
        )
    """
    count_sql = base + f"SELECT count(*) AS total FROM base {where}"
    list_sql = base + f"""
        SELECT * FROM base
        {where}
        ORDER BY started_at DESC
        LIMIT %s OFFSET %s
    """
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(count_sql, params)
            total = int(cur.fetchone()["total"] or 0)
            cur.execute(list_sql, [*params, page_size, offset])
            items = [dict(row) for row in cur.fetchall()]
    for item in items:
        item["duration_seconds"] = float(item.get("duration_seconds") or 0)
        item["domain"] = _job_domain(str(item.get("job_type") or ""))
    return _page_result(items, page=page, page_size=page_size, total=total)


@router.get("/raw")
def admin_raw_page(
    domain: str = "",
    area: str = "",
    q: str = "",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=200),
):
    page, page_size, offset = _normalize_page(page, page_size)
    inventory = _raw_inventory(limit=1_000_000)
    normalized_domain = domain.strip().upper()
    normalized_area = area.strip().lower()
    query = q.strip().casefold()
    items = []
    for item in inventory["files"]:
        if (
            normalized_domain
            and str(item.get("domain") or "").upper() != normalized_domain
        ):
            continue
        if normalized_area and str(item.get("area") or "").lower() != normalized_area:
            continue
        if query:
            haystack = " ".join(
                str(item.get(key) or "")
                for key in ("file_name", "relative_path", "raw_class", "suffix")
            ).casefold()
            if query not in haystack:
                continue
        items.append(item)
    total = len(items)
    return _page_result(
        items[offset : offset + page_size],
        page=page,
        page_size=page_size,
        total=total,
    ) | {
        "total_files_all": int(inventory["total_files"]),
        "total_bytes_all": int(inventory["total_bytes"]),
        "buckets": inventory["buckets"],
    }


@router.get("/contact-tasks")
def admin_contact_tasks_page(
    status: str = "",
    q: str = "",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=200),
):
    page, page_size, offset = _normalize_page(page, page_size)
    clauses: list[str] = []
    params: list[Any] = []
    if status.strip():
        clauses.append("status = %s")
        params.append(status.strip().upper())
    if q.strip():
        pattern = f"%{q.strip()}%"
        clauses.append(
            "(file_name ILIKE %s OR detected_profile ILIKE %s OR "
            "COALESCE(error_message, '') ILIKE %s)"
        )
        params.extend([pattern] * 3)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    count_sql = f"SELECT count(*) AS total FROM contact.ingest_task {where}"
    list_sql = f"""
        SELECT task_id, source_sha256, file_name, file_path, file_size,
               file_modified_at, file_type, ingest_version, status, detected_profile,
               plan_summary, error_message, discovered_at, last_seen_at,
               started_at, finished_at, archived_path
        FROM contact.ingest_task
        {where}
        ORDER BY discovered_at DESC, file_name
        LIMIT %s OFFSET %s
    """
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(count_sql, params)
            total = int(cur.fetchone()["total"] or 0)
            cur.execute(list_sql, [*params, page_size, offset])
            items = [dict(row) for row in cur.fetchall()]
    return _page_result(items, page=page, page_size=page_size, total=total)
