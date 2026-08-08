from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from app.cn.entity import EntityCandidate
from app.cn.package_meta import infer_package_descriptor
from app.db import postgres_conn
from app.domain import DiscoveredPackage


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def register_package(package: DiscoveredPackage) -> tuple[str, bool]:
    descriptor = infer_package_descriptor(package.path)
    sql = """
    INSERT INTO control.source_package (
        jurisdiction, file_name, file_path, file_size, sha256,
        source_modified_at, package_kind, partition_dimension,
        partition_value, source_period_start, source_period_end,
        source_sequence, status
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'REGISTERED')
    ON CONFLICT (sha256)
    DO UPDATE SET
        file_name = EXCLUDED.file_name,
        file_path = EXCLUDED.file_path,
        file_size = EXCLUDED.file_size,
        source_modified_at = EXCLUDED.source_modified_at,
        package_kind = EXCLUDED.package_kind,
        partition_dimension = EXCLUDED.partition_dimension,
        partition_value = EXCLUDED.partition_value,
        source_period_start = EXCLUDED.source_period_start,
        source_period_end = EXCLUDED.source_period_end,
        source_sequence = EXCLUDED.source_sequence,
        status = CASE
            WHEN control.source_package.status = 'MISSING_FILE' THEN 'FAILED'
            ELSE control.source_package.status
        END,
        last_seen_at = now()
    RETURNING package_id, package_sequence, (xmax = 0) AS inserted
    """
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    package.jurisdiction,
                    package.file_name,
                    str(package.path),
                    package.file_size,
                    package.sha256,
                    package.modified_at,
                    descriptor.package_kind,
                    descriptor.partition_dimension,
                    descriptor.partition_value,
                    descriptor.source_period_start,
                    descriptor.source_period_end,
                    descriptor.source_sequence,
                ),
            )
            row = cur.fetchone()
            source_rank = descriptor.source_rank(int(row["package_sequence"]))
            cur.execute(
                "UPDATE control.source_package SET source_rank = %s WHERE package_id = %s",
                (source_rank, row["package_id"]),
            )
        conn.commit()
    return str(row["package_id"]), bool(row["inserted"])


def get_package(package_id: str) -> dict[str, Any]:
    sql = """
    SELECT package_id, package_sequence, jurisdiction, file_name, file_path,
           file_size, sha256, package_kind, partition_dimension, partition_value,
           source_period_start, source_period_end, source_sequence, source_rank,
           status, profile, archived_path
    FROM control.source_package
    WHERE package_id = %s
    """
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (package_id,))
            row = cur.fetchone()
    if not row:
        raise KeyError(f"Unknown source package: {package_id}")
    return dict(row)


def create_job_run(
    job_type: str,
    trigger_type: str,
    payload: dict[str, Any] | None = None,
) -> str:
    sql = """
    INSERT INTO control.job_run (job_type, trigger_type, status, payload)
    VALUES (%s, %s, 'RUNNING', %s::jsonb)
    RETURNING run_id
    """
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (job_type, trigger_type, _json(payload or {})))
            run_id = cur.fetchone()["run_id"]
        conn.commit()
    return str(run_id)


def finish_job_run(
    run_id: str,
    status: str,
    metrics: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> None:
    sql = """
    UPDATE control.job_run
    SET status = %s,
        finished_at = now(),
        metrics = %s::jsonb,
        error_message = %s
    WHERE run_id = %s
    """
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (status, _json(metrics or {}), error_message, run_id))
        conn.commit()


def list_recent_runs(limit: int = 50) -> list[dict[str, Any]]:
    sql = """
    SELECT run_id, job_type, trigger_type, status, started_at, finished_at,
           metrics, error_message
    FROM control.job_run
    ORDER BY started_at DESC
    LIMIT %s
    """
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (limit,))
            return list(cur.fetchall())


