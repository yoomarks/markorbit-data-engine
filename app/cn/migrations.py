from __future__ import annotations

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


def ensure_m15_schema() -> None:
    """Fail early when an M1.0-M1.4 volume is used with M1.5 code.

    M1.5 changes replacement keys and permanent field semantics. An in-place
    migration would risk presenting old rows under the new meaning, so M1.5
    intentionally requires a clean development-volume reset while preserving
    raw ZIP files.
    """
    client = clickhouse_client()
    rows = client.query(
        """
        SELECT table, name
        FROM system.columns
        WHERE database = 'markorbit_facts'
        """
    ).result_rows
    available = {(str(table), str(name)) for table, name in rows}
    missing = sorted(REQUIRED_CLICKHOUSE_COLUMNS - available)
    if missing:
        formatted = ", ".join(f"{table}.{column}" for table, column in missing)
        raise RuntimeError(
            "M1.5 ClickHouse schema is not initialized. Missing: "
            f"{formatted}. Run scripts/reset-m15.ps1; raw_data is not removed."
        )

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
