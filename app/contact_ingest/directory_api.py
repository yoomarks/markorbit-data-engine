from __future__ import annotations

from typing import Any

from app.db import postgres_conn


_CONTACT_ROLLUP_CTE = r"""
WITH person_owner AS (
    SELECT DISTINCT ON (person_id)
        person_id,
        entity_id
    FROM contact.entity_person_relation
    ORDER BY person_id, last_seen_at DESC, relation_id
),
channel_entity AS (
    SELECT
        c.channel_id,
        COALESCE(c.entity_id, po.entity_id) AS entity_id,
        c.person_id,
        c.channel_type,
        c.channel_value,
        c.normalized_value
    FROM contact.channel AS c
    LEFT JOIN person_owner AS po ON po.person_id = c.person_id
    WHERE COALESCE(c.entity_id, po.entity_id) IS NOT NULL
),
contact_entities AS (
    SELECT entity_id FROM contact.raw_record WHERE entity_id IS NOT NULL
    UNION
    SELECT entity_id FROM contact.entity_person_relation
    UNION
    SELECT entity_id FROM channel_entity
),
mention_stats AS (
    SELECT
        entity_id,
        count(*) FILTER (WHERE role IN ('OWNER', 'CO_OWNER', 'APPLICANT')) AS applicant_mentions,
        count(*) FILTER (WHERE role IN ('AGENT', 'ATTORNEY', 'CORRESPONDENT')) AS agent_mentions,
        CASE
            WHEN count(DISTINCT jurisdiction)
                 FILTER (WHERE jurisdiction ~ '^[A-Z]{2}$') = 1
            THEN max(jurisdiction)
                 FILTER (WHERE jurisdiction ~ '^[A-Z]{2}$')
            ELSE NULL
        END AS single_mention_country
    FROM entity.entity_mention
    WHERE entity_id IS NOT NULL
    GROUP BY entity_id
),
source_stats AS (
    SELECT
        entity_id,
        bool_or(source_profile = 'AGENT_CONTACT_LIST') AS has_agent_source,
        bool_or(source_profile = 'QCC_COMPANY_EXPORT') AS has_direct_source,
        array_agg(DISTINCT source_profile ORDER BY source_profile) AS source_profiles
    FROM contact.raw_record
    WHERE entity_id IS NOT NULL
    GROUP BY entity_id
),
relation_stats AS (
    SELECT
        entity_id,
        bool_or(relation_type IN ('ATTORNEY', 'AGENT', 'CORRESPONDENT')) AS has_agent_relation,
        count(DISTINCT person_id) AS person_count
    FROM contact.entity_person_relation
    GROUP BY entity_id
),
people AS (
    SELECT
        r.entity_id,
        array_agg(DISTINCT p.canonical_name ORDER BY p.canonical_name) AS people
    FROM contact.entity_person_relation AS r
    JOIN contact.person AS p ON p.person_id = r.person_id
    GROUP BY r.entity_id
),
channels AS (
    SELECT
        entity_id,
        count(*) FILTER (WHERE channel_type IN ('PHONE', 'MOBILE')) AS phone_count,
        count(*) FILTER (WHERE channel_type = 'EMAIL') AS email_count,
        count(*) FILTER (WHERE channel_type = 'WEBSITE') AS website_count,
        count(*) FILTER (WHERE channel_type = 'WHATSAPP') AS whatsapp_count,
        array_agg(DISTINCT channel_value ORDER BY channel_value)
            FILTER (WHERE channel_type IN ('PHONE', 'MOBILE')) AS phones,
        array_agg(DISTINCT channel_value ORDER BY channel_value)
            FILTER (WHERE channel_type = 'EMAIL') AS emails,
        array_agg(DISTINCT channel_value ORDER BY channel_value)
            FILTER (WHERE channel_type = 'WEBSITE') AS websites,
        array_agg(DISTINCT channel_value ORDER BY channel_value)
            FILTER (WHERE channel_type = 'WHATSAPP') AS whatsapps
    FROM channel_entity
    GROUP BY entity_id
),
rollup AS (
    SELECT
        e.entity_id,
        e.canonical_name AS entity_name,
        e.entity_type,
        COALESCE(NULLIF(e.country_code, ''), ms.single_mention_country) AS country_code,
        e.region_code,
        e.city,
        COALESCE(rs.person_count, 0) AS person_count,
        COALESCE(ch.phone_count, 0) AS phone_count,
        COALESCE(ch.email_count, 0) AS email_count,
        COALESCE(ch.website_count, 0) AS website_count,
        COALESCE(ch.whatsapp_count, 0) AS whatsapp_count,
        COALESCE(ch.phones, ARRAY[]::text[]) AS phones,
        COALESCE(ch.emails, ARRAY[]::text[]) AS emails,
        COALESCE(ch.websites, ARRAY[]::text[]) AS websites,
        COALESCE(ch.whatsapps, ARRAY[]::text[]) AS whatsapps,
        COALESCE(p.people, ARRAY[]::text[]) AS people,
        COALESCE(ss.source_profiles, ARRAY[]::text[]) AS source_profiles,
        COALESCE(ms.applicant_mentions, 0) AS applicant_mentions,
        COALESCE(ms.agent_mentions, 0) AS agent_mentions,
        (
            COALESCE(ms.agent_mentions, 0) > 0
            OR COALESCE(ss.has_agent_source, false)
            OR COALESCE(rs.has_agent_relation, false)
            OR e.entity_type = 'AGENT_PERSON'
        ) AS is_agent,
        (
            COALESCE(ms.applicant_mentions, 0) > 0
            OR COALESCE(ss.has_direct_source, false)
        ) AS is_direct
    FROM contact_entities AS ce
    JOIN entity.entity AS e ON e.entity_id = ce.entity_id
    LEFT JOIN mention_stats AS ms ON ms.entity_id = e.entity_id
    LEFT JOIN source_stats AS ss ON ss.entity_id = e.entity_id
    LEFT JOIN relation_stats AS rs ON rs.entity_id = e.entity_id
    LEFT JOIN people AS p ON p.entity_id = e.entity_id
    LEFT JOIN channels AS ch ON ch.entity_id = e.entity_id
)
"""


