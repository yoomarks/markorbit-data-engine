from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from typing import Any

from app.cn import replay_readiness as core
from app.db import postgres_conn
from app.version import engine_version


def _row_to_dict(
    row: Mapping[str, Any] | Sequence[Any] | None,
    columns: tuple[str, ...],
) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, Mapping):
        return {column: row.get(column) for column in columns}
    return {column: value for column, value in zip(columns, row)}


def _status_counts(rows: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        if isinstance(row, Mapping):
            status = row.get("status")
            count = row.get("count")
            if count is None:
                count = row.get("package_count")
        else:
            status, count = row
        counts[str(status)] = int(count or 0)
    return counts


def _package_state() -> dict[str, Any]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(core._PACKAGE_STATUS_SQL)
            status_counts = _status_counts(cur.fetchall())

            cur.execute(core._NEXT_PENDING_SQL)
            next_pending = _row_to_dict(
                cur.fetchone(),
                (
                    "package_id",
                    "file_name",
                    "package_kind",
                    "partition_value",
                    "source_rank",
                    "status",
                ),
            )

            cur.execute(core._NEXT_RETRY_SQL)
            next_retry = _row_to_dict(
                cur.fetchone(),
                (
                    "package_id",
                    "file_name",
                    "package_kind",
                    "partition_value",
                    "source_rank",
                    "status",
                    "error_message",
                ),
            )

            cur.execute(core._LATEST_SUCCESS_SQL)
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
        "next_pending": next_pending,
        "next_retry": next_retry,
        "latest_success": latest_success,
    }


def build_readiness(*, persistent_worker_running: bool = False) -> dict[str, Any]:
    return core.evaluate_readiness(
        package_state=_package_state(),
        storage_state=core._storage_state(),
        persistent_worker_running=persistent_worker_running,
        current_engine_version=engine_version(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only CN M1.6 replay readiness and resume diagnostic"
    )
    parser.add_argument(
        "--persistent-worker-running",
        action="store_true",
        help="Report that the persistent docker compose worker is currently running.",
    )
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    report = build_readiness(persistent_worker_running=args.persistent_worker_running)
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=None if args.compact else 2,
            default=str,
        )
    )
    return 0 if report["status"] in {"READY", "COMPLETE", "RETRY_REQUIRED"} else 4


if __name__ == "__main__":
    raise SystemExit(main())
