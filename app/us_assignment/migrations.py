from __future__ import annotations

from app.db import clickhouse_client, postgres_conn
from app.us_assignment import ASSIGNMENT_SCHEMA_VERSION


REQUIRED_TABLES = {
    "us_assignment_record_history",
    "us_assignment_assignor_history",
    "us_assignment_assignee_history",
    "us_assignment_property_history",
}


def ensure_assignment_schema() -> None:
    client = clickhouse_client()
    rows = client.query(
        """
        SELECT name
        FROM system.tables
        WHERE database = 'markorbit_facts' AND name LIKE 'us_assignment_%'
        """
    ).result_rows
    available = {str(row[0]) for row in rows}
    missing = sorted(REQUIRED_TABLES - available)
    if missing:
        raise RuntimeError(
            "US assignment schema is not initialized. Missing tables: "
            f"{', '.join(missing)}. Run scripts/apply-us-assignment-schema.ps1."
        )
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO control.schema_version(component, version)
                VALUES ('US_ASSIGNMENT', %s)
                ON CONFLICT (component)
                DO UPDATE SET version = EXCLUDED.version, applied_at = now()
                """,
                (ASSIGNMENT_SCHEMA_VERSION,),
            )
        conn.commit()
