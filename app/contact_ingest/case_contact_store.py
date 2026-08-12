from __future__ import annotations

import json
from typing import Any

from app.contact_ingest.models import CaseContactPlan, ChannelPlan, ImportPlan
from app.contact_ingest.normalization import sha256_text


CASE_CONTACT_EVIDENCE_VERSION = "CONTACT_CASE_EVIDENCE_V1"

CASE_CONTACT_SCHEMA_SQL = r"""
CREATE SCHEMA IF NOT EXISTS contact;

CREATE TABLE IF NOT EXISTS contact.case_contact_observation (
    observation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    observation_key char(64) NOT NULL UNIQUE,
    source_id uuid NOT NULL REFERENCES contact.source(source_id) ON DELETE CASCADE,
    raw_record_id uuid REFERENCES contact.raw_record(raw_record_id) ON DELETE SET NULL,
    jurisdiction char(2),
    application_number text NOT NULL DEFAULT '',
    registration_number text NOT NULL DEFAULT '',
    channel_type text NOT NULL,
    raw_value text NOT NULL,
    normalized_value text NOT NULL,
    source_column text NOT NULL DEFAULT '',
    owner_assignment_status text NOT NULL DEFAULT 'UNRESOLVED',
    confidence_score numeric(5,4) NOT NULL DEFAULT 0.7500,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    observed_at timestamptz NOT NULL DEFAULT now(),
    CHECK (application_number <> '' OR registration_number <> ''),
    CHECK (owner_assignment_status = 'UNRESOLVED')
);
CREATE INDEX IF NOT EXISTS ix_contact_case_observation_application
ON contact.case_contact_observation(application_number)
WHERE application_number <> '';
CREATE INDEX IF NOT EXISTS ix_contact_case_observation_registration
ON contact.case_contact_observation(registration_number)
WHERE registration_number <> '';
CREATE INDEX IF NOT EXISTS ix_contact_case_observation_channel
ON contact.case_contact_observation(channel_type, normalized_value);
"""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _apply_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(CASE_CONTACT_SCHEMA_SQL)
        cur.execute(
            """
            INSERT INTO control.schema_version(component, version)
            VALUES ('CONTACT_CASE_EVIDENCE', %s)
            ON CONFLICT (component)
            DO UPDATE SET version = EXCLUDED.version, applied_at = now()
            """,
            (CASE_CONTACT_EVIDENCE_VERSION,),
        )


def ensure_case_contact_schema(conn=None) -> None:
    if conn is not None:
        _apply_schema(conn)
        return
    from app.db import postgres_conn

    with postgres_conn() as owned_conn:
        _apply_schema(owned_conn)
        owned_conn.commit()


def upsert_unresolved_raw_record(
    cur,
    *,
    source_id: str,
    source_member: str,
    sheet_name: str,
    profile: str,
    record: CaseContactPlan,
) -> str:
    cur.execute(
        """
        INSERT INTO contact.raw_record (
            source_id, source_member, sheet_name, source_row, source_profile,
            entity_id, entity_match_method, entity_match_confidence, raw_data
        )
        VALUES (%s, %s, %s, %s, %s, NULL, 'UNRESOLVED_CASE_CONTACT', NULL, %s::jsonb)
        ON CONFLICT (source_id, source_member, sheet_name, source_row)
        DO UPDATE SET
            source_profile = EXCLUDED.source_profile,
            entity_id = NULL,
            entity_match_method = EXCLUDED.entity_match_method,
            entity_match_confidence = NULL,
            raw_data = EXCLUDED.raw_data,
            updated_at = now()
        RETURNING raw_record_id
        """,
        (
            source_id,
            source_member,
            sheet_name,
            record.source_row,
            profile,
            _json(record.raw_record),
        ),
    )
    return str(cur.fetchone()["raw_record_id"])


def observe_case_contact(
    cur,
    *,
    plan: ImportPlan,
    source_id: str,
    raw_record_id: str,
    source_member: str,
    sheet_name: str,
    record: CaseContactPlan,
    channel: ChannelPlan,
) -> None:
    observation_key = sha256_text(
        source_id,
        source_member,
        sheet_name,
        record.source_row,
        record.application_number,
        record.registration_number,
        channel.source_column,
        channel.channel_type,
        channel.normalized_value,
    )
    cur.execute(
        """
        INSERT INTO contact.case_contact_observation (
            observation_key, source_id, raw_record_id, jurisdiction,
            application_number, registration_number, channel_type, raw_value,
            normalized_value, source_column, owner_assignment_status,
            confidence_score, metadata
        )
        VALUES (%s, %s, %s, NULLIF(%s, ''), %s, %s, %s, %s, %s, %s,
                'UNRESOLVED', 0.7500, %s::jsonb)
        ON CONFLICT (observation_key)
        DO UPDATE SET
            raw_record_id = EXCLUDED.raw_record_id,
            raw_value = EXCLUDED.raw_value,
            metadata = EXCLUDED.metadata
        """,
        (
            observation_key,
            source_id,
            raw_record_id,
            record.jurisdiction,
            record.application_number,
            record.registration_number,
            channel.channel_type,
            channel.raw_value,
            channel.normalized_value,
            channel.source_column,
            _json({
                "source_member": source_member,
                "sheet_name": sheet_name,
                "ingest_version": plan.version,
                "ownership": "SOURCE_DOES_NOT_NAME_CHANNEL_OWNER",
            }),
        ),
    )
