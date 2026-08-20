from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
from typing import Iterable

from app.db import clickhouse_client, postgres_conn
from app.us_mark_image import DEFAULT_QUEUE_FLOOR, DEFAULT_QUEUE_TARGET
from app.us_mark_image.migrations import ensure_mark_image_schema


@dataclass(frozen=True)
class MarkImageCandidate:
    serial_number: str
    source_rank: int
    filing_date: date | None
    mark_identification: str
    mark_drawing_code: str
    standard_character_claimed: bool

    @property
    def source_url(self) -> str:
        return f"https://tsdr.uspto.gov/img/{self.serial_number}/large"

    @property
    def source_mark_fingerprint(self) -> str:
        payload = {
            "mark_identification": self.mark_identification.strip(),
            "mark_drawing_code": self.mark_drawing_code.strip(),
            "standard_character_claimed": self.standard_character_claimed,
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize(value: object) -> object:
    if isinstance(value, bytes):
        return value.decode("utf-8").rstrip("\x00")
    if isinstance(value, bytearray):
        return bytes(value).decode("utf-8").rstrip("\x00")
    if isinstance(value, memoryview):
        return value.tobytes().decode("utf-8").rstrip("\x00")
    return value


def _rows(sql: str) -> list[dict[str, object]]:
    result = clickhouse_client().query(sql)
    return [
        {name: _normalize(value) for name, value in zip(result.column_names, row, strict=True)}
        for row in result.result_rows
    ]


def _candidate(row: dict[str, object]) -> MarkImageCandidate:
    serial = str(row.get("serial_number") or "").strip()
    if not serial.isdigit() or len(serial) != 8:
        raise ValueError(f"invalid US serial_number from source: {serial!r}")
    filing_date = row.get("filing_date")
    if filing_date is not None and not isinstance(filing_date, date):
        filing_date = None
    return MarkImageCandidate(
        serial_number=serial,
        source_rank=int(row.get("source_rank") or 0),
        filing_date=filing_date,
        mark_identification=str(row.get("mark_identification") or ""),
        mark_drawing_code=str(row.get("mark_drawing_code") or ""),
        standard_character_claimed=bool(int(row.get("standard_character_claimed") or 0)),
    )


def load_recent_candidates(*, limit: int = 100_000, lookback_days: int = 14) -> list[MarkImageCandidate]:
    if limit < 1 or lookback_days < 1:
        return []
    rows = _rows(
        f"""
        SELECT serial_number, source_rank, filing_date, mark_identification,
               mark_drawing_code, standard_character_claimed
        FROM markorbit_facts.us_case_current FINAL
        WHERE is_deleted = 0
          AND filing_date >= today() - INTERVAL {int(lookback_days)} DAY
        ORDER BY source_rank DESC, serial_number
        LIMIT {int(limit)}
        """
    )
    return [_candidate(row) for row in rows]


def _backfill_cursor() -> str:
    ensure_mark_image_schema()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT backfill_serial_cursor FROM acquisition.us_mark_image_planner_state "
                "WHERE state_key = 'US_MARK_IMAGE'"
            )
            row = cur.fetchone()
    return str(row["backfill_serial_cursor"] or "") if row else ""


def load_backfill_candidates(*, limit: int = 300_000) -> tuple[list[MarkImageCandidate], str]:
    if limit < 1:
        return [], _backfill_cursor()
    cursor = _backfill_cursor()
    safe_cursor = cursor if cursor.isdigit() and len(cursor) == 8 else ""
    rows = _rows(
        f"""
        SELECT serial_number, source_rank, filing_date, mark_identification,
               mark_drawing_code, standard_character_claimed
        FROM markorbit_facts.us_case_current FINAL
        WHERE is_deleted = 0 AND serial_number > '{safe_cursor}'
        ORDER BY serial_number
        LIMIT {int(limit)}
        """
    )
    if not rows:
        return [], ""
    candidates = [_candidate(row) for row in rows]
    return candidates, candidates[-1].serial_number


