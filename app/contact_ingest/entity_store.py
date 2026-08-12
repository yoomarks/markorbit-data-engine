from __future__ import annotations

import hashlib
import uuid
from typing import Any

from app.contact_ingest.models import EntityPlan
from app.contact_ingest.normalization import normalized_match_text


CONTACT_ENTITY_NAMESPACE = uuid.UUID("8e10d6f7-e4e5-44b4-88a4-3a54a76644ae")


def _entity_candidates_by_name(
    cur, normalized_name: str, normalized_address: str
) -> list[dict[str, Any]]:
    if normalized_address:
        cur.execute(
            """
            SELECT entity_id, entity_type, normalized_address
            FROM entity.entity
            WHERE normalized_name = %s AND normalized_address = %s
            ORDER BY confidence_score DESC NULLS LAST, first_seen_at
            LIMIT 2
            """,
            (normalized_name, normalized_address),
        )
        rows = list(cur.fetchall())
        if rows:
            return rows
    cur.execute(
        """
        SELECT entity_id, entity_type, normalized_address
        FROM entity.entity
        WHERE normalized_name = %s
        ORDER BY confidence_score DESC NULLS LAST, first_seen_at
        LIMIT 2
        """,
        (normalized_name,),
    )
    return list(cur.fetchall())


def _find_by_identifier(
    cur, identifiers: dict[str, str], country_code: str
) -> dict[str, Any] | None:
    for identifier_type, value in identifiers.items():
        cur.execute(
            """
            SELECT e.entity_id, e.entity_type
            FROM entity.entity_identifier i
            JOIN entity.entity e ON e.entity_id = i.entity_id
            WHERE i.identifier_type = %s
              AND i.normalized_value = %s
              AND COALESCE(i.country_code, '') = %s
            LIMIT 1
            """,
            (identifier_type, value, country_code),
        )
        row = cur.fetchone()
        if row:
            return dict(row)
    return None


def _contact_entity_material(entity: EntityPlan, profile: str) -> str:
    if entity.identifiers:
        identifier_type, value = sorted(entity.identifiers.items())[0]
        return f"CONTACT|{identifier_type}|{entity.country_code}|{value}"
    return (
        f"CONTACT|{profile}|{entity.country_code}|{entity.normalized_name}|"
        f"{entity.normalized_address}"
    )


