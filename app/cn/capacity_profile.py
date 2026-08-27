from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from app.db import clickhouse_client


PROFILE_VERSION = "CN_HOT_WARM_CAPACITY_PROFILE_V1"
FACT_DATABASE = "markorbit_facts"
HOT_DISK_NAME = "default"
HARD_MIN_POST_SCALE_FREE_RATIO = 0.20
RECOMMENDED_MIN_POST_SCALE_FREE_RATIO = 0.30

# This is a storage-planning contract only. It does not authorize moving,
# deleting, compacting, or rewriting any live facts.
TABLE_PLACEMENT_CONTRACT: dict[str, str] = {
    "cn_case_current": "HOT_REQUIRED_CURRENT_SERVING",
    "cn_case_scope_current": "HOT_REQUIRED_CURRENT_SERVING",
    "cn_case_party_current": "HOT_REQUIRED_CURRENT_SERVING",
    "cn_goods_item_current": "HOT_REQUIRED_CURRENT_SERVING",
    "cn_goods_scope_lifecycle_current": "HOT_REQUIRED_CURRENT_SERVING",
    "cn_observed_event": "HOT_WITH_COMPACTABLE_BASELINE",
    "cn_goods_item_observation": "WARM_AFTER_SUMMARY_EQUIVALENCE",
}
DEFAULT_PLACEMENT_CONTRACT = "UNCLASSIFIED_RETAIN_AS_IS"

CLICKHOUSE_ACTIVE_PARTS_BY_DISK_SQL = f"""
    SELECT
        table,
        disk_name,
        count() AS active_parts,
        coalesce(sum(rows), 0) AS rows_from_parts,
        coalesce(sum(bytes_on_disk), 0) AS bytes_on_disk,
        coalesce(sum(data_compressed_bytes), 0) AS data_compressed_bytes,
        coalesce(sum(data_uncompressed_bytes), 0) AS data_uncompressed_bytes
    FROM system.parts
    WHERE database = '{FACT_DATABASE}'
      AND active
    GROUP BY table, disk_name
    ORDER BY table, disk_name
"""

CLICKHOUSE_DISKS_SQL = """
    SELECT name, path, free_space, total_space, keep_free_space
    FROM system.disks
    ORDER BY name
"""

READ_ONLY_QUERIES = (
    CLICKHOUSE_ACTIVE_PARTS_BY_DISK_SQL,
    CLICKHOUSE_DISKS_SQL,
)


def _family_for_table(table: str) -> str:
    if table.startswith("cn_goods_"):
        return "goods"
    if "party" in table:
        return "party"
    if table == "cn_observed_event" or table.endswith("_event"):
        return "events"
    if table.startswith("cn_case_") or table == "cn_case_current":
        return "case"
    return "other"


def _placement_for_table(table: str) -> str:
    return TABLE_PLACEMENT_CONTRACT.get(table, DEFAULT_PLACEMENT_CONTRACT)


def _is_hot_contract(contract: str) -> bool:
    return contract.startswith("HOT_")


def _is_warm_candidate(contract: str) -> bool:
    return contract.startswith("WARM_")


def _aggregate_parts(
    rows: list[tuple[Any, Any, Any, Any, Any, Any, Any]],
) -> list[dict[str, Any]]:
    tables: dict[str, dict[str, Any]] = {}
    for (
        table,
        disk_name,
        active_parts,
        rows_from_parts,
        bytes_on_disk,
        compressed_bytes,
        uncompressed_bytes,
    ) in rows:
        table_name = str(table)
        disk = str(disk_name)
        entry = tables.setdefault(
            table_name,
            {
                "table": table_name,
                "family": _family_for_table(table_name),
                "placement_contract": _placement_for_table(table_name),
                "active_parts": 0,
                "rows_from_parts": 0,
                "bytes_on_disk": 0,
                "data_compressed_bytes": 0,
                "data_uncompressed_bytes": 0,
                "by_disk": [],
            },
        )
        disk_entry = {
            "disk_name": disk,
            "active_parts": int(active_parts or 0),
            "rows_from_parts": int(rows_from_parts or 0),
            "bytes_on_disk": int(bytes_on_disk or 0),
            "data_compressed_bytes": int(compressed_bytes or 0),
            "data_uncompressed_bytes": int(uncompressed_bytes or 0),
        }
        entry["by_disk"].append(disk_entry)
        for key in (
            "active_parts",
            "rows_from_parts",
            "bytes_on_disk",
            "data_compressed_bytes",
            "data_uncompressed_bytes",
        ):
            entry[key] += disk_entry[key]

    return sorted(
        tables.values(),
        key=lambda item: (-int(item["bytes_on_disk"]), str(item["table"])),
    )


