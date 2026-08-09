from __future__ import annotations

from app.db import clickhouse_client, postgres_conn


US_SCHEMA_VERSION = "US_M1.0"
REQUIRED_TABLES = {
    "us_case_current",
    "us_owner_current",
    "us_classification_current",
    "us_event_history",
    "us_statement_current",
}


def ensure_us_m1_schema() -> None:
    client = clickhouse_client()
    rows = client.query(
        """
        SELECT name
        FROM system.tables
        WHERE database = 'markorbit_facts' AND name LIKE 'us_%'
        """
    ).result_rows
    available = {str(row[0]) for row in rows}
    missing = sorted(REQUIRED_TABLES - available)
    if missing:
        raise RuntimeError(
            "US M1 ClickHouse schema is not initialized. Missing: "
            f"{', '.join(missing)}. Run scripts/apply-us-m1-schema.ps1."
        )

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO control.schema_version(component, version)
                VALUES ('US_CORE', %s)
                ON CONFLICT (component)
                DO UPDATE SET version = EXCLUDED.version, applied_at = now()
                """,
                (US_SCHEMA_VERSION,),
            )
        conn.commit()
