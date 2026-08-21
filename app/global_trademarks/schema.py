from textwrap import dedent

from app.db import postgres_conn


SCHEMA_SQL = dedent(
    """
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

    CREATE SCHEMA IF NOT EXISTS trademark_gb;
    CREATE TABLE IF NOT EXISTS trademark_gb.historical_record (
        application_number text PRIMARY KEY,
        mark_text text,
        applicant_name text,
        postcode text,
        region text,
        country text,
        source_status text,
        mark_category text,
        mark_type text,
        series text,
        series_count integer,
        filed_date date,
        published_date date,
        registered_date date,
        expired_date date,
        renewal_due_date date,
        nice_classes smallint[] NOT NULL DEFAULT '{}',
        source_stream text NOT NULL,
        source_object_id uuid REFERENCES acquisition.global_trademark_source_object(object_id),
        current_state_verified boolean NOT NULL DEFAULT false,
        source_payload jsonb NOT NULL DEFAULT '{}'::jsonb
    );
    CREATE TABLE IF NOT EXISTS trademark_gb.weekly_observation (
        observation_id uuid PRIMARY KEY,
        application_number text NOT NULL,
        publication_week text NOT NULL,
        event_type text,
        observed_at timestamptz NOT NULL DEFAULT now(),
        source_object_id uuid NOT NULL REFERENCES acquisition.global_trademark_source_object(object_id),
        payload jsonb NOT NULL,
        UNIQUE (application_number, publication_week, source_object_id)
    );
    CREATE TABLE IF NOT EXISTS trademark_gb.comparable_relationship (
        uk_application_number text PRIMARY KEY,
        source_office text NOT NULL,
        source_application_number text NOT NULL,
        relationship_type text NOT NULL,
        effective_date date,
        verified_by_ukipo boolean NOT NULL DEFAULT false,
        source_object_id uuid REFERENCES acquisition.global_trademark_source_object(object_id)
    );

    CREATE SCHEMA IF NOT EXISTS trademark_eu;
    CREATE TABLE IF NOT EXISTS trademark_eu.tm_link_seed (
        application_number text PRIMARY KEY,
        mark_text text,
        applicant_name text,
        applicant_country text,
        source_status text,
        filed_date date,
        registered_date date,
        renewal_due_date date,
        nice_classes smallint[] NOT NULL DEFAULT '{}',
        source_object_id uuid REFERENCES acquisition.global_trademark_source_object(object_id),
        current_state_verified boolean NOT NULL DEFAULT false,
        source_payload jsonb NOT NULL DEFAULT '{}'::jsonb
    );
    CREATE TABLE IF NOT EXISTS trademark_eu.api_observation (
        observation_id uuid PRIMARY KEY,
        application_number text NOT NULL,
        observed_at timestamptz NOT NULL DEFAULT now(),
        source_object_id uuid REFERENCES acquisition.global_trademark_source_object(object_id),
        payload jsonb NOT NULL
    );

    CREATE SCHEMA IF NOT EXISTS trademark_nz;
    CREATE TABLE IF NOT EXISTS trademark_nz.tm_link_seed (
        application_number text PRIMARY KEY,
        madrid_number text,
        mark_text text,
        applicant_name text,
        filed_date date,
        registered_date date,
        nice_classes smallint[] NOT NULL DEFAULT '{}',
        source_object_id uuid REFERENCES acquisition.global_trademark_source_object(object_id),
        current_state_verified boolean NOT NULL DEFAULT false,
        source_payload jsonb NOT NULL DEFAULT '{}'::jsonb
    );
    CREATE TABLE IF NOT EXISTS trademark_nz.api_observation (
        observation_id uuid PRIMARY KEY,
        application_number text NOT NULL,
        observed_at timestamptz NOT NULL DEFAULT now(),
        source_object_id uuid REFERENCES acquisition.global_trademark_source_object(object_id),
        payload jsonb NOT NULL
    );

    CREATE SCHEMA IF NOT EXISTS trademark_au;
    CREATE TABLE IF NOT EXISTS trademark_au.application (
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
    CREATE TABLE IF NOT EXISTS trademark_au.party_activity (
        source_row_hash text PRIMARY KEY,
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
        source_object_id uuid REFERENCES acquisition.global_trademark_source_object(object_id)
    );
    CREATE INDEX IF NOT EXISTS idx_trademark_au_party_activity_application
        ON trademark_au.party_activity (application_number);
    CREATE TABLE IF NOT EXISTS trademark_au.application_link (
        source_row_hash text PRIMARY KEY,
        application_number text NOT NULL REFERENCES trademark_au.application(application_number),
        link_type text NOT NULL,
        linked_application_number text NOT NULL,
        linked_application_country text,
        link_date date,
        source_object_id uuid REFERENCES acquisition.global_trademark_source_object(object_id)
    );
    CREATE INDEX IF NOT EXISTS idx_trademark_au_application_link_application
        ON trademark_au.application_link (application_number);
    CREATE TABLE IF NOT EXISTS trademark_au.application_event (
        source_row_hash text PRIMARY KEY,
        application_number text NOT NULL REFERENCES trademark_au.application(application_number),
        event_type text NOT NULL,
        event_category text,
        event_effective_date date,
        event_declared_date date,
        is_standing boolean,
        source_object_id uuid REFERENCES acquisition.global_trademark_source_object(object_id)
    );
    CREATE INDEX IF NOT EXISTS idx_trademark_au_application_event_application
        ON trademark_au.application_event (application_number);
    CREATE TABLE IF NOT EXISTS trademark_au.application_classification (
        source_row_hash text PRIMARY KEY,
        application_number text NOT NULL REFERENCES trademark_au.application(application_number),
        classification_system text,
        classification text,
        classification_importance text,
        classification_inventiveness text,
        classification_source text,
        classification_date date,
        classification_removal_date date,
        is_current boolean,
        source_object_id uuid REFERENCES acquisition.global_trademark_source_object(object_id)
    );
    CREATE INDEX IF NOT EXISTS idx_trademark_au_application_classification_application
        ON trademark_au.application_classification (application_number);
    CREATE TABLE IF NOT EXISTS trademark_au.application_description (
        source_row_hash text PRIMARY KEY,
        application_number text NOT NULL REFERENCES trademark_au.application(application_number),
        description_type text,
        description_value text,
        source_object_id uuid REFERENCES acquisition.global_trademark_source_object(object_id)
    );
    CREATE INDEX IF NOT EXISTS idx_trademark_au_application_description_application
        ON trademark_au.application_description (application_number);

    CREATE SCHEMA IF NOT EXISTS trademark_ca;
    CREATE TABLE IF NOT EXISTS trademark_ca.st96_record (
        record_key text PRIMARY KEY,
        application_number text NOT NULL,
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
        source_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
        UNIQUE (application_number, extension_counter)
    );
    CREATE INDEX IF NOT EXISTS idx_trademark_ca_st96_application
        ON trademark_ca.st96_record (application_number);
    CREATE TABLE IF NOT EXISTS trademark_ca.party (
        source_row_hash text PRIMARY KEY,
        record_key text NOT NULL REFERENCES trademark_ca.st96_record(record_key),
        application_number text NOT NULL,
        party_role text NOT NULL,
        party_name text NOT NULL,
        address_country text,
        source_object_id uuid REFERENCES acquisition.global_trademark_source_object(object_id)
    );
    CREATE INDEX IF NOT EXISTS idx_trademark_ca_party_application
        ON trademark_ca.party (application_number);
    CREATE TABLE IF NOT EXISTS trademark_ca.goods_service (
        source_row_hash text PRIMARY KEY,
        record_key text NOT NULL REFERENCES trademark_ca.st96_record(record_key),
        application_number text NOT NULL,
        class_number smallint,
        text_value text NOT NULL,
        language_code text,
        source_object_id uuid REFERENCES acquisition.global_trademark_source_object(object_id)
    );
    CREATE INDEX IF NOT EXISTS idx_trademark_ca_goods_service_application
        ON trademark_ca.goods_service (application_number);
    CREATE TABLE IF NOT EXISTS trademark_ca.event (
        source_row_hash text PRIMARY KEY,
        record_key text NOT NULL REFERENCES trademark_ca.st96_record(record_key),
        application_number text NOT NULL,
        event_code text NOT NULL,
        event_date date,
        event_text text,
        source_object_id uuid REFERENCES acquisition.global_trademark_source_object(object_id)
    );
    CREATE INDEX IF NOT EXISTS idx_trademark_ca_event_application
        ON trademark_ca.event (application_number);
    CREATE TABLE IF NOT EXISTS trademark_ca.relationship (
        source_row_hash text PRIMARY KEY,
        record_key text NOT NULL REFERENCES trademark_ca.st96_record(record_key),
        application_number text NOT NULL,
        relationship_type text NOT NULL,
        related_application_number text NOT NULL,
        source_object_id uuid REFERENCES acquisition.global_trademark_source_object(object_id)
    );
    CREATE INDEX IF NOT EXISTS idx_trademark_ca_relationship_application
        ON trademark_ca.relationship (application_number);
    CREATE TABLE IF NOT EXISTS trademark_ca.asset (
        source_row_hash text PRIMARY KEY,
        record_key text NOT NULL REFERENCES trademark_ca.st96_record(record_key),
        application_number text NOT NULL,
        asset_type text NOT NULL,
        object_key text NOT NULL,
        sha256 text NOT NULL,
        source_object_id uuid REFERENCES acquisition.global_trademark_source_object(object_id)
    );
    CREATE INDEX IF NOT EXISTS idx_trademark_ca_asset_application
        ON trademark_ca.asset (application_number);
    """
).strip()


def ensure_country_trademark_schemas() -> None:
    """Install additive country-native schemas without touching existing CN/US stores."""
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()