def _create_or_update_entity(
    cur, entity: EntityPlan, profile: str
) -> tuple[str, str, float, bool]:
    identifier_match = _find_by_identifier(cur, entity.identifiers, entity.country_code)
    if identifier_match:
        entity_id = str(identifier_match["entity_id"])
        method, confidence, created = "CONTACT_IDENTIFIER_MATCH", 0.995, False
    else:
        candidates = _entity_candidates_by_name(
            cur, entity.normalized_name, entity.normalized_address
        )
        if len(candidates) == 1:
            entity_id = str(candidates[0]["entity_id"])
            exact_address = (
                bool(entity.normalized_address)
                and candidates[0]["normalized_address"] == entity.normalized_address
            )
            method = (
                "CONTACT_EXACT_NAME_ADDRESS"
                if exact_address
                else "CONTACT_UNIQUE_EXACT_NAME"
            )
            confidence = 0.98 if exact_address else 0.94
            created = False
        else:
            ambiguous = len(candidates) > 1
            method = (
                "CONTACT_SOURCE_NEW_AMBIGUOUS"
                if ambiguous
                else "CONTACT_SOURCE_NEW"
            )
            confidence = 0.75 if ambiguous else 0.90
            material = _contact_entity_material(entity, profile)
            entity_uuid = uuid.uuid5(CONTACT_ENTITY_NAMESPACE, material)
            entity_id = str(entity_uuid)
            entity_key = hashlib.sha256(material.encode("utf-8")).hexdigest()
            entity_type = entity.entity_type_hint or (
                "AGENT_FIRM" if profile == "AGENT_CONTACT_LIST" else "ORGANIZATION"
            )
            cur.execute("SELECT 1 FROM entity.entity WHERE entity_id = %s", (entity_id,))
            existed = cur.fetchone() is not None
            cur.execute(
                """
                INSERT INTO entity.entity AS current_entity (
                    entity_id, entity_key, entity_type, canonical_name,
                    normalized_name, normalized_address, country_code,
                    region_code, city, status, resolution_method,
                    source_primary, confidence_score
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, NULLIF(%s, ''), NULLIF(%s, ''),
                    NULLIF(%s, ''), 'CANDIDATE', %s,
                    'CONTACT_INGEST', %s
                )
                ON CONFLICT (entity_id)
                DO UPDATE SET
                    canonical_name = CASE
                        WHEN length(EXCLUDED.canonical_name) > length(current_entity.canonical_name)
                        THEN EXCLUDED.canonical_name ELSE current_entity.canonical_name END,
                    normalized_address = CASE
                        WHEN current_entity.normalized_address = '' THEN EXCLUDED.normalized_address
                        ELSE current_entity.normalized_address END,
                    country_code = COALESCE(current_entity.country_code, EXCLUDED.country_code),
                    region_code = COALESCE(current_entity.region_code, EXCLUDED.region_code),
                    city = COALESCE(current_entity.city, EXCLUDED.city),
                    updated_at = now()
                """,
                (
                    entity_id,
                    entity_key,
                    entity_type,
                    entity.canonical_name,
                    entity.normalized_name,
                    entity.normalized_address,
                    entity.country_code,
                    entity.region_code,
                    entity.city,
                    method,
                    confidence,
                ),
            )
            created = not existed

    # External contact data can fill missing geo/name presentation but never
    # overwrite the official identity fields already populated by trademark data.
    cur.execute(
        """
        UPDATE entity.entity
        SET country_code = COALESCE(country_code, NULLIF(%s, '')),
            region_code = COALESCE(region_code, NULLIF(%s, '')),
            city = COALESCE(city, NULLIF(%s, '')),
            updated_at = now()
        WHERE entity_id = %s
        """,
        (entity.country_code, entity.region_code, entity.city, entity_id),
    )

    aliases = [(entity.canonical_name, "")] + entity.aliases
    for alias, language in aliases:
        normalized = normalized_match_text(alias)
        if not normalized:
            continue
        cur.execute(
            """
            INSERT INTO entity.entity_alias AS current_alias (
                entity_id, alias_name, normalized_name, language_code, source, confidence_score
            )
            VALUES (%s, %s, %s, NULLIF(%s, ''), 'CONTACT_INGEST', 0.9000)
            ON CONFLICT (entity_id, normalized_name, source)
            DO UPDATE SET alias_name = EXCLUDED.alias_name,
                          language_code = COALESCE(current_alias.language_code, EXCLUDED.language_code),
                          confidence_score = GREATEST(current_alias.confidence_score, EXCLUDED.confidence_score)
            """,
            (entity_id, alias, normalized, language),
        )

    for identifier_type, value in entity.identifiers.items():
        cur.execute(
            """
            INSERT INTO entity.entity_identifier (
                entity_id, identifier_type, identifier_value, normalized_value,
                country_code, source, confidence_score
            )
            VALUES (%s, %s, %s, %s, NULLIF(%s, ''), 'CONTACT_INGEST', 0.9900)
            ON CONFLICT DO NOTHING
            """,
            (entity_id, identifier_type, value, value, entity.country_code),
        )
        cur.execute(
            """
            UPDATE entity.entity_identifier
            SET last_seen_at = now()
            WHERE identifier_type = %s AND normalized_value = %s
              AND COALESCE(country_code, '') = %s AND entity_id = %s
            """,
            (identifier_type, value, entity.country_code, entity_id),
        )

    return entity_id, method, confidence, created


def _link_trademark_mentions(
    cur, entity_id: str, entity: EntityPlan, method: str
) -> int:
    if not entity.normalized_name or method == "CONTACT_SOURCE_NEW_AMBIGUOUS":
        return 0
    params: list[Any] = [entity_id, method, entity.normalized_name]
    jurisdiction_clause = ""
    if entity.country_code == "CN":
        jurisdiction_clause = "AND jurisdiction = 'CN'"
    # Exact legal/firm name is the only automatic contact-to-trademark bridge.
    # Existing links are never reassigned. Address is deliberately not required:
    # registry and QCC addresses can represent different observation dates.
    cur.execute(
        f"""
        UPDATE entity.entity_mention
        SET entity_id = %s,
            match_status = 'MATCHED',
            resolution_method = %s,
            last_seen_at = now()
        WHERE entity_id IS NULL
          AND normalized_name = %s
          {jurisdiction_clause}
        """,
        params,
    )
    return int(cur.rowcount or 0)
