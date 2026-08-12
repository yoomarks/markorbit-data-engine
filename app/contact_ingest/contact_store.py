from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from app.contact_ingest import CONTACT_INGEST_VERSION
from app.contact_ingest.models import ChannelPlan, EntityPlan, ImportPlan, PersonPlan
from app.contact_ingest.normalization import sha256_text


CONTACT_PERSON_NAMESPACE = uuid.UUID("49422525-2eb6-4b9d-9794-4ffdf852b776")
CONTACT_RELATION_NAMESPACE = uuid.UUID("2dc98aeb-64e2-475a-aa0a-4442ef62255b")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _source_profile(plan: ImportPlan) -> str:
    profiles = sorted({table.profile for table in plan.tables})
    return profiles[0] if len(profiles) == 1 else "MIXED"


def _upsert_source(cur, plan: ImportPlan) -> str:
    cur.execute(
        """
        INSERT INTO contact.source (
            source_sha256, source_name, file_type, source_profile, ingest_version
        )
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (source_sha256)
        DO UPDATE SET
            source_name = EXCLUDED.source_name,
            file_type = EXCLUDED.file_type,
            source_profile = EXCLUDED.source_profile,
            ingest_version = EXCLUDED.ingest_version,
            last_seen_at = now()
        RETURNING source_id
        """,
        (
            plan.source_sha256,
            plan.source_name,
            plan.file_type,
            _source_profile(plan),
            CONTACT_INGEST_VERSION,
        ),
    )
    return str(cur.fetchone()["source_id"])


def _create_run(cur, source_id: str) -> str:
    cur.execute(
        """
        INSERT INTO contact.import_run(source_id, status, apply_mode)
        VALUES (%s, 'RUNNING', true)
        RETURNING run_id
        """,
        (source_id,),
    )
    return str(cur.fetchone()["run_id"])


def _upsert_raw_record(
    cur,
    *,
    source_id: str,
    source_member: str,
    sheet_name: str,
    profile: str,
    entity: EntityPlan,
    entity_id: str,
    match_method: str,
    match_confidence: float,
) -> str:
    cur.execute(
        """
        INSERT INTO contact.raw_record (
            source_id, source_member, sheet_name, source_row, source_profile,
            entity_id, entity_match_method, entity_match_confidence, raw_data
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        ON CONFLICT (source_id, source_member, sheet_name, source_row)
        DO UPDATE SET
            source_profile = EXCLUDED.source_profile,
            entity_id = EXCLUDED.entity_id,
            entity_match_method = EXCLUDED.entity_match_method,
            entity_match_confidence = EXCLUDED.entity_match_confidence,
            raw_data = EXCLUDED.raw_data,
            updated_at = now()
        RETURNING raw_record_id
        """,
        (
            source_id,
            source_member,
            sheet_name,
            entity.source_row,
            profile,
            entity_id,
            match_method,
            match_confidence,
            _json(entity.raw_record),
        ),
    )
    return str(cur.fetchone()["raw_record_id"])


def _upsert_person(cur, *, entity_id: str, person: PersonPlan, source_id: str, country_code: str) -> str:
    material = f"{entity_id}|{person.normalized_name}"
    person_id = str(uuid.uuid5(CONTACT_PERSON_NAMESPACE, material))
    person_key = hashlib.sha256(material.encode("utf-8")).hexdigest()
    cur.execute(
        """
        INSERT INTO contact.person AS current_person (
            person_id, person_key, canonical_name, normalized_name, country_code
        )
        VALUES (%s, %s, %s, %s, NULLIF(%s, ''))
        ON CONFLICT (person_id)
        DO UPDATE SET
            canonical_name = CASE
                WHEN length(EXCLUDED.canonical_name) > length(current_person.canonical_name)
                THEN EXCLUDED.canonical_name ELSE current_person.canonical_name END,
            country_code = COALESCE(current_person.country_code, EXCLUDED.country_code),
            updated_at = now()
        """,
        (person_id, person_key, person.full_name, person.normalized_name, country_code),
    )
    relation_material = f"{entity_id}|{person_id}|{person.relation_type}"
    relation_id = str(uuid.uuid5(CONTACT_RELATION_NAMESPACE, relation_material))
    cur.execute(
        """
        INSERT INTO contact.entity_person_relation AS current_relation (
            relation_id, entity_id, person_id, relation_type, title, department,
            confidence_score, first_source_id, last_source_id
        )
        VALUES (%s, %s, %s, %s, %s, %s, 0.9000, %s, %s)
        ON CONFLICT (entity_id, person_id, relation_type)
        DO UPDATE SET
            title = CASE WHEN EXCLUDED.title <> '' THEN EXCLUDED.title ELSE current_relation.title END,
            department = CASE WHEN EXCLUDED.department <> '' THEN EXCLUDED.department ELSE current_relation.department END,
            last_source_id = EXCLUDED.last_source_id,
            last_seen_at = now()
        """,
        (
            relation_id, entity_id, person_id, person.relation_type,
            person.title, person.department, source_id, source_id,
        ),
    )
    return person_id


def _upsert_channel(cur, *, entity_id: str | None, person_id: str | None, channel: ChannelPlan) -> str:
    if bool(entity_id) == bool(person_id):
        raise ValueError("Exactly one channel owner must be supplied")
    if entity_id:
        cur.execute(
            """
            INSERT INTO contact.channel (
                entity_id, channel_type, channel_value, normalized_value
            )
            VALUES (%s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            RETURNING channel_id
            """,
            (entity_id, channel.channel_type, channel.raw_value, channel.normalized_value),
        )
        row = cur.fetchone()
        if not row:
            cur.execute(
                """
                UPDATE contact.channel SET last_seen_at = now()
                WHERE entity_id = %s AND channel_type = %s AND normalized_value = %s
                RETURNING channel_id
                """,
                (entity_id, channel.channel_type, channel.normalized_value),
            )
            row = cur.fetchone()
    else:
        cur.execute(
            """
            INSERT INTO contact.channel (
                person_id, channel_type, channel_value, normalized_value
            )
            VALUES (%s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            RETURNING channel_id
            """,
            (person_id, channel.channel_type, channel.raw_value, channel.normalized_value),
        )
        row = cur.fetchone()
        if not row:
            cur.execute(
                """
                UPDATE contact.channel SET last_seen_at = now()
                WHERE person_id = %s AND channel_type = %s AND normalized_value = %s
                RETURNING channel_id
                """,
                (person_id, channel.channel_type, channel.normalized_value),
            )
            row = cur.fetchone()
    if not row:
        raise RuntimeError("Failed to resolve contact channel after upsert")
    return str(row["channel_id"])


def _observe_channel(
    cur,
    *,
    channel_id: str,
    source_id: str,
    raw_record_id: str,
    source_member: str,
    sheet_name: str,
    channel: ChannelPlan,
) -> None:
    observation_key = sha256_text(
        source_id, source_member, sheet_name, channel.source_row,
        channel.source_column, channel.channel_type, channel.normalized_value,
    )
    cur.execute(
        """
        INSERT INTO contact.channel_observation (
            observation_key, channel_id, source_id, raw_record_id,
            source_column, raw_value, confidence_score, metadata
        )
        VALUES (%s, %s, %s, %s, %s, %s, 0.9000, %s::jsonb)
        ON CONFLICT (observation_key) DO NOTHING
        """,
        (
            observation_key,
            channel_id,
            source_id,
            raw_record_id,
            channel.source_column,
            channel.raw_value,
            _json({"source_member": source_member, "sheet_name": sheet_name}),
        ),
    )