def _disk_report(rows: list[tuple[Any, Any, Any, Any, Any]]) -> list[dict[str, Any]]:
    report: list[dict[str, Any]] = []
    for name, path, free_space, total_space, keep_free_space in rows:
        free_bytes = int(free_space or 0)
        total_bytes = int(total_space or 0)
        report.append(
            {
                "name": str(name),
                "path": str(path),
                "free_space": free_bytes,
                "total_space": total_bytes,
                "keep_free_space": int(keep_free_space or 0),
                "free_ratio": (
                    round(free_bytes / total_bytes, 6) if total_bytes > 0 else None
                ),
            }
        )
    return report


def _floor_budget(*, total_bytes: int, free_bytes: int, floor_ratio: float) -> dict[str, Any]:
    reserve_bytes = int(math.ceil(total_bytes * floor_ratio))
    max_additional_bytes = max(free_bytes - reserve_bytes, 0)
    return {
        "minimum_post_scale_free_ratio": floor_ratio,
        "minimum_post_scale_free_bytes": reserve_bytes,
        "max_additional_hot_bytes": max_additional_bytes,
    }


def _projection_gate(
    *,
    total_bytes: int,
    free_bytes: int,
    projected_us_hot_bytes: int | None,
) -> dict[str, Any]:
    hard = _floor_budget(
        total_bytes=total_bytes,
        free_bytes=free_bytes,
        floor_ratio=HARD_MIN_POST_SCALE_FREE_RATIO,
    )
    recommended = _floor_budget(
        total_bytes=total_bytes,
        free_bytes=free_bytes,
        floor_ratio=RECOMMENDED_MIN_POST_SCALE_FREE_RATIO,
    )

    report: dict[str, Any] = {
        "hard_floor": hard,
        "recommended_floor": recommended,
        "projected_us_hot_bytes": projected_us_hot_bytes,
    }
    if projected_us_hot_bytes is None:
        report.update(
            {
                "decision": "PROJECTION_REQUIRED",
                "projected_post_scale_free_bytes": None,
                "projected_post_scale_free_ratio": None,
                "reason": (
                    "A bounded US Hot-byte projection is required before starting the "
                    "US full-corpus import."
                ),
            }
        )
        return report

    projected = max(int(projected_us_hot_bytes), 0)
    post_free = free_bytes - projected
    post_ratio = post_free / total_bytes if total_bytes > 0 else -1.0
    if post_free < 0 or post_ratio < HARD_MIN_POST_SCALE_FREE_RATIO:
        decision = "NO_GO"
        reason = "Projected US Hot footprint would breach the 20% post-scale free-space floor."
    elif post_ratio < RECOMMENDED_MIN_POST_SCALE_FREE_RATIO:
        decision = "CONDITIONAL_WARN"
        reason = (
            "Projected US Hot footprint clears the 20% hard floor but leaves less than "
            "the recommended 30% merge/spill headroom."
        )
    else:
        decision = "GO_WITHIN_PROJECTED_BUDGET"
        reason = "Projected US Hot footprint retains at least 30% Hot-disk free space."

    report.update(
        {
            "decision": decision,
            "projected_post_scale_free_bytes": post_free,
            "projected_post_scale_free_ratio": round(post_ratio, 6),
            "reason": reason,
        }
    )
    return report


