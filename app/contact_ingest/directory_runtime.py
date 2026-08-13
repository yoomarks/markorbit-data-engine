from __future__ import annotations

from typing import Any

from app.contact_ingest.directory_api import _PAGE_HYDRATION_SQL
from app.db import postgres_conn


# The directory must stay contact-index driven. The historical rollup grouped the
# entire trademark mention corpus before LIMIT/OFFSET, so CN replay growth could
# make a read-only Contacts page exhaust PostgreSQL resources. Page candidates are
# now selected from contact-owned tables first; trademark evidence is hydrated only
# for the requested entity ids.
_CONTACT_ENTITIES_CTE = r"""
WITH contact_entities AS (
    SELECT entity_id FROM contact.raw_record WHERE entity_id IS NOT NULL
    UNION
    SELECT entity_id FROM contact.entity_person_relation
    UNION
    SELECT entity_id FROM contact.channel WHERE entity_id IS NOT NULL
),
contact_base AS (
    SELECT
        e.entity_id,
        e.canonical_name AS entity_name,
        e.entity_type,
        COALESCE(e.country_code, '') AS country_code,
        e.region_code,
        e.city,
        (
            e.entity_type IN ('AGENT_FIRM', 'AGENT_PERSON')
            OR EXISTS (
                SELECT 1
                FROM contact.raw_record AS rr
                JOIN contact.source AS s ON s.source_id = rr.source_id
                WHERE rr.entity_id = e.entity_id
                  AND (rr.source_profile = 'AGENT_CONTACT_LIST' OR s.source_segment = 'AGENT')
            )
            OR EXISTS (
                SELECT 1 FROM contact.entity_person_relation AS r
                WHERE r.entity_id = e.entity_id
                  AND r.relation_type IN ('ATTORNEY', 'AGENT', 'CORRESPONDENT')
            )
        ) AS is_agent,
        (
            e.entity_type = 'TRADEMARK_PARTY'
            OR EXISTS (
                SELECT 1
                FROM contact.raw_record AS rr
                JOIN contact.source AS s ON s.source_id = rr.source_id
                WHERE rr.entity_id = e.entity_id
                  AND (rr.source_profile = 'QCC_COMPANY_EXPORT' OR s.source_segment = 'DIRECT')
            )
        ) AS is_direct
    FROM contact_entities AS ce
    JOIN entity.entity AS e ON e.entity_id = ce.entity_id
)
"""


_PAGE_EVIDENCE_SQL = r"""
WITH requested AS (
    SELECT unnest(%s::uuid[]) AS entity_id
)
SELECT
    m.entity_id,
    count(*) FILTER (WHERE m.role IN ('OWNER', 'CO_OWNER', 'APPLICANT')) AS applicant_mentions,
    count(*) FILTER (WHERE m.role IN ('AGENT', 'ATTORNEY', 'CORRESPONDENT')) AS agent_mentions,
    CASE
        WHEN count(DISTINCT m.jurisdiction)
             FILTER (WHERE m.jurisdiction ~ '^[A-Z]{2}$') = 1
        THEN max(m.jurisdiction)
             FILTER (WHERE m.jurisdiction ~ '^[A-Z]{2}$')
        ELSE NULL
    END AS single_mention_country
FROM entity.entity_mention AS m
JOIN requested AS q ON q.entity_id = m.entity_id
GROUP BY m.entity_id
"""


def _channel_exists_sql(channel_types: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in channel_types)
    return (
        "EXISTS ("
        "SELECT 1 FROM contact.channel AS fc "
        "WHERE fc.channel_type IN (" + quoted + ") AND ("
        "fc.entity_id = contact_base.entity_id OR "
        "fc.person_id IN ("
        "SELECT fr.person_id FROM contact.entity_person_relation AS fr "
        "WHERE fr.entity_id = contact_base.entity_id"
        ")"
        ")"
        ")"
    )


def contact_directory_countries() -> dict[str, Any]:
    """Return the lightweight country selector without running full analytics."""
    sql = _CONTACT_ENTITIES_CTE + r"""
SELECT country_code, count(*) AS entities
FROM contact_base
GROUP BY country_code
ORDER BY country_code = '', entities DESC, country_code
"""
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = [dict(row) for row in cur.fetchall()]
    return {
        "countries": [
            {
                "country_code": str(row.get("country_code") or ""),
                "entities": int(row.get("entities") or 0),
            }
            for row in rows
        ]
    }