def _upsert_candidates(
    candidates: Iterable[MarkImageCandidate],
    *,
    priority: int,
    reason_code: str,
) -> dict[str, int]:
    ensure_mark_image_schema()
    rows = []
    not_applicable = 0
    queued = 0
    for item in candidates:
        state = "NOT_APPLICABLE" if item.standard_character_claimed else "QUEUED"
        not_applicable += int(state == "NOT_APPLICABLE")
        queued += int(state == "QUEUED")
        rows.append(
            (
                item.serial_number,
                item.source_url,
                item.source_rank,
                item.source_mark_fingerprint,
                item.standard_character_claimed,
                state,
                priority,
                [reason_code],
            )
        )
    if not rows:
        return {"observed": 0, "queued": 0, "not_applicable": 0}

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            for offset in range(0, len(rows), 5_000):
                cur.executemany(
                    """
                    INSERT INTO acquisition.us_mark_image_coverage (
                        serial_number, source_url, source_rank, source_mark_fingerprint,
                        standard_character_claimed, state, priority, reason_codes
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (serial_number) DO UPDATE SET
                        source_url = EXCLUDED.source_url,
                        source_rank = GREATEST(
                            acquisition.us_mark_image_coverage.source_rank,
                            EXCLUDED.source_rank
                        ),
                        standard_character_claimed = EXCLUDED.standard_character_claimed,
                        state = CASE
                            WHEN EXCLUDED.standard_character_claimed THEN 'NOT_APPLICABLE'
                            WHEN acquisition.us_mark_image_coverage.standard_character_claimed
                                THEN 'QUEUED'
                            WHEN acquisition.us_mark_image_coverage.source_mark_fingerprint
                                 IS DISTINCT FROM EXCLUDED.source_mark_fingerprint
                                THEN 'QUEUED'
                            ELSE acquisition.us_mark_image_coverage.state
                        END,
                        priority = CASE
                            WHEN EXCLUDED.priority > acquisition.us_mark_image_coverage.priority
                                THEN EXCLUDED.priority
                            ELSE acquisition.us_mark_image_coverage.priority
                        END,
                        reason_codes = CASE
                            WHEN EXCLUDED.priority > acquisition.us_mark_image_coverage.priority
                                THEN EXCLUDED.reason_codes
                            ELSE acquisition.us_mark_image_coverage.reason_codes
                        END,
                        attempts = CASE
                            WHEN acquisition.us_mark_image_coverage.source_mark_fingerprint
                                 IS DISTINCT FROM EXCLUDED.source_mark_fingerprint
                                THEN 0
                            ELSE acquisition.us_mark_image_coverage.attempts
                        END,
                        next_attempt_at = CASE
                            WHEN acquisition.us_mark_image_coverage.source_mark_fingerprint
                                 IS DISTINCT FROM EXCLUDED.source_mark_fingerprint
                                THEN NULL
                            ELSE acquisition.us_mark_image_coverage.next_attempt_at
                        END,
                        source_mark_fingerprint = EXCLUDED.source_mark_fingerprint,
                        updated_at = now()
                    """,
                    rows[offset : offset + 5_000],
                )
        conn.commit()
    return {"observed": len(rows), "queued": queued, "not_applicable": not_applicable}


def pending_count() -> int:
    ensure_mark_image_schema()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) AS n
                FROM acquisition.us_mark_image_coverage
                WHERE state = 'QUEUED'
                   OR (state IN ('RETRYABLE', 'NOT_FOUND') AND next_attempt_at <= now())
                """
            )
            row = cur.fetchone()
    return int(row["n"] or 0) if row else 0


def replenish_queue(
    *,
    queue_floor: int = DEFAULT_QUEUE_FLOOR,
    queue_target: int = DEFAULT_QUEUE_TARGET,
    recent_lookback_days: int = 14,
) -> dict[str, object]:
    if queue_floor < 0 or queue_target < 1 or queue_floor > queue_target:
        raise ValueError("invalid mark-image queue floor/target")
    before = pending_count()
    if before >= queue_floor:
        return {"status": "ENOUGH_PENDING", "pending_before": before, "pending_after": before}

    recent = load_recent_candidates(limit=min(queue_target, 100_000), lookback_days=recent_lookback_days)
    recent_result = _upsert_candidates(recent, priority=1_000_000, reason_code="RECENT_APPLICATION")

    needed = max(queue_target - pending_count(), 0)
    scan_limit = min(max(needed * 3, 100_000), 600_000) if needed else 0
    backfill_result = {"observed": 0, "queued": 0, "not_applicable": 0}
    cursor_to = _backfill_cursor()
    if scan_limit:
        backfill, cursor_to = load_backfill_candidates(limit=scan_limit)
        backfill_result = _upsert_candidates(
            backfill,
            priority=100_000,
            reason_code="HISTORICAL_BACKFILL",
        )
        with postgres_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE acquisition.us_mark_image_planner_state
                    SET backfill_serial_cursor = %s, updated_at = now()
                    WHERE state_key = 'US_MARK_IMAGE'
                    """,
                    (cursor_to,),
                )
            conn.commit()

    after = pending_count()
    return {
        "status": "REPLENISHED",
        "pending_before": before,
        "pending_after": after,
        "recent": recent_result,
        "backfill": backfill_result,
        "backfill_serial_cursor": cursor_to,
    }
