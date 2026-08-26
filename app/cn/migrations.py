from __future__ import annotations

from collections.abc import Iterable

from app.db import clickhouse_client, postgres_conn


REQUIRED_CLICKHOUSE_COLUMNS = {
    ("cn_case_current", "source_rank"),
    ("cn_case_current", "filing_route"),
    ("cn_case_current", "exclusive_period_raw"),
    ("cn_case_scope_current", "unmapped_status_item_count"),
    ("cn_case_scope_current", "interpretation_complete"),
    ("cn_case_party_current", "relation_key"),
    ("cn_observed_event", "field_name"),
    ("cn_case_relation_current", "relation_type"),
}

EXPECTED_CN_GOODS_ITEM_CURRENT_COLUMNS = (
    "case_id",
    "application_number",
    "class_no",
    "goods_item_key",
    "goods_sequence",
    "goods_name",
    "goods_name_norm",
    "similar_group",
    "goods_status_raw",
    "goods_status_bucket",
    "goods_status_reason",
    "goods_status_semantic",
    "goods_status_source_finality",
    "operational_effect",
    "goods_status_mapping_version",
    "evidence_label",
    "first_source_package_id",
    "first_source_package_kind",
    "first_source_rank",
    "source_package_kind",
    "source_effective_date",
    "source_file",
    "source_first_line",
    "source_last_line",
    "source_row_hash",
    "last_source_package_id",
    "record_hash",
    "source_rank",
    "ingested_at",
    "is_deleted",
)


def assert_exact_goods_current_schema(
    rows: Iterable[tuple[object, object, object]],
) -> None:
    """Reject legacy or reordered goods-current schemas before CN work starts."""
    actual = tuple(
        str(name)
        for table, name, _position in sorted(rows, key=lambda row: int(row[2]))
        if str(table) == "cn_goods_item_current"
    )
    expected = EXPECTED_CN_GOODS_ITEM_CURRENT_COLUMNS
    if actual == expected:
        return

    actual_display = ", ".join(actual) if actual else "<missing>"
    raise RuntimeError(
        "CN goods schema migration required before replay/import: "
        "markorbit_facts.cn_goods_item_current must expose exactly "
        f"{len(expected)} columns in positional-write order; found {len(actual)}. "
        f"Expected: {', '.join(expected)}. Actual: {actual_display}. "
        "Fix the ClickHouse schema before continuing; no CN replay/import was started."
    )


def ensure_m15_schema() -> None:
    """Fail early when an incompatible CN schema is used by current code.

    M1.5 changes replacement keys and permanent field semantics. An in-place
    migration would risk presenting old rows under the new meaning, so M1.5
    intentionally requires a clean development-volume reset while preserving
    raw ZIP files. The M1.6 goods publisher also uses an intentional positional
    write contract, so its current table must match the exact 30-column order.
    """
    client = clickhouse_client()
    rows = client.query(
        """
        SELECT table, name, position
        FROM system.columns
        WHERE database = 'markorbit_facts'
        ORDER BY table, position
        """
    ).result_rows
    available = {(str(table), str(name)) for table, name, _position in rows}
    missing = sorted(REQUIRED_CLICKHOUSE_COLUMNS - available)
    if missing:
        formatted = ", ".join(f"{table}.{column}" for table, column in missing)
        raise RuntimeError(
            "M1.5 ClickHouse schema is not initialized. Missing: "
            f"{formatted}. Run scripts/reset-m15.ps1; raw_data is not removed."
        )

    assert_exact_goods_current_schema(rows)

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT version FROM control.schema_version WHERE component = 'CN_CORE'"
            )
            row = cur.fetchone()
    if not row or row["version"] != "M1.5":
        raise RuntimeError(
            "M1.5 PostgreSQL schema is not initialized. "
            "Run scripts/reset-m15.ps1; raw_data is not removed."
        )


# Compatibility name used by M1.4 startup code.
def ensure_cn_date32_schema() -> list[str]:
    ensure_m15_schema()
    return []