def list_recent_packages(limit: int = 50) -> list[dict[str, Any]]:
    sql = """
    SELECT package_id, package_sequence, jurisdiction, file_name, file_size,
           sha256, package_kind, partition_dimension, partition_value,
           source_period_start, source_period_end, source_sequence, source_rank,
           status, first_seen_at, last_seen_at, processed_at, archived_path,
           error_message
    FROM control.source_package
    ORDER BY first_seen_at DESC
    LIMIT %s
    """
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (limit,))
            return list(cur.fetchall())


def update_package_status(
    package_id: str,
    status: str,
    *,
    package_kind: str | None = None,
    profile: dict[str, Any] | None = None,
    archived_path: str | None = None,
    error_message: str | None = None,
) -> None:
    sql = """
    UPDATE control.source_package
    SET status = %s,
        package_kind = COALESCE(%s, package_kind),
        profile = COALESCE(%s::jsonb, profile),
        archived_path = COALESCE(%s, archived_path),
        error_message = %s,
        processed_at = CASE WHEN %s = 'SUCCESS' THEN now() ELSE processed_at END,
        last_seen_at = now()
    WHERE package_id = %s
    """
    profile_json = _json(profile) if profile is not None else None
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    status,
                    package_kind,
                    profile_json,
                    archived_path,
                    error_message,
                    status,
                    package_id,
                ),
            )
        conn.commit()


def upsert_package_file(package_id: str, item: dict[str, Any]) -> None:
    sql = """
    INSERT INTO control.source_package_file (
        package_id, internal_name, original_internal_name, file_role,
        file_size, compressed_size, filename_encoding, filename_repaired,
        content_encoding, header_raw, header_canonical, physical_rows,
        logical_rows, continuation_rows, repaired_rows, failed_rows,
        replacement_chars, max_record_length, max_field_length, metrics
    )
    VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s, %s,
        %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
    )
    ON CONFLICT (package_id, internal_name)
    DO UPDATE SET
        original_internal_name = EXCLUDED.original_internal_name,
        file_role = EXCLUDED.file_role,
        file_size = EXCLUDED.file_size,
        compressed_size = EXCLUDED.compressed_size,
        filename_encoding = EXCLUDED.filename_encoding,
        filename_repaired = EXCLUDED.filename_repaired,
        content_encoding = EXCLUDED.content_encoding,
        header_raw = EXCLUDED.header_raw,
        header_canonical = EXCLUDED.header_canonical,
        physical_rows = EXCLUDED.physical_rows,
        logical_rows = EXCLUDED.logical_rows,
        continuation_rows = EXCLUDED.continuation_rows,
        repaired_rows = EXCLUDED.repaired_rows,
        failed_rows = EXCLUDED.failed_rows,
        replacement_chars = EXCLUDED.replacement_chars,
        max_record_length = EXCLUDED.max_record_length,
        max_field_length = EXCLUDED.max_field_length,
        metrics = EXCLUDED.metrics
    """
    repairs = item.get("repairs", {})
    repaired_rows = sum(
        value
        for key, value in repairs.items()
        if key != "OK" and not key.startswith("UNREPAIRABLE")
    )
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    package_id,
                    item["internal_name"],
                    item.get("original_internal_name", item["internal_name"]),
                    item.get("role"),
                    int(item.get("size", 0)),
                    int(item.get("compressed_size", 0)),
                    item.get("filename_encoding"),
                    bool(item.get("filename_repaired", False)),
                    item.get("encoding"),
                    _json(item.get("header_raw", [])),
                    _json(item.get("header_canonical", [])),
                    int(item.get("physical_rows", 0)),
                    int(item.get("logical_rows", 0)),
                    int(item.get("continuation_rows", 0)),
                    int(repaired_rows),
                    int(item.get("failed_rows", 0)),
                    int(item.get("replacement_chars", 0)),
                    int(item.get("max_record_length", 0)),
                    int(item.get("max_field_length", 0)),
                    _json(
                        {
                            "repairs": repairs,
                            "mojibake_cells_repaired": int(
                                item.get("mojibake_cells_repaired", 0)
                            ),
                        }
                    ),
                ),
            )
        conn.commit()


