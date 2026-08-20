from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import logging
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import get_settings
from app.db import postgres_conn
from app.us_mark_image.migrations import ensure_mark_image_schema
from app.us_mark_image.planner import replenish_queue
from app.us_mark_image.processor import MAX_IMAGE_BYTES, persist_success


LOGGER = logging.getLogger("markorbit.us_mark_image")
_USER_AGENT = "MarkOrbit-Data-Engine/US-Mark-Image-V1"


def recover_interrupted_tasks(*, older_than_minutes: int = 120) -> int:
    ensure_mark_image_schema()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE acquisition.us_mark_image_coverage
                SET state = 'RETRYABLE', next_attempt_at = now(), claimed_at = NULL,
                    last_error = 'Mark-image worker restarted before task completion.',
                    updated_at = now()
                WHERE state = 'FETCHING'
                  AND claimed_at < now() - (%s * interval '1 minute')
                """,
                (int(older_than_minutes),),
            )
            count = cur.rowcount
        conn.commit()
    return int(count)


def claim_next_task() -> dict[str, object] | None:
    ensure_mark_image_schema()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT serial_number, source_url, source_rank, attempts, priority
                FROM acquisition.us_mark_image_coverage
                WHERE state = 'QUEUED'
                   OR (state IN ('RETRYABLE', 'NOT_FOUND') AND next_attempt_at <= now())
                ORDER BY priority DESC, source_rank DESC, serial_number
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            )
            row = cur.fetchone()
            if row is None:
                return None
            cur.execute(
                """
                UPDATE acquisition.us_mark_image_coverage
                SET state = 'FETCHING', attempts = attempts + 1, claimed_at = now(),
                    last_error = NULL, updated_at = now()
                WHERE serial_number = %s
                RETURNING serial_number, source_url, source_rank, attempts, priority
                """,
                (row["serial_number"],),
            )
            claimed = cur.fetchone()
        conn.commit()
    return dict(claimed) if claimed else None


def _reserve_request_slot(interval_seconds: float) -> None:
    if interval_seconds <= 0:
        return
    ensure_mark_image_schema()
    now = datetime.now(timezone.utc)
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT request_not_before
                FROM acquisition.us_mark_image_planner_state
                WHERE state_key = 'US_MARK_IMAGE'
                FOR UPDATE
                """
            )
            row = cur.fetchone()
            reserved_for = row["request_not_before"] if row else None
            if reserved_for is None or reserved_for < now:
                reserved_for = now
            cur.execute(
                """
                UPDATE acquisition.us_mark_image_planner_state
                SET request_not_before = %s, updated_at = now()
                WHERE state_key = 'US_MARK_IMAGE'
                """,
                (reserved_for + timedelta(seconds=interval_seconds),),
            )
        conn.commit()
    delay = (reserved_for - now).total_seconds()
    if delay > 0:
        time.sleep(delay)


def _download(url: str, *, timeout_seconds: int) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "image/*",
        },
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read(MAX_IMAGE_BYTES + 1)
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError(f"mark image exceeds {MAX_IMAGE_BYTES} bytes")
    return raw


def _retry_at(attempts: int, *, not_found: bool) -> datetime:
    now = datetime.now(timezone.utc)
    if not_found:
        days = (1, 7, 30, 365)[min(max(attempts - 1, 0), 3)]
        return now + timedelta(days=days)
    minutes = min(5 * (2 ** min(max(attempts - 1, 0), 8)), 24 * 60)
    return now + timedelta(minutes=minutes)


