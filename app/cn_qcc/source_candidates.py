from __future__ import annotations

from dataclasses import dataclass
import hashlib
import uuid

from app.cn_qcc.policy import QccCandidate, has_company_name_signal
from app.db import clickhouse_client, postgres_conn


@dataclass(frozen=True)
class CandidatePool:
    candidates: list[QccCandidate]
    source_watermark_to: tuple[int, str]
    backfill_bucket: int
    backfill_entity_watermark_to: str
    backfill_bucket_exhausted: bool
    lane_counts: dict[str, int]


def _normalize(value: object) -> object:
    if isinstance(value, bytes):
        return value.decode("utf-8").rstrip("\x00")
    if isinstance(value, bytearray):
        return bytes(value).decode("utf-8").rstrip("\x00")
    if isinstance(value, memoryview):
        return value.tobytes().decode("utf-8").rstrip("\x00")
    return value


def _rows(client, sql: str) -> list[dict[str, object]]:
    result = client.query(sql)
    return [
        {name: _normalize(value) for name, value in zip(result.column_names, row, strict=True)}
        for row in result.result_rows
    ]


def _entity_id(value: object) -> str:
    return str(uuid.UUID(str(value)))


def _uuid_in_clause(entity_ids: list[str]) -> str:
    if not entity_ids:
        return "('00000000-0000-0000-0000-000000000000')"
    return "(" + ",".join(f"'{_entity_id(item)}'" for item in entity_ids) + ")"


def _load_official_details(client, entity_ids: list[str]) -> dict[str, dict[str, object]]:
    output: dict[str, dict[str, object]] = {}
    for offset in range(0, len(entity_ids), 5000):
        chunk = entity_ids[offset : offset + 5000]
        clause = _uuid_in_clause(chunk)
        for row in _rows(
            client,
            f"""
            SELECT
                toString(assumeNotNull(entity_id)) AS entity_id,
                argMax(raw_name, source_rank) AS applicant_name,
                argMax(normalized_name, source_rank) AS normalized_name,
                argMax(raw_address, source_rank) AS applicant_address,
                argMax(country_code, source_rank) AS country_code,
                argMax(region_code, source_rank) AS region_code,
                argMax(city, source_rank) AS city,
                countDistinct(application_number) AS trademark_count,
                argMax(application_number, source_rank) AS latest_application_number,
                max(source_rank) AS source_rank
            FROM markorbit_facts.cn_case_party_current FINAL
            WHERE is_deleted = 0
              AND is_current = 1
              AND role IN ('OWNER', 'CO_OWNER', 'APPLICANT')
              AND isNotNull(entity_id)
              AND toString(assumeNotNull(entity_id)) IN {clause}
            GROUP BY entity_id
            """,
        ):
            output[_entity_id(row["entity_id"])] = row
    return output


def _coverage_rows(entity_ids: list[str]) -> dict[str, dict[str, object]]:
    if not entity_ids:
        return {}
    output: dict[str, dict[str, object]] = {}
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            for offset in range(0, len(entity_ids), 5000):
                chunk = entity_ids[offset : offset + 5000]
                cur.execute(
                    """
                    SELECT entity_id, source_fingerprint, last_result_status,
                           refresh_due_at, successful_fetch_count
                    FROM acquisition.cn_qcc_company_coverage
                    WHERE entity_id = ANY(%s::uuid[])
                    """,
                    (chunk,),
                )
                for row in cur.fetchall():
                    output[str(row["entity_id"])] = row
    return output


def _entity_rows(entity_ids: list[str]) -> dict[str, dict[str, object]]:
    if not entity_ids:
        return {}
    output: dict[str, dict[str, object]] = {}
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            for offset in range(0, len(entity_ids), 5000):
                chunk = entity_ids[offset : offset + 5000]
                cur.execute(
                    """
                    SELECT entity_id, entity_type, canonical_name, normalized_name,
                           normalized_address, country_code, region_code, city
                    FROM entity.entity
                    WHERE entity_id = ANY(%s::uuid[])
                    """,
                    (chunk,),
                )
                for row in cur.fetchall():
                    output[str(row["entity_id"])] = row
    return output