def upsert_entities(rows: Iterable[EntityCandidate], *, conn: Any | None = None) -> None:
    candidates = list(rows)
    if not candidates:
        return
    entity_sql = """
    INSERT INTO entity.entity (
        entity_id, entity_key, entity_type, canonical_name, normalized_name,
        normalized_address, country_code, region_code, city, status,
        resolution_method, source_primary, confidence_score
    )
    VALUES (
        %(entity_id)s, %(entity_key)s, %(entity_type)s, %(canonical_name)s,
        %(normalized_name)s, %(normalized_address)s,
        NULLIF(%(country_code)s, ''), NULLIF(%(region_code)s, ''),
        NULLIF(%(city)s, ''), 'CANDIDATE', %(resolution_method)s,
        'CN_OFFICIAL_DATA', %(confidence_score)s
    )
    ON CONFLICT (entity_id)
    DO UPDATE SET
        canonical_name = CASE
            WHEN length(EXCLUDED.canonical_name) > length(entity.entity.canonical_name)
            THEN EXCLUDED.canonical_name ELSE entity.entity.canonical_name END,
        country_code = COALESCE(entity.entity.country_code, EXCLUDED.country_code),
        region_code = COALESCE(entity.entity.region_code, EXCLUDED.region_code),
        city = COALESCE(entity.entity.city, EXCLUDED.city),
        confidence_score = GREATEST(
            COALESCE(entity.entity.confidence_score, 0),
            COALESCE(EXCLUDED.confidence_score, 0)
        ),
        updated_at = now()
    """
    alias_sql = """
    INSERT INTO entity.entity_alias (
        entity_id, alias_name, normalized_name, source, confidence_score
    )
    VALUES (%(entity_id)s, %(canonical_name)s, %(normalized_name)s,
            'CN_OFFICIAL_DATA', %(confidence_score)s)
    ON CONFLICT (entity_id, normalized_name, source)
    DO UPDATE SET alias_name = EXCLUDED.alias_name,
                  confidence_score = GREATEST(
                      entity.entity_alias.confidence_score,
                      EXCLUDED.confidence_score
                  )
    """
    payload = [candidate.__dict__ for candidate in candidates]
    def execute(target: Any) -> None:
        with target.cursor() as cur:
            cur.executemany(entity_sql, payload)
            cur.executemany(alias_sql, payload)
    if conn is not None:
        execute(conn)
        return
    with postgres_conn() as owned_conn:
        execute(owned_conn)
        owned_conn.commit()


def upsert_entity_mentions(rows: list[dict[str, Any]], *, conn: Any | None = None) -> None:
    if not rows:
        return
    sql = """
    INSERT INTO entity.entity_mention (
        mention_id, jurisdiction, source_case_key, role, raw_name,
        normalized_name, raw_address, normalized_address, country_code,
        region_code, city, geo_confidence, source_package_id,
        source_internal_file, source_start_line, entity_id, match_status,
        resolution_method
    )
    VALUES (
        %(mention_id)s, 'CN', %(source_case_key)s, %(role)s, %(raw_name)s,
        %(normalized_name)s, %(raw_address)s, %(normalized_address)s,
        NULLIF(%(country_code)s, ''), NULLIF(%(region_code)s, ''),
        NULLIF(%(city)s, ''), %(geo_confidence)s, %(source_package_id)s,
        %(source_internal_file)s, %(source_start_line)s, %(entity_id)s,
        %(match_status)s, %(resolution_method)s
    )
    ON CONFLICT (mention_id)
    DO UPDATE SET
        raw_name = EXCLUDED.raw_name,
        normalized_name = EXCLUDED.normalized_name,
        raw_address = EXCLUDED.raw_address,
        normalized_address = EXCLUDED.normalized_address,
        country_code = COALESCE(EXCLUDED.country_code, entity.entity_mention.country_code),
        region_code = COALESCE(EXCLUDED.region_code, entity.entity_mention.region_code),
        city = COALESCE(EXCLUDED.city, entity.entity_mention.city),
        geo_confidence = GREATEST(
            COALESCE(entity.entity_mention.geo_confidence, 0),
            COALESCE(EXCLUDED.geo_confidence, 0)
        ),
        source_package_id = EXCLUDED.source_package_id,
        source_internal_file = EXCLUDED.source_internal_file,
        source_start_line = EXCLUDED.source_start_line,
        entity_id = COALESCE(EXCLUDED.entity_id, entity.entity_mention.entity_id),
        match_status = CASE
            WHEN EXCLUDED.entity_id IS NOT NULL THEN EXCLUDED.match_status
            ELSE entity.entity_mention.match_status END,
        resolution_method = CASE
            WHEN EXCLUDED.entity_id IS NOT NULL THEN EXCLUDED.resolution_method
            ELSE entity.entity_mention.resolution_method END,
        last_seen_at = now()
    """
    def execute(target: Any) -> None:
        with target.cursor() as cur:
            cur.executemany(sql, rows)
    if conn is not None:
        execute(conn)
        return
    with postgres_conn() as owned_conn:
        execute(owned_conn)
        owned_conn.commit()


