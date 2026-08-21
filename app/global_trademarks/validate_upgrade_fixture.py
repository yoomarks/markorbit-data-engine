from app.db import postgres_conn
from app.global_trademarks.ingest_schema import ensure_seed_ingest_schema
from app.global_trademarks.validate_fixture import main as validate_ingest_fixture


LEGACY_FIXTURE_SQL = """
CREATE SCHEMA IF NOT EXISTS acquisition;
CREATE TABLE IF NOT EXISTS acquisition.global_trademark_source_object (
    object_id uuid PRIMARY KEY,
    jurisdiction text NOT NULL,
    source_id text NOT NULL,
    object_key text NOT NULL,
    sha256 text NOT NULL,
    observed_at timestamptz NOT NULL DEFAULT now(),
    source_period_start date,
    source_period_end date,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (source_id, object_key, sha256)
);

CREATE SCHEMA IF NOT EXISTS trademark_au;
CREATE TABLE trademark_au.application (
    application_number text PRIMARY KEY,
    ip_right_sub_type text,
    source_status text,
    earliest_filed_date date,
    priority_date date,
    gained_registration_status_date date,
    gained_enforceable_status_date date,
    enforceable_from_date date,
    deemed_retired_date date,
    source_object_id uuid REFERENCES acquisition.global_trademark_source_object(object_id),
    source_payload jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE trademark_au.party_activity (
    application_number text NOT NULL REFERENCES trademark_au.application(application_number),
    party_id bigint NOT NULL,
    party_role text NOT NULL,
    party_role_category text,
    party_type text,
    party_name text,
    abn text,
    country_code text,
    state_code text,
    postcode text,
    effective_from_date date,
    effective_to_date date,
    is_current boolean NOT NULL DEFAULT false,
    source_object_id uuid REFERENCES acquisition.global_trademark_source_object(object_id),
    PRIMARY KEY (application_number, party_id, party_role, effective_from_date)
);
CREATE TABLE trademark_au.application_link (
    application_number text NOT NULL REFERENCES trademark_au.application(application_number),
    link_type text NOT NULL,
    linked_application_number text NOT NULL,
    linked_application_country text,
    link_date date,
    source_object_id uuid REFERENCES acquisition.global_trademark_source_object(object_id),
    PRIMARY KEY (application_number, link_type, linked_application_number)
);
CREATE TABLE trademark_au.application_event (
    application_number text NOT NULL REFERENCES trademark_au.application(application_number),
    event_type text NOT NULL,
    event_category text,
    event_effective_date date,
    event_declared_date date,
    is_standing boolean,
    source_object_id uuid REFERENCES acquisition.global_trademark_source_object(object_id),
    PRIMARY KEY (application_number, event_type, event_effective_date, event_declared_date)
);
CREATE TABLE trademark_au.application_classification (
    application_number text NOT NULL REFERENCES trademark_au.application(application_number),
    classification_system text NOT NULL,
    classification text NOT NULL,
    classification_importance text,
    classification_inventiveness text,
    classification_source text,
    classification_date date,
    classification_removal_date date,
    is_current boolean,
    source_object_id uuid REFERENCES acquisition.global_trademark_source_object(object_id),
    PRIMARY KEY (application_number, classification_system, classification, classification_date)
);
CREATE TABLE trademark_au.application_description (
    application_number text NOT NULL REFERENCES trademark_au.application(application_number),
    description_type text NOT NULL,
    description_value text NOT NULL,
    source_object_id uuid REFERENCES acquisition.global_trademark_source_object(object_id),
    PRIMARY KEY (application_number, description_type, description_value)
);

CREATE SCHEMA IF NOT EXISTS trademark_ca;
CREATE TABLE trademark_ca.st96_record (
    application_number text PRIMARY KEY,
    extension_counter text NOT NULL DEFAULT '00',
    registration_number text,
    international_registration_number text,
    mark_text text,
    mark_category text,
    source_status text,
    status_date date,
    filed_date date,
    registered_date date,
    expiry_date date,
    termination_date date,
    application_language text,
    source_object_id uuid REFERENCES acquisition.global_trademark_source_object(object_id),
    source_payload jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE trademark_ca.party (
    application_number text NOT NULL REFERENCES trademark_ca.st96_record(application_number),
    party_role text NOT NULL,
    party_name text NOT NULL,
    address_country text,
    source_object_id uuid REFERENCES acquisition.global_trademark_source_object(object_id),
    PRIMARY KEY (application_number, party_role, party_name)
);
CREATE TABLE trademark_ca.goods_service (
    application_number text NOT NULL REFERENCES trademark_ca.st96_record(application_number),
    class_number smallint,
    text_value text NOT NULL,
    language_code text,
    source_object_id uuid REFERENCES acquisition.global_trademark_source_object(object_id),
    PRIMARY KEY (application_number, class_number, text_value)
);
CREATE TABLE trademark_ca.event (
    application_number text NOT NULL REFERENCES trademark_ca.st96_record(application_number),
    event_code text NOT NULL,
    event_date date,
    event_text text,
    source_object_id uuid REFERENCES acquisition.global_trademark_source_object(object_id),
    PRIMARY KEY (application_number, event_code, event_date)
);
CREATE TABLE trademark_ca.relationship (
    application_number text NOT NULL REFERENCES trademark_ca.st96_record(application_number),
    relationship_type text NOT NULL,
    related_application_number text NOT NULL,
    source_object_id uuid REFERENCES acquisition.global_trademark_source_object(object_id),
    PRIMARY KEY (application_number, relationship_type, related_application_number)
);
CREATE TABLE trademark_ca.asset (
    application_number text NOT NULL REFERENCES trademark_ca.st96_record(application_number),
    asset_type text NOT NULL,
    object_key text NOT NULL,
    sha256 text NOT NULL,
    source_object_id uuid REFERENCES acquisition.global_trademark_source_object(object_id),
    PRIMARY KEY (application_number, asset_type, sha256)
);
"""