def _summary_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "entities": int(row.get("entities") or 0),
        "agents": int(row.get("agents") or 0),
        "direct_clients": int(row.get("direct_clients") or 0),
        "both": int(row.get("both") or 0),
        "unknown": int(row.get("unknown") or 0),
        "people": int(row.get("people") or 0),
        "phones": int(row.get("phones") or 0),
        "emails": int(row.get("emails") or 0),
        "websites": int(row.get("websites") or 0),
        "whatsapps": int(row.get("whatsapps") or 0),
    }


def contact_directory_analytics() -> dict[str, Any]:
    """Return contact-side business segmentation and country/channel coverage."""
    total_sql = _CONTACT_ROLLUP_CTE + r"""
SELECT
    count(*) AS entities,
    count(*) FILTER (WHERE is_agent) AS agents,
    count(*) FILTER (WHERE is_direct) AS direct_clients,
    count(*) FILTER (WHERE is_agent AND is_direct) AS both,
    count(*) FILTER (WHERE NOT is_agent AND NOT is_direct) AS unknown,
    sum(person_count) AS people,
    sum(phone_count) AS phones,
    sum(email_count) AS emails,
    sum(website_count) AS websites,
    sum(whatsapp_count) AS whatsapps,
    count(DISTINCT country_code) FILTER (WHERE country_code IS NOT NULL) AS countries
FROM rollup
"""
    country_sql = _CONTACT_ROLLUP_CTE + r"""
SELECT
    COALESCE(country_code, '') AS country_code,
    count(*) AS entities,
    count(*) FILTER (WHERE is_agent) AS agents,
    count(*) FILTER (WHERE is_direct) AS direct_clients,
    count(*) FILTER (WHERE is_agent AND is_direct) AS both,
    count(*) FILTER (WHERE NOT is_agent AND NOT is_direct) AS unknown,
    sum(person_count) AS people,
    sum(phone_count) AS phones,
    sum(email_count) AS emails,
    sum(website_count) AS websites,
    sum(whatsapp_count) AS whatsapps
FROM rollup
GROUP BY country_code
ORDER BY country_code IS NULL, entities DESC, country_code
"""

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(total_sql)
            total_raw = dict(cur.fetchone())
            cur.execute(country_sql)
            country_rows = [dict(row) for row in cur.fetchall()]

    total = _summary_row(total_raw)
    total["countries"] = int(total_raw.get("countries") or 0)
    return {
        "totals": total,
        "countries": [
            {"country_code": str(row.get("country_code") or ""), **_summary_row(row)}
            for row in country_rows
        ],
        "classification": {
            "agent": "代理角色商标记录、AGENT_CONTACT_LIST 来源或 ATTORNEY/AGENT/CORRESPONDENT 关系",
            "direct_client": "OWNER/CO_OWNER/APPLICANT 商标记录或 QCC_COMPANY_EXPORT 来源",
            "both": "同时满足代理和直客条件",
            "unknown": "已有联系人数据，但现有证据不足以归类",
        },
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
    """List one row per contact entity with filterable people and channels."""
    clauses: list[str] = []
    params: list[Any] = []

    country = country.strip().upper()
    segment = segment.strip().upper()
    channel = channel.strip().upper()
    query = query.strip()

    if country == "__UNKNOWN__":
        clauses.append("country_code IS NULL")
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
        clauses.append("email_count > 0")
    elif channel == "PHONE":
        clauses.append("phone_count > 0")
    elif channel == "WEBSITE":
        clauses.append("website_count > 0")
    elif channel == "WHATSAPP":
        clauses.append("whatsapp_count > 0")

    if query:
        clauses.append(
            "("
            "entity_name ILIKE %s OR COALESCE(city, '') ILIKE %s OR "
            "array_to_string(people, ' ') ILIKE %s OR "
            "array_to_string(phones, ' ') ILIKE %s OR "
            "array_to_string(emails, ' ') ILIKE %s OR "
            "array_to_string(websites, ' ') ILIKE %s"
            ")"
        )
        pattern = f"%{query}%"
        params.extend([pattern] * 6)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = _CONTACT_ROLLUP_CTE + f"""
SELECT
    entity_id,
    entity_name,
    entity_type,
    COALESCE(country_code, '') AS country_code,
    region_code,
    city,
    CASE
        WHEN is_agent AND is_direct THEN 'BOTH'
        WHEN is_agent THEN 'AGENT'
        WHEN is_direct THEN 'DIRECT'
        ELSE 'UNKNOWN'
    END AS segment,
    is_agent,
    is_direct,
    person_count,
    phone_count,
    email_count,
    website_count,
    whatsapp_count,
    people,
    phones,
    emails,
    websites,
    whatsapps,
    source_profiles,
    applicant_mentions,
    agent_mentions,
    count(*) OVER() AS filtered_total
FROM rollup
{where}
ORDER BY country_code NULLS LAST, lower(entity_name), entity_id
LIMIT %s OFFSET %s
"""
    params.extend([max(1, min(int(limit), 500)), max(0, int(offset))])

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = [dict(row) for row in cur.fetchall()]

    total = int(rows[0].pop("filtered_total")) if rows else 0
    for row in rows:
        row.pop("filtered_total", None)
        for key in (
            "person_count",
            "phone_count",
            "email_count",
            "website_count",
            "whatsapp_count",
            "applicant_mentions",
            "agent_mentions",
        ):
            row[key] = int(row.get(key) or 0)
        for key in (
            "people",
            "phones",
            "emails",
            "websites",
            "whatsapps",
            "source_profiles",
        ):
            row[key] = list(row.get(key) or [])
        row["country_code"] = str(row.get("country_code") or "")

    return {
        "total": total,
        "limit": max(1, min(int(limit), 500)),
        "offset": max(0, int(offset)),
        "rows": rows,
    }
