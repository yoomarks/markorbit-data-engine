from app.db import postgres_conn
from app.global_trademarks.schema import ensure_country_trademark_schemas


EXPECTED_TABLES = (
    "trademark_gb.historical_record",
    "trademark_gb.weekly_observation",
    "trademark_gb.comparable_relationship",
    "trademark_eu.tm_link_seed",
    "trademark_eu.api_observation",
    "trademark_nz.tm_link_seed",
    "trademark_nz.api_observation",
    "trademark_au.application",
    "trademark_au.party_activity",
    "trademark_au.application_link",
    "trademark_au.application_event",
    "trademark_au.application_classification",
    "trademark_au.application_description",
    "trademark_ca.st96_record",
    "trademark_ca.party",
    "trademark_ca.goods_service",
    "trademark_ca.event",
    "trademark_ca.relationship",
    "trademark_ca.asset",
)


def main() -> int:
    ensure_country_trademark_schemas()
    ensure_country_trademark_schemas()

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            for table in EXPECTED_TABLES:
                cur.execute("SELECT to_regclass(%s) AS table_name", (table,))
                row = cur.fetchone()
                if not row or row["table_name"] is None:
                    raise RuntimeError(f"missing country trademark table: {table}")

            cur.execute(
                """
                SELECT COUNT(*) AS count
                FROM information_schema.tables
                WHERE table_schema IN (
                    'trademark_gb', 'trademark_eu', 'trademark_nz',
                    'trademark_au', 'trademark_ca'
                )
                """
            )
            table_count = int(cur.fetchone()["count"])

    if table_count != len(EXPECTED_TABLES):
        raise RuntimeError(
            f"unexpected country trademark table count: {table_count} != {len(EXPECTED_TABLES)}"
        )

    print(
        {
            "status": "PASS",
            "country_native_schemas": ["GB", "EU", "NZ", "AU", "CA"],
            "table_count": table_count,
            "idempotent_migration": True,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