def _primary_key_columns(cur, schema: str, table: str) -> list[str]:
    cur.execute(
        """
        SELECT a.attname
        FROM pg_constraint c
        JOIN unnest(c.conkey) WITH ORDINALITY AS keys(attnum, ordinality) ON true
        JOIN pg_attribute a
          ON a.attrelid = c.conrelid
         AND a.attnum = keys.attnum
        WHERE c.conrelid = to_regclass(%s)
          AND c.contype = 'p'
        ORDER BY keys.ordinality
        """,
        (f"{schema}.{table}",),
    )
    return [row["attname"] for row in cur.fetchall()]


def _assert_upgrade_shape() -> None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            for table in (
                "party_activity",
                "application_link",
                "application_event",
                "application_classification",
                "application_description",
            ):
                assert _primary_key_columns(cur, "trademark_au", table) == ["source_row_hash"]

            cur.execute(
                """
                SELECT column_name, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'trademark_au'
                  AND table_name IN ('application_classification', 'application_description')
                  AND column_name IN (
                      'classification_system', 'classification',
                      'description_type', 'description_value'
                  )
                """
            )
            assert {row["is_nullable"] for row in cur.fetchall()} == {"YES"}

            assert _primary_key_columns(cur, "trademark_ca", "st96_record") == ["record_key"]
            for table in ("party", "goods_service", "event", "relationship", "asset"):
                assert _primary_key_columns(cur, "trademark_ca", table) == ["source_row_hash"]
                cur.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM information_schema.columns
                    WHERE table_schema = 'trademark_ca'
                      AND table_name = %s
                      AND column_name IN ('source_row_hash', 'record_key')
                    """,
                    (table,),
                )
                assert cur.fetchone()["count"] == 2

            cur.execute(
                """
                SELECT COUNT(*) AS count
                FROM pg_constraint c
                WHERE c.conrelid = 'trademark_ca.st96_record'::regclass
                  AND c.contype = 'u'
                  AND pg_get_constraintdef(c.oid) = 'UNIQUE (application_number, extension_counter)'
                """
            )
            assert cur.fetchone()["count"] == 1


def main() -> int:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(LEGACY_FIXTURE_SQL)
        conn.commit()

    ensure_seed_ingest_schema()
    ensure_seed_ingest_schema()
    _assert_upgrade_shape()

    assert validate_ingest_fixture() == 0
    print(
        {
            "status": "PASS",
            "legacy_schema_upgrade": True,
            "unexpected_legacy_rows_fail_closed": True,
            "post_upgrade_ingest_fixture": True,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
