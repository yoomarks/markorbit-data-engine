from __future__ import annotations

from copy import deepcopy
import threading
import time
from typing import Any

from app.db import postgres_conn


_ANALYTICS_CACHE_TTL_SECONDS = 30.0
_analytics_cache_lock = threading.Lock()
_analytics_cache_at = 0.0
_analytics_cache_value: dict[str, Any] | None = None


# Keep the common rollup deliberately narrow. The old implementation aggregated
# every person's name and every channel value into arrays before it could count
# or page entities. With a large Contacts corpus that made even the overview do
# full-corpus array aggregation twice. Arrays are now hydrated only for the
# requested page below.
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
        c.channel_value
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
        bool_or(source_profile = 'QCC_COMPANY_EXPORT') AS has_direct_source
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
channel_stats AS (
    SELECT
        entity_id,
        count(*) FILTER (WHERE channel_type IN ('PHONE', 'MOBILE')) AS phone_count,
        count(*) FILTER (WHERE channel_type = 'EMAIL') AS email_count,
        count(*) FILTER (WHERE channel_type = 'WEBSITE') AS website_count,
        count(*) FILTER (WHERE channel_type = 'WHATSAPP') AS whatsapp_count
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
    LEFT JOIN channel_stats AS ch ON ch.entity_id = e.entity_id
)
"""


_PAGE_HYDRATION_SQL = r"""
WITH requested AS (
    SELECT unnest(%s::uuid[]) AS entity_id
),
target_people AS (
    SELECT DISTINCT r.person_id
    FROM contact.entity_person_relation AS r
    JOIN requested AS q ON q.entity_id = r.entity_id
),
person_owner AS (
    SELECT DISTINCT ON (r.person_id)
        r.person_id,
        r.entity_id
    FROM contact.entity_person_relation AS r
    JOIN target_people AS tp ON tp.person_id = r.person_id
    ORDER BY r.person_id, r.last_seen_at DESC, r.relation_id
),
people AS (
    SELECT
        r.entity_id,
        array_agg(DISTINCT p.canonical_name ORDER BY p.canonical_name) AS people
    FROM contact.entity_person_relation AS r
    JOIN requested AS q ON q.entity_id = r.entity_id
    JOIN contact.person AS p ON p.person_id = r.person_id
    GROUP BY r.entity_id
),
channel_entity AS (
    SELECT c.entity_id, c.channel_type, c.channel_value
    FROM contact.channel AS c
    JOIN requested AS q ON q.entity_id = c.entity_id
    WHERE c.entity_id IS NOT NULL
    UNION ALL
    SELECT po.entity_id, c.channel_type, c.channel_value
    FROM contact.channel AS c
    JOIN person_owner AS po ON po.person_id = c.person_id
    JOIN requested AS q ON q.entity_id = po.entity_id
    WHERE c.person_id IS NOT NULL
),
channels AS (
    SELECT
        entity_id,
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
sources AS (
    SELECT
        rr.entity_id,
        array_agg(DISTINCT rr.source_profile ORDER BY rr.source_profile) AS source_profiles
    FROM contact.raw_record AS rr
    JOIN requested AS q ON q.entity_id = rr.entity_id
    GROUP BY rr.entity_id
)
SELECT
    q.entity_id,
    COALESCE(p.people, ARRAY[]::text[]) AS people,
    COALESCE(ch.phones, ARRAY[]::text[]) AS phones,
    COALESCE(ch.emails, ARRAY[]::text[]) AS emails,
    COALESCE(ch.websites, ARRAY[]::text[]) AS websites,
    COALESCE(ch.whatsapps, ARRAY[]::text[]) AS whatsapps,
    COALESCE(s.source_profiles, ARRAY[]::text[]) AS source_profiles
FROM requested AS q
LEFT JOIN people AS p ON p.entity_id = q.entity_id
LEFT JOIN channels AS ch ON ch.entity_id = q.entity_id
LEFT JOIN sources AS s ON s.entity_id = q.entity_id
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


def invalidate_contact_directory_cache() -> None:
    global _analytics_cache_at, _analytics_cache_value
    with _analytics_cache_lock:
        _analytics_cache_at = 0.0
        _analytics_cache_value = None


def _cached_analytics() -> dict[str, Any] | None:
    now = time.monotonic()
    with _analytics_cache_lock:
        if (
            _analytics_cache_value is not None
            and now - _analytics_cache_at < _ANALYTICS_CACHE_TTL_SECONDS
        ):
            return deepcopy(_analytics_cache_value)
    return None


def _store_analytics(value: dict[str, Any]) -> None:
    global _analytics_cache_at, _analytics_cache_value
    with _analytics_cache_lock:
        _analytics_cache_at = time.monotonic()
        _analytics_cache_value = deepcopy(value)


def contact_directory_analytics() -> dict[str, Any]:
    """Return country/type/channel coverage using one lightweight corpus pass."""
    cached = _cached_analytics()
    if cached is not None:
        return cached

    sql = _CONTACT_ROLLUP_CTE + r"""
SELECT
    CASE WHEN GROUPING(country_code) = 1 THEN '__TOTAL__'
         ELSE COALESCE(country_code, '') END AS bucket,
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
GROUP BY GROUPING SETS ((), (country_code))
ORDER BY GROUPING(country_code) DESC, entities DESC, country_code NULLS LAST
"""
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            raw_rows = [dict(row) for row in cur.fetchall()]

    total_raw = next((row for row in raw_rows if row.get("bucket") == "__TOTAL__"), {})
    total = _summary_row(total_raw)
    total["countries"] = int(total_raw.get("countries") or 0)
    countries = [
        {"country_code": str(row.get("bucket") or ""), **_summary_row(row)}
        for row in raw_rows
        if row.get("bucket") != "__TOTAL__"
    ]
    result = {
        "totals": total,
        "countries": countries,
        "classification": {
            "agent": "代理角色商标记录、AGENT_CONTACT_LIST 来源或 ATTORNEY/AGENT/CORRESPONDENT 关系",
            "direct_client": "OWNER/CO_OWNER/APPLICANT 商标记录或 QCC_COMPANY_EXPORT 来源",
            "both": "同时满足代理和直客条件",
            "unknown": "已有联系人数据，但现有证据不足以归类",
        },
    }
    _store_analytics(result)
    return result


def contact_directory_list(
    *,
    country: str = "",
    segment: str = "",
    channel: str = "",
    query: str = "",
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Page lightweight entity rows first, then hydrate details only for that page."""
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
            "EXISTS ("
            "  SELECT 1 FROM contact.entity_person_relation AS qr "
            "  JOIN contact.person AS qp ON qp.person_id = qr.person_id "
            "  WHERE qr.entity_id = rollup.entity_id AND qp.canonical_name ILIKE %s"
            ") OR EXISTS ("
            "  SELECT 1 FROM channel_entity AS qc "
            "  WHERE qc.entity_id = rollup.entity_id AND qc.channel_value ILIKE %s"
            ")"
            ")"
        )
        pattern = f"%{query}%"
        params.extend([pattern] * 4)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    page_limit = max(1, min(int(limit), 500))
    page_offset = max(0, int(offset))
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
    applicant_mentions,
    agent_mentions,
    count(*) OVER() AS filtered_total
FROM rollup
{where}
ORDER BY country_code NULLS LAST, lower(entity_name), entity_id
LIMIT %s OFFSET %s
"""
    params.extend([page_limit, page_offset])

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = [dict(row) for row in cur.fetchall()]
            entity_ids = [str(row["entity_id"]) for row in rows]
            hydrated: dict[str, dict[str, Any]] = {}
            if entity_ids:
                cur.execute(_PAGE_HYDRATION_SQL, (entity_ids,))
                hydrated = {
                    str(item["entity_id"]): dict(item)
                    for item in cur.fetchall()
                }

    total = int(rows[0].pop("filtered_total")) if rows else 0
    for row in rows:
        row.pop("filtered_total", None)
        details = hydrated.get(str(row["entity_id"]), {})
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
            row[key] = list(details.get(key) or [])
        row["country_code"] = str(row.get("country_code") or "")

    return {
        "total": total,
        "limit": page_limit,
        "offset": page_offset,
        "rows": rows,
    }
