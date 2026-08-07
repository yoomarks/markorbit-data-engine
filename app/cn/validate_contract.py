from __future__ import annotations

import json
import time
import uuid

from app.cn.ingest import STAGE_COLUMNS, _publish
from app.cn.text import application_number_parts
from app.db import clickhouse_client


PERMANENT_LINEAGE_TABLES = [
    "cn_case_current",
    "cn_case_scope_current",
    "cn_case_party_current",
    "cn_case_party_relation_history",
    "cn_agent_current",
    "cn_priority_current",
    "cn_madrid_current",
    "cn_observed_event",
    "cn_case_relation_current",
    "cn_scope_carve_out_current",
]


def _table_columns(client, table: str) -> set[str]:
    rows = client.query(
        "SELECT name FROM system.columns "
        "WHERE database = 'markorbit_facts' "
        f"AND table = '{table}'"
    ).result_rows
    return {str(row[0]) for row in rows}


def _assert_lineage_contract(client) -> None:
    for qualified in STAGE_COLUMNS:
        table = qualified.split(".", 1)[1]
        columns = _table_columns(client, table)
        missing = {"source_file", "source_start_line", "source_end_line", "row_hash"} - columns
        if missing:
            raise RuntimeError(f"stage lineage contract failed for {table}: missing {sorted(missing)}")

    for table in PERMANENT_LINEAGE_TABLES:
        columns = _table_columns(client, table)
        missing = {"source_file", "source_first_line", "source_last_line"} - columns
        if missing:
            raise RuntimeError(
                f"permanent lineage contract failed for {table}: missing {sorted(missing)}"
            )
        forbidden = {"source_start_line", "source_end_line"} & columns
        if forbidden:
            raise RuntimeError(
                f"permanent lineage contract failed for {table}: forbidden {sorted(forbidden)}"
            )


def _assert_number_contract() -> None:
    parts = application_number_parts("G602365A")
    expected = {
        "full": "G602365A",
        "family_root": "G602365",
        "suffix_path": "A",
        "filing_route": "MADRID_DESIGNATION_CN",
        "international_registration_number": "602365",
        "is_derived_case": True,
    }
    actual = {name: getattr(parts, name) for name in expected}
    if actual != expected:
        raise RuntimeError(f"G-number contract failed: expected={expected}, actual={actual}")


def _assert_empty_publish_compiles() -> dict[str, int]:
    # A random package has no stage rows. _publish therefore compiles and executes every
    # production INSERT...SELECT path without importing a real ZIP or changing business data.
    # This catches ClickHouse identifier/alias/aggregation/column-count errors in seconds.
    package_uuid = uuid.uuid4()
    metrics = _publish(
        package_uuid,
        {
            "package_kind": "CONTRACT_PREFLIGHT",
            "source_rank": 1,
            "source_period_end": None,
        },
    )
    nonzero = {key: value for key, value in metrics.items() if int(value or 0) != 0}
    if nonzero:
        raise RuntimeError(f"empty publish preflight unexpectedly produced rows: {nonzero}")
    return metrics


def main() -> None:
    started = time.perf_counter()
    client = clickhouse_client()

    _assert_lineage_contract(client)
    _assert_number_contract()
    metrics = _assert_empty_publish_compiles()

    result = {
        "status": "PASS",
        "contract": "M1.5.3",
        "checks": [
            "stage_lineage_schema",
            "permanent_lineage_schema",
            "g_number_model",
            "empty_publish_all_sql_paths",
        ],
        "publish_metrics": metrics,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