def mark_failure(
    serial_number: str,
    *,
    error: str,
    http_status: int | None = None,
    not_found: bool = False,
    retry_at: datetime | None = None,
) -> None:
    ensure_mark_image_schema()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT attempts FROM acquisition.us_mark_image_coverage WHERE serial_number = %s",
                (serial_number,),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"unknown US mark-image coverage serial: {serial_number}")
            attempts = int(row["attempts"] or 0)
            state = "NOT_FOUND" if not_found else "RETRYABLE"
            due = retry_at or _retry_at(attempts, not_found=not_found)
            cur.execute(
                """
                UPDATE acquisition.us_mark_image_coverage
                SET state = %s, next_attempt_at = %s, claimed_at = NULL,
                    last_http_status = %s, last_error = %s, updated_at = now()
                WHERE serial_number = %s
                """,
                (state, due, http_status, error[:2000], serial_number),
            )
        conn.commit()


def _retry_after(error: HTTPError) -> datetime | None:
    raw = error.headers.get("Retry-After") if error.headers else None
    if not raw:
        return None
    raw = raw.strip()
    if raw.isdigit():
        return datetime.now(timezone.utc) + timedelta(seconds=min(int(raw), 3600))
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return min(parsed, datetime.now(timezone.utc) + timedelta(hours=1))
    except (TypeError, ValueError, OverflowError):
        return None


def process_one(*, interval_seconds: float, timeout_seconds: int) -> dict[str, object] | None:
    task = claim_next_task()
    if task is None:
        return None
    serial = str(task["serial_number"])
    source_url = str(task["source_url"])
    _reserve_request_slot(interval_seconds)
    try:
        raw = _download(source_url, timeout_seconds=timeout_seconds)
        result = persist_success(
            serial,
            raw,
            source_url=source_url,
            source_rank=int(task["source_rank"] or 0),
        )
        LOGGER.info(
            "US mark image fetched serial=%s bytes=%s asset=%s",
            serial,
            result["byte_size"],
            result["asset_id"],
        )
        return {"status": "FETCHED", **result}
    except HTTPError as exc:
        not_found = exc.code in {404, 410}
        mark_failure(
            serial,
            error=f"HTTP {exc.code}: {exc.reason}",
            http_status=int(exc.code),
            not_found=not_found,
            retry_at=_retry_after(exc) if exc.code in {429, 503} else None,
        )
        LOGGER.warning("US mark image HTTP failure serial=%s status=%s", serial, exc.code)
        return {"status": "NOT_FOUND" if not_found else "RETRYABLE", "serial_number": serial}
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        mark_failure(serial, error=f"{type(exc).__name__}: {exc}")
        LOGGER.warning("US mark image failure serial=%s error=%s", serial, exc)
        return {"status": "RETRYABLE", "serial_number": serial}


def run(*, max_items: int | None = None, replenish: bool = True) -> int:
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    ensure_mark_image_schema()
    recovered = recover_interrupted_tasks()
    if recovered:
        LOGGER.warning("Recovered %s interrupted US mark-image task(s)", recovered)

    processed = 0
    while max_items is None or processed < max_items:
        if replenish:
            try:
                replenish_queue(
                    queue_floor=settings.us_mark_image_queue_floor,
                    queue_target=settings.us_mark_image_queue_target,
                    recent_lookback_days=settings.us_mark_image_recent_lookback_days,
                )
            except Exception:
                LOGGER.exception("US mark-image queue replenishment failed")

        result = process_one(
            interval_seconds=settings.us_mark_image_request_interval_seconds,
            timeout_seconds=settings.us_mark_image_http_timeout_seconds,
        )
        if result is None:
            if max_items is not None:
                break
            time.sleep(settings.us_mark_image_idle_sleep_seconds)
            continue
        processed += 1
    return processed


def main() -> None:
    parser = argparse.ArgumentParser(description="Rate-limited USPTO mark-image worker")
    parser.add_argument("--once", action="store_true", help="process at most one image")
    parser.add_argument("--max-items", type=int)
    parser.add_argument("--no-replenish", action="store_true")
    args = parser.parse_args()
    max_items = 1 if args.once else args.max_items
    run(max_items=max_items, replenish=not args.no_replenish)


if __name__ == "__main__":
    main()
