from __future__ import annotations

import json
from typing import Any

from app.db import clickhouse_client


DIAGNOSTIC_VERSION = "CN_CLICKHOUSE_FAILURE_DIAGNOSTIC_V1"


def recent_clickhouse_failures(limit: int = 3) -> dict[str, Any]:
    """Read recent failed ClickHouse queries without mutating application data."""
    safe_limit = max(1, min(int(limit), 20))
    client = clickhouse_client()
    client.command("SYSTEM FLUSH LOGS")
    rows = client.query(
        f"""
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
          AND database = 'markorbit_facts'
          AND query NOT LIKE '%system.query_log%'
        ORDER BY event_time_microseconds DESC
        LIMIT {safe_limit}
        """
    ).result_rows

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
        "failure_count": len(failures),
        "failures": failures,
    }


def main() -> None:
    print(json.dumps(recent_clickhouse_failures(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