def upsert_identity_batch(
    entities: Iterable[EntityCandidate], mentions: list[dict[str, Any]]
) -> None:
    """Persist one identity batch atomically over a single PostgreSQL session.

    Entity rows must precede mentions because the latter can reference the former.
    Keeping both operations in one transaction preserves that ordering while avoiding
    the two connection handshakes previously paid by every ingestion buffer flush.
    """
    candidates = list(entities)
    if not candidates and not mentions:
        return
    with postgres_conn() as conn:
        upsert_entities(candidates, conn=conn)
        upsert_entity_mentions(mentions, conn=conn)
        conn.commit()


def _quality_issue_key(row: dict[str, Any]) -> str:
    material = "|".join(
        (
            str(row.get("package_id") or ""),
            str(row.get("issue_type") or ""),
            str(row.get("source_file") or ""),
            str(row.get("source_row") or ""),
            str(row.get("raw_excerpt") or "")[:500],
            str(row.get("details") or ""),
        )
    )
    return hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()


def record_quality_issues(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    sql = """
    INSERT INTO control.data_quality_issue (
        issue_key, package_id, run_id, jurisdiction, issue_type, severity,
        occurrence_count, source_file, source_row, raw_excerpt, details
    )
    VALUES (
        %(issue_key)s, %(package_id)s, %(run_id)s, 'CN', %(issue_type)s,
        %(severity)s, %(occurrence_count)s, %(source_file)s, %(source_row)s,
        %(raw_excerpt)s, %(details)s::jsonb
    )
    ON CONFLICT (issue_key)
    DO UPDATE SET
        occurrence_count = EXCLUDED.occurrence_count,
        details = EXCLUDED.details,
        severity = EXCLUDED.severity,
        updated_at = now()
    """
    payload: list[dict[str, Any]] = []
    for item in rows:
        row = dict(item)
        row.setdefault("occurrence_count", 1)
        row.setdefault("source_file", None)
        row.setdefault("source_row", None)
        row.setdefault("raw_excerpt", "")
        if not isinstance(row.get("details"), str):
            row["details"] = _json(row.get("details", {}))
        row["issue_key"] = _quality_issue_key(row)
        payload.append(row)
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, payload)
        conn.commit()


def pending_packages(
    jurisdiction: str = "CN",
    limit: int = 10,
    statuses: tuple[str, ...] = ("REGISTERED",),
) -> list[dict[str, Any]]:
    if not statuses:
        return []
    placeholders = ", ".join(["%s"] * len(statuses))
    sql = f"""
    SELECT package_id, package_sequence, jurisdiction, file_name, file_path,
           file_size, sha256, package_kind, partition_dimension,
           partition_value, source_period_start, source_period_end,
           source_sequence, source_rank, status
    FROM control.source_package
    WHERE jurisdiction = %s AND status IN ({placeholders})
    ORDER BY source_rank, package_sequence
    LIMIT %s
    """
    params = (jurisdiction, *statuses, limit)
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())
