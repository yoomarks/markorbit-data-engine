from __future__ import annotations

import argparse
import json
from typing import Any, Iterable

from app.db import clickhouse_client


HEADROOM_VERSION = "DATA_ENGINE_STORAGE_HEADROOM_V1"
GIB = 1024**3
DEFAULT_MIN_FREE_GIB = 128.0
DEFAULT_MIN_FREE_PERCENT = 10.0
DEFAULT_RESERVE_GIB = 32.0


def evaluate_disk_headroom(
    *,
    disks: Iterable[dict[str, Any]],
    minimum_free_gib: float = DEFAULT_MIN_FREE_GIB,
    minimum_free_percent: float = DEFAULT_MIN_FREE_PERCENT,
    reserve_gib: float = DEFAULT_RESERVE_GIB,
) -> dict[str, Any]:
    rows = [dict(row) for row in disks]
    if minimum_free_gib < 0 or minimum_free_percent < 0 or reserve_gib < 0:
        raise ValueError("headroom policy values must be non-negative")

    selected = next((row for row in rows if str(row.get("name")) == "default"), None)
    if selected is None and rows:
        selected = rows[0]
    if selected is None:
        return {
            "headroom_version": HEADROOM_VERSION,
            "status": "BLOCKED",
            "safe_to_mutate": False,
            "reason_codes": ["clickhouse_disk_state_missing"],
            "policy": {
                "minimum_free_gib": minimum_free_gib,
                "minimum_free_percent": minimum_free_percent,
                "reserve_gib": reserve_gib,
            },
            "disk": None,
        }

    free_bytes = int(selected.get("free_space") or 0)
    total_bytes = int(selected.get("total_space") or 0)
    absolute_required = int(minimum_free_gib * GIB)
    percent_required = int(total_bytes * minimum_free_percent / 100.0)
    reserve_bytes = int(reserve_gib * GIB)
    required_free_bytes = max(absolute_required, percent_required) + reserve_bytes
    free_percent = (free_bytes / total_bytes * 100.0) if total_bytes > 0 else 0.0
    safe = total_bytes > 0 and free_bytes >= required_free_bytes

    reasons: list[str] = []
    if total_bytes <= 0:
        reasons.append("clickhouse_disk_total_space_invalid")
    if free_bytes < required_free_bytes:
        reasons.append("clickhouse_free_space_below_policy")

    return {
        "headroom_version": HEADROOM_VERSION,
        "status": "PASS" if safe else "BLOCKED",
        "safe_to_mutate": safe,
        "reason_codes": reasons,
        "policy": {
            "minimum_free_gib": minimum_free_gib,
            "minimum_free_percent": minimum_free_percent,
            "reserve_gib": reserve_gib,
            "required_free_bytes": required_free_bytes,
        },
        "disk": {
            "name": str(selected.get("name") or ""),
            "path": str(selected.get("path") or ""),
            "free_space": free_bytes,
            "total_space": total_bytes,
            "free_percent": free_percent,
        },
    }


def read_clickhouse_disks() -> list[dict[str, Any]]:
    rows = clickhouse_client().query(
        "SELECT name, path, free_space, total_space FROM system.disks ORDER BY name"
    ).result_rows
    return [
        {
            "name": str(name),
            "path": str(path),
            "free_space": int(free_space or 0),
            "total_space": int(total_space or 0),
        }
        for name, path, free_space, total_space in rows
    ]


def build_headroom_report(
    *,
    minimum_free_gib: float = DEFAULT_MIN_FREE_GIB,
    minimum_free_percent: float = DEFAULT_MIN_FREE_PERCENT,
    reserve_gib: float = DEFAULT_RESERVE_GIB,
) -> dict[str, Any]:
    report = evaluate_disk_headroom(
        disks=read_clickhouse_disks(),
        minimum_free_gib=minimum_free_gib,
        minimum_free_percent=minimum_free_percent,
        reserve_gib=reserve_gib,
    )
    report["read_only"] = True
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only ClickHouse storage headroom gate")
    parser.add_argument("--minimum-free-gib", type=float, default=DEFAULT_MIN_FREE_GIB)
    parser.add_argument("--minimum-free-percent", type=float, default=DEFAULT_MIN_FREE_PERCENT)
    parser.add_argument("--reserve-gib", type=float, default=DEFAULT_RESERVE_GIB)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    report = build_headroom_report(
        minimum_free_gib=args.minimum_free_gib,
        minimum_free_percent=args.minimum_free_percent,
        reserve_gib=args.reserve_gib,
    )
    print(json.dumps(report, ensure_ascii=False, indent=None if args.compact else 2))
    return 0 if report["safe_to_mutate"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
