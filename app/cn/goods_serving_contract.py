from __future__ import annotations

from typing import Any, Iterable


GOODS_SERVING_CONTRACT_VERSION = "CN_GOODS_SERVING_SCHEMA_V1"
DATABASE = "markorbit_facts"
TABLE = "cn_goods_item_current"

GOODS_SERVING_COLUMNS: tuple[tuple[str, str], ...] = (
    ("case_id", "UUID"),
    ("application_number", "String"),
    ("class_no", "UInt8"),
    ("goods_item_key", "FixedString(64)"),
    ("goods_sequence", "String"),
    ("goods_name", "String"),
    ("goods_name_norm", "String"),
    ("similar_group", "String"),
    ("goods_status_raw", "String"),
    ("goods_status_bucket", "LowCardinality(String)"),
    ("goods_status_reason", "LowCardinality(String)"),
    ("goods_status_semantic", "LowCardinality(String)"),
    ("goods_status_source_finality", "LowCardinality(String)"),
    ("operational_effect", "LowCardinality(String)"),
    ("goods_status_mapping_version", "String"),
    ("evidence_label", "LowCardinality(String)"),
    ("first_source_package_id", "UUID"),
    ("first_source_package_kind", "LowCardinality(String)"),
    ("first_source_rank", "UInt64"),
    ("source_package_kind", "LowCardinality(String)"),
    ("source_effective_date", "Nullable(Date32)"),
    ("source_file", "String"),
    ("source_first_line", "UInt64"),
    ("source_last_line", "UInt64"),
    ("source_row_hash", "FixedString(64)"),
    ("last_source_package_id", "UUID"),
    ("record_hash", "FixedString(64)"),
    ("source_rank", "UInt64"),
    ("ingested_at", "DateTime64(3, 'UTC')"),
    ("is_deleted", "UInt8"),
)

GOODS_SERVING_COLUMN_NAMES = tuple(name for name, _ in GOODS_SERVING_COLUMNS)
GOODS_SERVING_SELECT_LIST = ",\n                ".join(GOODS_SERVING_COLUMN_NAMES)

_METADATA_SQL = f"""
SELECT name, type, position
FROM system.columns
WHERE database = '{DATABASE}'
  AND table = '{TABLE}'
ORDER BY position
"""


def evaluate_goods_serving_schema(
    rows: Iterable[tuple[str, str, int] | dict[str, Any]],
) -> dict[str, Any]:
    actual: list[tuple[str, str]] = []
    positions: list[int] = []
    for row in rows:
        if isinstance(row, dict):
            name = str(row.get("name") or "")
            type_name = str(row.get("type") or "")
            position = int(row.get("position") or 0)
        else:
            name, type_name, position = row
            name = str(name)
            type_name = str(type_name)
            position = int(position)
        actual.append((name, type_name))
        positions.append(position)

    expected = list(GOODS_SERVING_COLUMNS)
    expected_names = [name for name, _ in expected]
    actual_names = [name for name, _ in actual]
    expected_types = dict(expected)
    actual_types = dict(actual)

    missing = [name for name in expected_names if name not in actual_types]
    extra = [name for name in actual_names if name not in expected_types]
    retyped = [
        {
            "name": name,
            "expected": expected_types[name],
            "actual": actual_types[name],
        }
        for name in expected_names
        if name in actual_types and actual_types[name] != expected_types[name]
    ]
    expected_positions = list(range(1, len(expected) + 1))
    ordered = actual_names == expected_names and positions == expected_positions
    exact = actual == expected and ordered

    reasons: list[str] = []
    if missing:
        reasons.append("MISSING_COLUMNS")
    if extra:
        reasons.append("EXTRA_COLUMNS")
    if retyped:
        reasons.append("RETYPED_COLUMNS")
    if actual_names != expected_names:
        reasons.append("COLUMN_ORDER_OR_SET_DRIFT")
    if positions != expected_positions:
        reasons.append("COLUMN_POSITION_DRIFT")

    return {
        "contract_version": GOODS_SERVING_CONTRACT_VERSION,
        "status": "PASS" if exact else "BLOCKED",
        "exact_match": exact,
        "database": DATABASE,
        "table": TABLE,
        "expected_column_count": len(expected),
        "actual_column_count": len(actual),
        "missing_columns": missing,
        "extra_columns": extra,
        "retyped_columns": retyped,
        "ordered": ordered,
        "reason_codes": reasons,
        "migration_authorized": False,
    }


def read_goods_serving_schema(client: Any) -> list[tuple[str, str, int]]:
    normalized = " ".join(_METADATA_SQL.upper().split())
    if "FROM SYSTEM.COLUMNS" not in normalized:
        raise RuntimeError("goods serving schema guard must read system.columns only")
    for forbidden in (" FINAL ", " ALTER ", " INSERT ", " DELETE ", " OPTIMIZE "):
        if forbidden in f" {normalized} ":
            raise RuntimeError(
                f"goods serving schema guard contains forbidden token: {forbidden.strip()}"
            )
    return [
        (str(name), str(type_name), int(position))
        for name, type_name, position in client.query(_METADATA_SQL).result_rows
    ]


def assert_goods_serving_schema(client: Any) -> dict[str, Any]:
    report = evaluate_goods_serving_schema(read_goods_serving_schema(client))
    if not report["exact_match"]:
        raise RuntimeError(
            "CN M1.6 goods serving schema drift detected. "
            f"Expected {report['expected_column_count']} frozen columns; "
            f"actual={report['actual_column_count']}; "
            f"reasons={','.join(report['reason_codes']) or 'UNKNOWN'}. "
            "Do not serve or project cn_goods_item_current until the drift is reviewed."
        )
    return report
