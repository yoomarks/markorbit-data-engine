from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from typing import Any, Mapping, Sequence

from app.component_versions import component_versions
from app.db import clickhouse_client, postgres_conn


TELEMETRY_VERSION = "DATA_ENGINE_REPLAY_TELEMETRY_V1"
VALID_JURISDICTIONS = {"CN", "US", "US_ASSIGNMENT", "US_TTAB"}

_PACKAGE_COUNTS_SQL = """
SELECT status, count(*) AS package_count
FROM control.source_package
WHERE jurisdiction = %s
GROUP BY status
ORDER BY status
"""

_LATEST_SUCCESS_SQL = """
SELECT package_id, file_name, package_kind, partition_value, source_rank, processed_at
FROM control.source_package
WHERE jurisdiction = %s
  AND status = 'SUCCESS'
ORDER BY processed_at DESC NULLS LAST, package_sequence DESC
LIMIT 1
"""

_PARTS_SQL = """
SELECT
    sumIf(bytes_on_disk, active) AS active_bytes,
    sumIf(rows, active) AS active_rows,
    sumIf(bytes_on_disk, active AND (table LIKE 'cn_stage_%' OR table LIKE 'us_stage_%')) AS active_stage_bytes,
    sumIf(rows, active AND (table LIKE 'cn_stage_%' OR table LIKE 'us_stage_%')) AS active_stage_rows
FROM system.parts
WHERE database = 'markorbit_facts'
"""

_DISKS_SQL = """
SELECT name, path, free_space, total_space
FROM system.disks
ORDER BY name
"""


def _row_to_dict(
    row: Mapping[str, Any] | Sequence[Any] | None,
    columns: tuple[str, ...],
) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, Mapping):
        return {column: row.get(column) for column in columns}
    return {column: value for column, value in zip(columns, row, strict=False)}


def _package_state(jurisdiction: str) -> dict[str, Any]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_PACKAGE_COUNTS_SQL, (jurisdiction,))
            status_counts: dict[str, int] = {}
            for row in cur.fetchall():
                if isinstance(row, Mapping):
                    status = row.get("status")
                    count = row.get("package_count")
                else:
                    status, count = row
                status_counts[str(status)] = int(count or 0)

            cur.execute(_LATEST_SUCCESS_SQL, (jurisdiction,))
            latest_success = _row_to_dict(
                cur.fetchone(),
                (
                    "package_id",
                    "file_name",
                    "package_kind",
                    "partition_value",
                    "source_rank",
                    "processed_at",
                ),
            )

    return {
        "status_counts": status_counts,
        "registered_package_count": sum(status_counts.values()),
        "latest_success": latest_success,
    }


def _clickhouse_state() -> dict[str, Any]:
    client = clickhouse_client()
    active_bytes, active_rows, stage_bytes, stage_rows = client.query(
        _PARTS_SQL
    ).result_rows[0]
    disks = [
        {
            "name": str(name),
            "path": str(path),
            "free_space": int(free_space or 0),
            "total_space": int(total_space or 0),
        }
        for name, path, free_space, total_space in client.query(_DISKS_SQL).result_rows
    ]
    return {
        "active_bytes": int(active_bytes or 0),
        "active_rows": int(active_rows or 0),
        "active_stage_bytes": int(stage_bytes or 0),
        "active_stage_rows": int(stage_rows or 0),
        "disks": disks,
    }


def calculate_runtime_delta(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> dict[str, Any]:
    before_ch = before.get("clickhouse") or {}
    after_ch = after.get("clickhouse") or {}
    before_packages = (before.get("packages") or {}).get("status_counts") or {}
    after_packages = (after.get("packages") or {}).get("status_counts") or {}
    statuses = sorted(set(before_packages) | set(after_packages))
    return {
        "clickhouse_active_bytes": int(after_ch.get("active_bytes") or 0)
        - int(before_ch.get("active_bytes") or 0),
        "clickhouse_active_rows": int(after_ch.get("active_rows") or 0)
        - int(before_ch.get("active_rows") or 0),
        "clickhouse_stage_bytes": int(after_ch.get("active_stage_bytes") or 0)
        - int(before_ch.get("active_stage_bytes") or 0),
        "package_status_counts": {
            status: int(after_packages.get(status) or 0)
            - int(before_packages.get(status) or 0)
            for status in statuses
        },
    }


def build_snapshot(jurisdiction: str) -> dict[str, Any]:
    jurisdiction = jurisdiction.strip().upper()
    if jurisdiction not in VALID_JURISDICTIONS:
        raise ValueError(
            f"jurisdiction must be one of {sorted(VALID_JURISDICTIONS)}"
        )
    return {
        "telemetry_version": TELEMETRY_VERSION,
        "read_only": True,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "jurisdiction": jurisdiction,
        "component_versions": component_versions(),
        "packages": _package_state(jurisdiction),
        "clickhouse": _clickhouse_state(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only replay telemetry snapshot for Data Engine corpus runs"
    )
    parser.add_argument("--jurisdiction", required=True, choices=sorted(VALID_JURISDICTIONS))
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    report = build_snapshot(args.jurisdiction)
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=None if args.compact else 2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
