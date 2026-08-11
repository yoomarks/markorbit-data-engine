from __future__ import annotations

import argparse
import json
from typing import Any

from app.db import clickhouse_client


PARTS_SQL = """
SELECT
    table,
    active,
    count() AS part_count,
    sum(rows) AS rows,
    sum(bytes_on_disk) AS bytes_on_disk
FROM system.parts
WHERE database = 'markorbit_facts'
GROUP BY table, active
ORDER BY bytes_on_disk DESC, table, active DESC
"""

GOODS_TRANSITIONS_SQL = """
SELECT
    transition_type,
    count() AS rows
FROM markorbit_facts.cn_goods_item_observation
GROUP BY transition_type
ORDER BY rows DESC, transition_type
"""

CN_EVENT_TYPES_SQL = """
SELECT
    event_type,
    count() AS rows
FROM markorbit_facts.cn_observed_event
GROUP BY event_type
ORDER BY rows DESC, event_type
"""

READ_ONLY_QUERIES = (PARTS_SQL, GOODS_TRANSITIONS_SQL, CN_EVENT_TYPES_SQL)


def _assert_read_only() -> None:
    for sql in READ_ONLY_QUERIES:
        normalized = sql.lstrip().upper()
        if not normalized.startswith("SELECT"):
            raise RuntimeError("storage audit contains a non-read-only query")
        for forbidden in (
            " ALTER ",
            " DELETE ",
            " DROP ",
            " INSERT ",
            " OPTIMIZE ",
            " TRUNCATE ",
            " UPDATE ",
        ):
            if forbidden in f" {normalized} ":
                raise RuntimeError(
                    f"storage audit contains forbidden mutation token: {forbidden.strip()}"
                )


def _human_bytes(value: int) -> str:
    size = float(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} TiB"


def _part_rows(client: Any) -> list[dict[str, Any]]:
    rows = client.query(PARTS_SQL).result_rows
    return [
        {
            "table": str(table),
            "active": bool(active),
            "part_count": int(part_count or 0),
            "rows": int(row_count or 0),
            "bytes_on_disk": int(bytes_on_disk or 0),
            "size": _human_bytes(int(bytes_on_disk or 0)),
        }
        for table, active, part_count, row_count, bytes_on_disk in rows
    ]


def _physical_summary(parts: list[dict[str, Any]]) -> dict[str, Any]:
    active_bytes = sum(row["bytes_on_disk"] for row in parts if row["active"])
    inactive_bytes = sum(row["bytes_on_disk"] for row in parts if not row["active"])
    active_rows = sum(row["rows"] for row in parts if row["active"])
    inactive_rows = sum(row["rows"] for row in parts if not row["active"])
    stage_bytes = sum(
        row["bytes_on_disk"]
        for row in parts
        if row["active"] and row["table"].startswith(("cn_stage_", "us_stage_"))
    )
    total_bytes = active_bytes + inactive_bytes
    return {
        "active_bytes": active_bytes,
        "active_size": _human_bytes(active_bytes),
        "inactive_bytes": inactive_bytes,
        "inactive_size": _human_bytes(inactive_bytes),
        "total_parts_bytes": total_bytes,
        "total_parts_size": _human_bytes(total_bytes),
        "active_rows": active_rows,
        "inactive_rows": inactive_rows,
        "active_stage_bytes": stage_bytes,
        "active_stage_size": _human_bytes(stage_bytes),
        "inactive_share": (inactive_bytes / total_bytes) if total_bytes else 0.0,
    }


def _grouped_counts(client: Any, sql: str, key_name: str) -> list[dict[str, Any]]:
    rows = client.query(sql).result_rows
    return [
        {key_name: str(key), "rows": int(row_count or 0)}
        for key, row_count in rows
    ]


def build_storage_audit(*, deep: bool = False, client: Any | None = None) -> dict[str, Any]:
    """Return a read-only physical/logical storage report.

    The default mode only reads ``system.parts`` and is suitable while the large
    corpus is idle or being inspected. ``deep=True`` additionally scans the CN
    observation/event tables to quantify logical history categories. It still
    performs SELECT statements only and never runs FINAL, mutation, OPTIMIZE, or
    cleanup statements.
    """
    _assert_read_only()
    client = client or clickhouse_client()
    parts = _part_rows(client)
    report: dict[str, Any] = {
        "audit_version": "DATA_ENGINE_STORAGE_V2_AUDIT_V1",
        "mode": "deep" if deep else "physical",
        "read_only": True,
        "physical": _physical_summary(parts),
        "parts": parts,
    }
    if deep:
        transitions = _grouped_counts(
            client, GOODS_TRANSITIONS_SQL, "transition_type"
        )
        event_types = _grouped_counts(client, CN_EVENT_TYPES_SQL, "event_type")
        reobserved_rows = sum(
            row["rows"]
            for row in transitions
            if row["transition_type"] == "REOBSERVED"
        )
        report["cn_goods_item_observation"] = {
            "transition_counts": transitions,
            "reobserved_rows": reobserved_rows,
            "policy": "REOBSERVED_IS_NO_OP_AND_NOT_PERSISTED_BY_STORAGE_V2",
        }
        report["cn_observed_event"] = {"event_type_counts": event_types}
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only MarkOrbit Data Engine storage audit."
    )
    parser.add_argument(
        "--deep",
        action="store_true",
        help="Also scan CN history tables by transition/event type.",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Emit compact JSON instead of indented JSON.",
    )
    args = parser.parse_args()
    report = build_storage_audit(deep=args.deep)
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=None if args.compact else 2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
