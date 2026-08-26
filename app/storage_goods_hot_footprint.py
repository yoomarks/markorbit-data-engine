from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any, Iterable

from app.db import clickhouse_client


AUDIT_VERSION = "DATA_ENGINE_CN_GOODS_HOT_FOOTPRINT_V1"
DATABASE = "markorbit_facts"
TABLE = "cn_goods_item_current"

COLUMNS_SQL = f"""
SELECT
    name,
    type,
    position,
    default_kind,
    default_expression,
    data_compressed_bytes,
    data_uncompressed_bytes,
    marks_bytes,
    is_in_partition_key,
    is_in_sorting_key,
    is_in_primary_key,
    is_in_sampling_key
FROM system.columns
WHERE database = '{DATABASE}'
  AND table = '{TABLE}'
ORDER BY position
"""

TABLE_SQL = f"""
SELECT
    engine,
    sorting_key,
    primary_key,
    partition_key
FROM system.tables
WHERE database = '{DATABASE}'
  AND name = '{TABLE}'
LIMIT 1
"""

METADATA_QUERIES = (COLUMNS_SQL, TABLE_SQL)


def _assert_metadata_only_queries() -> None:
    allowed_sources = ("FROM SYSTEM.COLUMNS", "FROM SYSTEM.TABLES")
    for sql in METADATA_QUERIES:
        normalized = " ".join(sql.upper().split())
        if not normalized.startswith("SELECT "):
            raise RuntimeError("goods Hot footprint audit contains a non-SELECT query")
        if not any(source in normalized for source in allowed_sources):
            raise RuntimeError("goods Hot footprint audit may read system metadata only")
        for forbidden in (
            " ALTER ",
            " DELETE ",
            " DROP ",
            " INSERT ",
            " OPTIMIZE ",
            " TRUNCATE ",
            " UPDATE ",
            " FINAL ",
        ):
            if forbidden in f" {normalized} ":
                raise RuntimeError(
                    f"goods Hot footprint audit contains forbidden token: {forbidden.strip()}"
                )


def _column_role(name: str, *, key_member: bool) -> str:
    lowered = name.lower()
    if key_member:
        return "key"
    if any(token in lowered for token in ("source", "package", "rank", "provenance")):
        return "provenance"
    if any(
        token in lowered
        for token in ("version", "observed", "updated", "created", "ingested", "hash")
    ):
        return "control"
    return "payload"


def detect_select_star_goods_contract(repo_root: Path) -> dict[str, Any]:
    path = repo_root / "app" / "main_core.py"
    if not path.exists():
        return {
            "status": "SOURCE_MISSING",
            "path": str(path),
            "select_star": False,
        }
    text = path.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(
        rf"SELECT\s+\*\s+FROM\s+(?:{re.escape(DATABASE)}\.)?{re.escape(TABLE)}\b",
        re.IGNORECASE | re.DOTALL,
    )
    matched = bool(pattern.search(text))
    return {
        "status": "SELECT_STAR_ALL_COLUMNS_EXPOSED" if matched else "REVIEW_REQUIRED",
        "path": path.relative_to(repo_root).as_posix(),
        "select_star": matched,
    }


