from __future__ import annotations

from app.db import clickhouse_client, postgres_conn


US_SCHEMA_VERSION = "US_M1.4"
REQUIRED_TABLES = {
    "us_case_current",
    "us_owner_current",
    "us_classification_current",
    "us_event_history",
    "us_statement_current",
    "us_correspondent_current",
    "us_design_search_current",
    "us_prior_registration_current",
    "us_foreign_application_current",
    "us_madrid_filing_current",
    "us_madrid_event_history",
    "us_case_observation_history",
}
REQUIRED_COLUMNS = {
    ("us_case_current", "transaction_date"),
    ("us_case_current", "use_1a_filed"),
    ("us_case_current", "use_1a_current"),
    ("us_case_current", "madrid_66a_current"),
    ("us_case_current", "section_8_accepted"),
    ("us_case_current", "international_registration_date"),
    ("us_owner_current", "entity_statement"),
    ("us_event_history", "description_text"),
    ("us_correspondent_current", "attorney_name"),
    ("us_design_search_current", "code"),
    ("us_prior_registration_current", "number"),
    ("us_foreign_application_current", "foreign_priority_claimed"),
    ("us_madrid_filing_current", "reference_number"),
    ("us_madrid_event_history", "description_text"),
    ("us_case_observation_history", "owner_set_hash"),
    ("us_case_observation_history", "owner_record_set_hash"),
    ("us_case_observation_history", "observation_hash"),
    ("us_case_observation_history", "source_package_id"),
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
            "US M1.4 ClickHouse schema is not initialized. Missing tables: "
            f"{', '.join(missing)}. Run scripts/apply-us-m1-schema.ps1."
        )

    column_rows = client.query(
        """
        SELECT table, name
        FROM system.columns
        WHERE database = 'markorbit_facts' AND table LIKE 'us_%'
        """
    ).result_rows
    available_columns = {(str(table), str(name)) for table, name in column_rows}
    missing_columns = sorted(REQUIRED_COLUMNS - available_columns)
    if missing_columns:
        formatted = ", ".join(f"{table}.{name}" for table, name in missing_columns)
        raise RuntimeError(
            "US M1.4 ClickHouse schema is not initialized. Missing columns: "
            f"{formatted}. Run scripts/apply-us-m1-schema.ps1."
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
