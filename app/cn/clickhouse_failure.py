from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from typing import Any

from app.db import clickhouse_client


DIAGNOSTIC_VERSION = "CN_CLICKHOUSE_FAILURE_DIAGNOSTIC_V3_RUN_SCOPED"


def _normalize_since_utc(value: str | None) -> tuple[int | None, str | None]:
    if value is None or not value.strip():
        return None, None

    raw = value.strip()
    candidate = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(
            "since_utc must be an ISO-8601 timestamp with an explicit timezone"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("since_utc must include an explicit timezone")

    normalized = parsed.astimezone(timezone.utc)
    epoch_seconds = int(normalized.timestamp())
    normalized_text = normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return epoch_seconds, normalized_text


def _query_log_sql(limit: int, since_epoch_seconds: int | None = None) -> str:
    safe_limit = max(1, min(int(limit), 20))
    since_clause = ""
    if since_epoch_seconds is not None:
        since_clause = (
            "\n          AND event_time >= "
            f"toDateTime({int(since_epoch_seconds)}, 'UTC')"
        )
    return f"""
        SELECT
            event_time,
            query_duration_ms,
            read_rows,
            read_bytes,
            written_rows,
            written_bytes,
            memory_usage,
            exception_code,
            exception,
            query
        FROM system.query_log
        WHERE type = 'ExceptionWhileProcessing'
          AND exception_code != 0
          AND has(databases, 'markorbit_facts')
          AND query NOT LIKE '%system.query_log%'{since_clause}
        ORDER BY event_time_microseconds DESC
        LIMIT {safe_limit}
        """


def recent_clickhouse_failures(
    limit: int = 3,
    *,
    since_utc: str | None = None,
) -> dict[str, Any]:
    """Read failed ClickHouse queries, optionally scoped to one replay window."""
    since_epoch_seconds, normalized_since_utc = _normalize_since_utc(since_utc)
    client = clickhouse_client()
    client.command("SYSTEM FLUSH LOGS")
    rows = client.query(_query_log_sql(limit, since_epoch_seconds)).result_rows

    failures = []
    for row in rows:
        failures.append(
            {
                "event_time": str(row[0]),
                "query_duration_ms": int(row[1] or 0),
                "read_rows": int(row[2] or 0),
                "read_bytes": int(row[3] or 0),
                "written_rows": int(row[4] or 0),
                "written_bytes": int(row[5] or 0),
                "memory_usage": int(row[6] or 0),
                "exception_code": int(row[7] or 0),
                "exception": str(row[8] or ""),
                "query": str(row[9] or ""),
            }
        )

    return {
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "since_utc": normalized_since_utc,
        "failure_count": len(failures),
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read recent CN-related ClickHouse query failures without changing facts."
    )
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument(
        "--since-utc",
        default=None,
        help="ISO-8601 timestamp; only failures at or after this instant are returned.",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            recent_clickhouse_failures(args.limit, since_utc=args.since_utc),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
