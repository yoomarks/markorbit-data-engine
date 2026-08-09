from __future__ import annotations

from app.db import clickhouse_client, postgres_conn
from app.us_ttab import TTAB_SCHEMA_VERSION


REQUIRED_TABLES = {
    "us_ttab_proceeding_history",
    "us_ttab_party_history",
    "us_ttab_property_history",
    "us_ttab_docket_history",
}
REQUIRED_COLUMNS = {
    "us_ttab_proceeding_history": {"proceeding_type_code", "status_code"},
    "us_ttab_party_history": {
        "party_id",
        "role",
        "company",
        "organization",
        "granted_to_date_raw",
        "correspondent_organization",
    },
    "us_ttab_property_history": {
        "mark_explanation",
        "property_filing",
        "property_filing_code",
        "common_law_indicator",
        "application_status_code",
        "trademark_gid",
    },
    "us_ttab_docket_history": {
        "identifier",
        "object_id",
        "entry_code",
        "confidential",
    },
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
    for table, required in REQUIRED_COLUMNS.items():
        columns = {
            str(row[0])
            for row in client.query(
                f"""
                SELECT name FROM system.columns
                WHERE database = 'markorbit_facts' AND table = '{table}'
                """
            ).result_rows
        }
        absent = sorted(required - columns)
        if absent:
            raise RuntimeError(
                f"US TTAB M1.1 schema is missing columns in {table}: {', '.join(absent)}. "
                "Run scripts/apply-us-ttab-schema.ps1."
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
