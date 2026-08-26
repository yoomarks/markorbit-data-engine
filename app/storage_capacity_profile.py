from __future__ import annotations

import argparse
import json
from collections import defaultdict
from typing import Any, Iterable

from app.storage_audit import build_storage_audit


PROFILE_VERSION = "DATA_ENGINE_STORAGE_CAPACITY_PROFILE_V1"


def _family_for_table(table: str) -> str:
    if table.startswith(("cn_stage_", "us_stage_")):
        return "stage"
    if table.startswith("cn_goods_"):
        return "cn_goods"
    if table == "cn_observed_event" or table.startswith("cn_event_"):
        return "cn_events"
    if table.startswith("cn_case_party"):
        return "cn_party"
    if table.startswith(
        (
            "cn_case_current",
            "cn_case_scope_",
            "cn_case_relation_",
            "cn_scope_carve_out_",
        )
    ):
        return "cn_case_core"
    if table.startswith(("cn_agent_", "cn_madrid_", "cn_priority_")):
        return "cn_reference"
    if table.startswith("cn_"):
        return "cn_other"
    if table.startswith("us_"):
        return "us"
    return "other"


def build_capacity_profile(parts: Iterable[dict[str, Any]]) -> dict[str, Any]:
    active = [dict(row) for row in parts if bool(row.get("active"))]
    total_bytes = sum(int(row.get("bytes_on_disk") or 0) for row in active)
    total_rows = sum(int(row.get("rows") or 0) for row in active)

    family_accumulator: dict[str, dict[str, int]] = defaultdict(
        lambda: {"bytes_on_disk": 0, "rows": 0, "table_count": 0}
    )
    table_rows: list[dict[str, Any]] = []
    for row in active:
        table = str(row.get("table") or "")
        family = _family_for_table(table)
        byte_count = int(row.get("bytes_on_disk") or 0)
        row_count = int(row.get("rows") or 0)
        family_row = family_accumulator[family]
        family_row["bytes_on_disk"] += byte_count
        family_row["rows"] += row_count
        family_row["table_count"] += 1
        table_rows.append(
            {
                "table": table,
                "family": family,
                "bytes_on_disk": byte_count,
                "rows": row_count,
                "byte_share": (byte_count / total_bytes) if total_bytes else 0.0,
                "row_share": (row_count / total_rows) if total_rows else 0.0,
            }
        )

    families = []
    for family, values in family_accumulator.items():
        byte_count = values["bytes_on_disk"]
        row_count = values["rows"]
        families.append(
            {
                "family": family,
                **values,
                "byte_share": (byte_count / total_bytes) if total_bytes else 0.0,
                "row_share": (row_count / total_rows) if total_rows else 0.0,
            }
        )

    families.sort(key=lambda row: (-row["bytes_on_disk"], row["family"]))
    table_rows.sort(key=lambda row: (-row["bytes_on_disk"], row["table"]))

    return {
        "profile_version": PROFILE_VERSION,
        "read_only": True,
        "active_bytes": total_bytes,
        "active_rows": total_rows,
        "families": families,
        "tables": table_rows,
        "largest_tables": table_rows[:10],
        "review_priority": [
            row["family"]
            for row in families
            if row["family"] in {"cn_goods", "cn_events", "cn_party"}
        ],
        "tier_decision": "REQUIRES_CONSUMER_AND_RECONSTRUCTIBILITY_REVIEW",
    }


def build_live_capacity_profile() -> dict[str, Any]:
    audit = build_storage_audit(deep=False)
    profile = build_capacity_profile(audit.get("parts") or [])
    profile["audit_version"] = audit.get("audit_version")
    return profile


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only MarkOrbit table-family storage capacity profile."
    )
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    report = build_live_capacity_profile()
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
