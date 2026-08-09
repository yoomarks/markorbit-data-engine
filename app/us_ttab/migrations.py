from __future__ import annotations

from app.db import clickhouse_client, postgres_conn
from app.us_ttab import TTAB_SCHEMA_VERSION


REQUIRED_TABLES = {
    "us_ttab_proceeding_history",
    "us_ttab_party_history",
    "us_ttab_property_history",
    "us_ttab_docket_history",
}


def ensure_ttab_schema() -> None:
    client = clickhouse_client()
    available = {
        str(row[0])
        for row in client.query(
            """
            SELECT name FROM system.tables
            WHERE database = 'markorbit_facts' AND name LIKE 'us_ttab_%'
            """
        ).result_rows
    }
    missing = sorted(REQUIRED_TABLES - available)
    if missing:
        raise RuntimeError(
            "US TTAB schema is not initialized. Missing tables: "
            f"{', '.join(missing)}. Run scripts/apply-us-ttab-schema.ps1."
        )
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO control.schema_version(component, version)
                VALUES ('US_TTAB', %s)
                ON CONFLICT (component)
                DO UPDATE SET version = EXCLUDED.version, applied_at = now()
                """,
                (TTAB_SCHEMA_VERSION,),
            )
        conn.commit()