def build_capacity_profile(
    *,
    projected_us_hot_bytes: int | None = None,
    clickhouse_client_factory: Callable[[], Any] = clickhouse_client,
) -> dict[str, Any]:
    client = clickhouse_client_factory()
    tables = _aggregate_parts(
        client.query(CLICKHOUSE_ACTIVE_PARTS_BY_DISK_SQL).result_rows
    )
    disks = _disk_report(client.query(CLICKHOUSE_DISKS_SQL).result_rows)

    family_totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {"tables": 0, "rows_from_parts": 0, "bytes_on_disk": 0}
    )
    placement_totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {"tables": 0, "rows_from_parts": 0, "bytes_on_disk": 0}
    )
    total_rows = 0
    total_bytes = 0
    hot_contract_bytes = 0
    warm_candidate_bytes = 0
    unclassified_bytes = 0

    for table in tables:
        rows = int(table["rows_from_parts"])
        bytes_on_disk = int(table["bytes_on_disk"])
        total_rows += rows
        total_bytes += bytes_on_disk

        family = str(table["family"])
        family_totals[family]["tables"] += 1
        family_totals[family]["rows_from_parts"] += rows
        family_totals[family]["bytes_on_disk"] += bytes_on_disk

        contract = str(table["placement_contract"])
        placement_totals[contract]["tables"] += 1
        placement_totals[contract]["rows_from_parts"] += rows
        placement_totals[contract]["bytes_on_disk"] += bytes_on_disk
        if _is_hot_contract(contract):
            hot_contract_bytes += bytes_on_disk
        elif _is_warm_candidate(contract):
            warm_candidate_bytes += bytes_on_disk
        else:
            unclassified_bytes += bytes_on_disk

    hot_disk = next((disk for disk in disks if disk["name"] == HOT_DISK_NAME), None)
    if hot_disk is None:
        scale_out_gate = {
            "decision": "NO_GO",
            "reason": f"ClickHouse disk {HOT_DISK_NAME!r} was not reported.",
            "projected_us_hot_bytes": projected_us_hot_bytes,
        }
    else:
        scale_out_gate = _projection_gate(
            total_bytes=int(hot_disk["total_space"]),
            free_bytes=int(hot_disk["free_space"]),
            projected_us_hot_bytes=projected_us_hot_bytes,
        )

    return {
        "profile_version": PROFILE_VERSION,
        "read_only": True,
        "database": FACT_DATABASE,
        "query_scope": "clickhouse_system_metadata_only",
        "full_corpus_scan": False,
        "package_reprocessed": False,
        "mutation_performed": False,
        "table_swap_performed": False,
        "tables": tables,
        "family_totals": dict(sorted(family_totals.items())),
        "placement_totals": dict(sorted(placement_totals.items())),
        "active_totals": {
            "rows_from_parts": total_rows,
            "bytes_on_disk": total_bytes,
            "hot_contract_bytes": hot_contract_bytes,
            "warm_candidate_bytes": warm_candidate_bytes,
            "unclassified_retain_as_is_bytes": unclassified_bytes,
        },
        "disks": disks,
        "us_scale_out_gate": scale_out_gate,
        "constraints": {
            "no_live_deletion": True,
            "no_optimize_final": True,
            "no_mutation": True,
            "no_table_swap": True,
            "warm_changes_require_equivalence_and_rollback_evidence": True,
        },
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a CN Hot/Warm capacity profile from ClickHouse system metadata only."
        )
    )
    parser.add_argument(
        "--projected-us-hot-gib",
        type=float,
        default=None,
        help=(
            "Optional projected incremental US Hot footprint in GiB. When omitted, "
            "the report emits budget ceilings but keeps the US gate at PROJECTION_REQUIRED."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    projected_bytes: int | None = None
    if args.projected_us_hot_gib is not None:
        if args.projected_us_hot_gib < 0:
            raise SystemExit("--projected-us-hot-gib must be non-negative")
        projected_bytes = int(args.projected_us_hot_gib * (1024**3))

    report = build_capacity_profile(projected_us_hot_bytes=projected_bytes)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