def evaluate_goods_hot_footprint(
    *,
    columns: Iterable[dict[str, Any]],
    table_metadata: dict[str, Any] | None,
    api_contract: dict[str, Any],
) -> dict[str, Any]:
    rows = [dict(row) for row in columns]
    total_compressed = sum(int(row.get("data_compressed_bytes") or 0) for row in rows)
    total_uncompressed = sum(int(row.get("data_uncompressed_bytes") or 0) for row in rows)
    total_marks = sum(int(row.get("marks_bytes") or 0) for row in rows)

    profiled_columns: list[dict[str, Any]] = []
    for row in rows:
        compressed = int(row.get("data_compressed_bytes") or 0)
        uncompressed = int(row.get("data_uncompressed_bytes") or 0)
        marks = int(row.get("marks_bytes") or 0)
        key_flags = {
            "partition": bool(row.get("is_in_partition_key")),
            "sorting": bool(row.get("is_in_sorting_key")),
            "primary": bool(row.get("is_in_primary_key")),
            "sampling": bool(row.get("is_in_sampling_key")),
        }
        profiled_columns.append(
            {
                "name": str(row.get("name") or ""),
                "type": str(row.get("type") or ""),
                "position": int(row.get("position") or 0),
                "default_kind": str(row.get("default_kind") or ""),
                "default_expression": str(row.get("default_expression") or ""),
                "data_compressed_bytes": compressed,
                "data_uncompressed_bytes": uncompressed,
                "marks_bytes": marks,
                "compressed_share": (
                    compressed / total_compressed if total_compressed else 0.0
                ),
                "compression_ratio": (
                    uncompressed / compressed if compressed > 0 else None
                ),
                "key_membership": key_flags,
                "role": _column_role(
                    str(row.get("name") or ""), key_member=any(key_flags.values())
                ),
                "api_exposed_under_current_contract": bool(api_contract.get("select_star")),
                "removal_allowed_under_current_contract": False,
            }
        )

    profiled_columns.sort(
        key=lambda row: (-row["data_compressed_bytes"], row["position"], row["name"])
    )
    metadata_present = bool(profiled_columns) and table_metadata is not None
    select_star = bool(api_contract.get("select_star"))
    status = "PASS" if metadata_present and select_star else "REVIEW_REQUIRED"
    reasons: list[str] = []
    if not profiled_columns:
        reasons.append("DEPLOYED_COLUMN_METADATA_MISSING")
    if table_metadata is None:
        reasons.append("DEPLOYED_TABLE_METADATA_MISSING")
    if not select_star:
        reasons.append("CURRENT_API_SELECT_STAR_CONTRACT_NOT_CONFIRMED")

    return {
        "audit_version": AUDIT_VERSION,
        "status": status,
        "read_only": True,
        "metadata_only": True,
        "database": DATABASE,
        "table": TABLE,
        "api_contract": {
            **api_contract,
            "endpoint": "/api/cn/cases/{application_number}",
            "all_table_columns_exposed": select_star,
            "narrowing_without_contract_change_allowed": False,
        },
        "table_metadata": table_metadata,
        "totals": {
            "column_count": len(profiled_columns),
            "data_compressed_bytes": total_compressed,
            "data_uncompressed_bytes": total_uncompressed,
            "marks_bytes": total_marks,
            "compression_ratio": (
                total_uncompressed / total_compressed if total_compressed > 0 else None
            ),
        },
        "columns": profiled_columns,
        "largest_columns": profiled_columns[:10],
        "compatibility_decision": (
            "NO_IN_PLACE_COLUMN_REMOVAL_UNDER_CURRENT_SELECT_STAR_API_CONTRACT"
        ),
        "compatibility_preserving_removable_bytes": 0,
        "migration_authorized": False,
        "next_safe_optimization_classes": [
            "measure whether deployed codecs/types/order keys can reduce bytes without changing response fields",
            "design a compatibility projection or view only if every currently exposed field remains available",
            "version the CN case API before intentionally removing response fields",
            "retain official-source rebuild authority and rollback evidence before any future physical cutover",
        ],
        "reason_codes": reasons,
    }


def read_deployed_metadata(client: Any | None = None) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    _assert_metadata_only_queries()
    client = client or clickhouse_client()
    column_rows = client.query(COLUMNS_SQL).result_rows
    columns = [
        {
            "name": str(name),
            "type": str(type_name),
            "position": int(position or 0),
            "default_kind": str(default_kind or ""),
            "default_expression": str(default_expression or ""),
            "data_compressed_bytes": int(data_compressed_bytes or 0),
            "data_uncompressed_bytes": int(data_uncompressed_bytes or 0),
            "marks_bytes": int(marks_bytes or 0),
            "is_in_partition_key": bool(is_in_partition_key),
            "is_in_sorting_key": bool(is_in_sorting_key),
            "is_in_primary_key": bool(is_in_primary_key),
            "is_in_sampling_key": bool(is_in_sampling_key),
        }
        for (
            name,
            type_name,
            position,
            default_kind,
            default_expression,
            data_compressed_bytes,
            data_uncompressed_bytes,
            marks_bytes,
            is_in_partition_key,
            is_in_sorting_key,
            is_in_primary_key,
            is_in_sampling_key,
        ) in column_rows
    ]
    table_rows = client.query(TABLE_SQL).result_rows
    table_metadata = None
    if table_rows:
        engine, sorting_key, primary_key, partition_key = table_rows[0]
        table_metadata = {
            "engine": str(engine or ""),
            "sorting_key": str(sorting_key or ""),
            "primary_key": str(primary_key or ""),
            "partition_key": str(partition_key or ""),
        }
    return columns, table_metadata


def build_goods_hot_footprint(*, repo_root: Path | None = None) -> dict[str, Any]:
    root = (repo_root or Path(__file__).resolve().parents[1]).resolve()
    columns, table_metadata = read_deployed_metadata()
    api_contract = detect_select_star_goods_contract(root)
    return evaluate_goods_hot_footprint(
        columns=columns,
        table_metadata=table_metadata,
        api_contract=api_contract,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Metadata-only CN goods Hot footprint and API-compatibility audit. "
            "Reads system.columns/system.tables; does not scan corpus rows."
        )
    )
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    report = build_goods_hot_footprint(repo_root=args.root)
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=None if args.compact else 2,
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "PASS" else 4


if __name__ == "__main__":
    raise SystemExit(main())