def contact_directory_list(
    *,
    country: str = "",
    segment: str = "",
    channel: str = "",
    query: str = "",
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Page contact entities before hydrating channels and trademark evidence."""
    clauses: list[str] = []
    params: list[Any] = []

    country = country.strip().upper()
    segment = segment.strip().upper()
    channel = channel.strip().upper()
    query = query.strip()

    if country == "__UNKNOWN__":
        clauses.append("country_code = ''")
    elif country:
        clauses.append("country_code = %s")
        params.append(country[:2])

    if segment == "AGENT":
        clauses.append("is_agent")
    elif segment == "DIRECT":
        clauses.append("is_direct")
    elif segment == "BOTH":
        clauses.append("is_agent AND is_direct")
    elif segment == "UNKNOWN":
        clauses.append("NOT is_agent AND NOT is_direct")

    if channel == "EMAIL":
        clauses.append(_channel_exists_sql(("EMAIL",)))
    elif channel == "PHONE":
        clauses.append(_channel_exists_sql(("PHONE", "MOBILE")))
    elif channel == "WEBSITE":
        clauses.append(_channel_exists_sql(("WEBSITE",)))
    elif channel == "WHATSAPP":
        clauses.append(_channel_exists_sql(("WHATSAPP",)))

    if query:
        clauses.append(
            "("
            "entity_name ILIKE %s OR COALESCE(city, '') ILIKE %s OR "
            "EXISTS ("
            "  SELECT 1 FROM contact.entity_person_relation AS qr "
            "  JOIN contact.person AS qp ON qp.person_id = qr.person_id "
            "  WHERE qr.entity_id = contact_base.entity_id AND qp.canonical_name ILIKE %s"
            ") OR EXISTS ("
            "  SELECT 1 FROM contact.channel AS qc "
            "  WHERE (qc.entity_id = contact_base.entity_id OR qc.person_id IN ("
            "      SELECT qpr.person_id FROM contact.entity_person_relation AS qpr "
            "      WHERE qpr.entity_id = contact_base.entity_id"
            "  )) AND qc.channel_value ILIKE %s"
            ")"
            ")"
        )
        pattern = f"%{query}%"
        params.extend([pattern] * 4)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    page_limit = max(1, min(int(limit), 500))
    page_offset = max(0, int(offset))
    sql = _CONTACT_ENTITIES_CTE + f"""
SELECT
    entity_id,
    entity_name,
    entity_type,
    country_code,
    region_code,
    city,
    is_agent,
    is_direct,
    count(*) OVER() AS filtered_total
FROM contact_base
{where}
ORDER BY country_code = '', country_code, lower(entity_name), entity_id
LIMIT %s OFFSET %s
"""
    params.extend([page_limit, page_offset])

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = [dict(row) for row in cur.fetchall()]
            entity_ids = [str(row["entity_id"]) for row in rows]
            hydrated: dict[str, dict[str, Any]] = {}
            evidence: dict[str, dict[str, Any]] = {}
            if entity_ids:
                cur.execute(_PAGE_HYDRATION_SQL, (entity_ids,))
                hydrated = {
                    str(item["entity_id"]): dict(item)
                    for item in cur.fetchall()
                }
                cur.execute(_PAGE_EVIDENCE_SQL, (entity_ids,))
                evidence = {
                    str(item["entity_id"]): dict(item)
                    for item in cur.fetchall()
                }

    total = int(rows[0].pop("filtered_total")) if rows else 0
    for row in rows:
        row.pop("filtered_total", None)
        key = str(row["entity_id"])
        details = hydrated.get(key, {})
        proof = evidence.get(key, {})

        for field in (
            "people",
            "phones",
            "emails",
            "websites",
            "whatsapps",
            "source_profiles",
            "source_segments",
            "source_scopes",
        ):
            row[field] = list(details.get(field) or [])

        row["person_count"] = len(row["people"])
        row["phone_count"] = len(row["phones"])
        row["email_count"] = len(row["emails"])
        row["website_count"] = len(row["websites"])
        row["whatsapp_count"] = len(row["whatsapps"])
        row["applicant_mentions"] = int(proof.get("applicant_mentions") or 0)
        row["agent_mentions"] = int(proof.get("agent_mentions") or 0)
        if not row.get("country_code") and proof.get("single_mention_country"):
            row["country_code"] = str(proof["single_mention_country"])

        row["is_agent"] = bool(row.get("is_agent")) or row["agent_mentions"] > 0
        row["is_direct"] = bool(row.get("is_direct")) or row["applicant_mentions"] > 0
        if row["is_agent"] and row["is_direct"]:
            row["segment"] = "BOTH"
        elif row["is_agent"]:
            row["segment"] = "AGENT"
        elif row["is_direct"]:
            row["segment"] = "DIRECT"
        else:
            row["segment"] = "UNKNOWN"

    return {
        "total": total,
        "limit": page_limit,
        "offset": page_offset,
        "rows": rows,
    }