def _due_entity_ids(limit: int) -> list[str]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT entity_id
                FROM acquisition.cn_qcc_company_coverage
                WHERE last_result_status IN ('FAILED', 'UNATTEMPTED')
                   OR (refresh_due_at IS NOT NULL AND refresh_due_at <= now())
                ORDER BY
                    CASE WHEN last_result_status IN ('FAILED', 'UNATTEMPTED') THEN 0 ELSE 1 END,
                    refresh_due_at NULLS FIRST,
                    entity_id
                LIMIT %s
                """,
                (limit,),
            )
            return [str(row["entity_id"]) for row in cur.fetchall()]


def _candidate_from_rows(
    *,
    official: dict[str, object],
    entity: dict[str, object],
    coverage: dict[str, object] | None,
    lane_reason: str,
) -> QccCandidate | None:
    entity_id = _entity_id(official["entity_id"])
    applicant_name = str(official.get("applicant_name") or entity.get("canonical_name") or "").strip()
    normalized_name = str(official.get("normalized_name") or entity.get("normalized_name") or "").strip()
    applicant_address = str(official.get("applicant_address") or entity.get("normalized_address") or "").strip()
    country_code = str(official.get("country_code") or entity.get("country_code") or "").strip().upper()
    region_code = str(official.get("region_code") or entity.get("region_code") or "").strip()
    city = str(official.get("city") or entity.get("city") or "").strip()

    # Current CN party entities are intentionally typed broadly as TRADEMARK_PARTY.
    # QCC acquisition is therefore gated by a deterministic company-form signal
    # in the applicant name rather than pretending the Entity Hub knows person vs company.
    if not has_company_name_signal(applicant_name):
        return None

    source_fingerprint = hashlib.sha256(
        f"{normalized_name}|{applicant_address}".encode("utf-8", errors="replace")
    ).hexdigest()
    coverage = coverage or {}
    return QccCandidate(
        entity_id=entity_id,
        applicant_name=applicant_name,
        normalized_name=normalized_name,
        applicant_address=applicant_address,
        country_code=country_code,
        region_code=region_code,
        city=city,
        trademark_count=int(official.get("trademark_count") or 0),
        latest_application_number=str(official.get("latest_application_number") or "").strip(),
        source_rank=int(official.get("source_rank") or 0),
        source_fingerprint=source_fingerprint,
        lane_reason=lane_reason,
        last_result_status=str(coverage.get("last_result_status") or "NEVER_FETCHED"),
        last_source_fingerprint=str(coverage.get("source_fingerprint") or ""),
        refresh_due_at=coverage.get("refresh_due_at"),
    )


def _bounded_backfill_rows(
    rows: list[dict[str, object]],
    *,
    scan_limit: int,
    current_watermark: str,
) -> tuple[list[dict[str, object]], str, bool]:
    """Return one bounded backfill page plus durable cursor/exhaustion state."""
    selected = rows[:scan_limit]
    watermark_to = current_watermark
    if selected:
        watermark_to = _entity_id(selected[-1]["entity_id"])
    exhausted = len(rows) <= scan_limit
    return selected, watermark_to, exhausted


def load_candidate_pool(
    *,
    source_watermark: tuple[int, str],
    capacity: int,
    backfill_bucket: int,
    backfill_entity_watermark: str = "",
) -> CandidatePool:
    if capacity <= 0:
        raise ValueError("capacity must be positive")
    rank, entity_watermark = source_watermark
    try:
        entity_watermark = _entity_id(entity_watermark) if entity_watermark else ""
    except ValueError:
        entity_watermark = ""
    try:
        backfill_entity_watermark = (
            _entity_id(backfill_entity_watermark) if backfill_entity_watermark else ""
        )
    except ValueError:
        backfill_entity_watermark = ""

    client = clickhouse_client()
    scan_limit = min(max(capacity * 3, 5000), 200_000)

    changed_rows = _rows(
        client,
        f"""
        SELECT toString(assumeNotNull(entity_id)) AS entity_id,
               max(source_rank) AS latest_source_rank
        FROM markorbit_facts.cn_case_party_current FINAL
        WHERE is_deleted = 0
          AND is_current = 1
          AND role IN ('OWNER', 'CO_OWNER', 'APPLICANT')
          AND isNotNull(entity_id)
          AND (source_rank > {int(rank)}
               OR (source_rank = {int(rank)}
                   AND toString(assumeNotNull(entity_id)) > '{entity_watermark}'))
        GROUP BY entity_id
        ORDER BY latest_source_rank, entity_id
        LIMIT {scan_limit}
        """,
    )
    changed_ids = [_entity_id(row["entity_id"]) for row in changed_rows]
    if changed_rows:
        last = changed_rows[-1]
        watermark_to = (int(last.get("latest_source_rank") or rank), _entity_id(last["entity_id"]))
    else:
        watermark_to = (int(rank), entity_watermark)

    due_ids = _due_entity_ids(scan_limit)

    bucket = int(backfill_bucket) % 52
    backfill_query_rows = _rows(
        client,
        f"""
        SELECT toString(assumeNotNull(entity_id)) AS entity_id
        FROM markorbit_facts.cn_case_party_current FINAL
        WHERE is_deleted = 0
          AND is_current = 1
          AND role IN ('OWNER', 'CO_OWNER', 'APPLICANT')
          AND isNotNull(entity_id)
          AND cityHash64(toString(assumeNotNull(entity_id))) % 52 = {bucket}
          AND toString(assumeNotNull(entity_id)) > '{backfill_entity_watermark}'
        GROUP BY entity_id
        ORDER BY entity_id
        LIMIT {scan_limit + 1}
        """,
    )
    backfill_rows, backfill_watermark_to, backfill_exhausted = _bounded_backfill_rows(
        backfill_query_rows,
        scan_limit=scan_limit,
        current_watermark=backfill_entity_watermark,
    )
    backfill_ids = [_entity_id(row["entity_id"]) for row in backfill_rows]

    lane_by_entity: dict[str, str] = {}
    ordered: list[str] = []
    for lane, values in (
        ("RECENT_SOURCE_CHANGE", changed_ids),
        ("REFRESH_OR_RETRY", due_ids),
        ("HISTORICAL_BACKFILL", backfill_ids),
    ):
        for entity_id in values:
            if entity_id not in lane_by_entity:
                lane_by_entity[entity_id] = lane
                ordered.append(entity_id)

    official_rows = _load_official_details(client, ordered)
    entities = _entity_rows(ordered)
    coverage = _coverage_rows(ordered)
    candidates: list[QccCandidate] = []
    for entity_id in ordered:
        official = official_rows.get(entity_id)
        entity = entities.get(entity_id)
        if not official or not entity:
            continue
        item = _candidate_from_rows(
            official=official,
            entity=entity,
            coverage=coverage.get(entity_id),
            lane_reason=lane_by_entity[entity_id],
        )
        if item is not None:
            candidates.append(item)

    return CandidatePool(
        candidates=candidates,
        source_watermark_to=watermark_to,
        backfill_bucket=bucket,
        backfill_entity_watermark_to=backfill_watermark_to,
        backfill_bucket_exhausted=backfill_exhausted,
        lane_counts={
            "recent_source_change": len(changed_ids),
            "refresh_or_retry": len(due_ids),
            "historical_bucket": len(backfill_ids),
            "historical_bucket_exhausted": int(backfill_exhausted),
            "deduplicated_entities": len(ordered),
            "company_candidates": len(candidates),
        },
    )


__all__ = ["CandidatePool", "_bounded_backfill_rows", "load_candidate_pool"]
