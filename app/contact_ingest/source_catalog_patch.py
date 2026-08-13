from __future__ import annotations

import json
from typing import Any

from app.contact_ingest.migrations import ensure_contact_schema
from app.contact_ingest.source_catalog import lookup_source_catalog
from app.db import postgres_conn


SOURCE_METADATA_SQL = """
ALTER TABLE contact.source
    ADD COLUMN IF NOT EXISTS source_segment text NOT NULL DEFAULT 'UNKNOWN';
ALTER TABLE contact.source
    ADD COLUMN IF NOT EXISTS source_scope text NOT NULL DEFAULT '';
ALTER TABLE contact.source
    ADD COLUMN IF NOT EXISTS default_country_code char(2);
"""


def _source_rows(cur) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT source_id, source_name, source_segment, source_scope,
               default_country_code
        FROM contact.source
        ORDER BY first_seen_at, source_id
        """
    )
    return [dict(row) for row in cur.fetchall()]


def apply_source_catalog_patch() -> dict[str, int]:
    """Backfill reviewed source metadata and only missing country values.

    Row/entity country evidence always wins. A source-level country is used only
    when the entity has no country, and only when all reviewed source evidence
    linked to that entity agrees on one country.
    """
    ensure_contact_schema()
    metrics = {
        "catalog_sources_matched": 0,
        "catalog_sources_unmatched": 0,
        "agent_sources": 0,
        "direct_sources": 0,
        "country_default_sources": 0,
        "source_rows_updated": 0,
        "entities_country_filled": 0,
        "people_country_filled": 0,
    }

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(SOURCE_METADATA_SQL)
            for row in _source_rows(cur):
                item = lookup_source_catalog(str(row.get("source_name") or ""))
                if item is None:
                    metrics["catalog_sources_unmatched"] += 1
                    continue
                metrics["catalog_sources_matched"] += 1
                metrics["agent_sources" if item.segment == "AGENT" else "direct_sources"] += 1
                if item.default_country_code:
                    metrics["country_default_sources"] += 1
                cur.execute(
                    """
                    UPDATE contact.source
                    SET source_segment = %s,
                        source_scope = %s,
                        default_country_code = NULLIF(%s, ''),
                        last_seen_at = last_seen_at
                    WHERE source_id = %s
                      AND (
                          source_segment IS DISTINCT FROM %s
                          OR source_scope IS DISTINCT FROM %s
                          OR default_country_code IS DISTINCT FROM NULLIF(%s, '')::char(2)
                      )
                    """,
                    (
                        item.segment,
                        item.scope_label,
                        item.default_country_code,
                        row["source_id"],
                        item.segment,
                        item.scope_label,
                        item.default_country_code,
                    ),
                )
                metrics["source_rows_updated"] += int(cur.rowcount or 0)

            # Explicit/entity-level country values are authoritative and never overwritten.
            # For an empty entity country, source-level fallback is applied only if all
            # reviewed country-bearing sources for that entity agree on one ISO code.
            cur.execute(
                """
                WITH candidates AS (
                    SELECT rr.entity_id,
                           min(s.default_country_code)::char(2) AS country_code
                    FROM contact.raw_record AS rr
                    JOIN contact.source AS s ON s.source_id = rr.source_id
                    WHERE rr.entity_id IS NOT NULL
                      AND s.default_country_code IS NOT NULL
                    GROUP BY rr.entity_id
                    HAVING count(DISTINCT s.default_country_code) = 1
                )
                UPDATE entity.entity AS e
                SET country_code = c.country_code,
                    updated_at = now()
                FROM candidates AS c
                WHERE e.entity_id = c.entity_id
                  AND (e.country_code IS NULL OR btrim(e.country_code) = '')
                """
            )
            metrics["entities_country_filled"] = int(cur.rowcount or 0)

            # Person country is also a fallback. Only use a single unambiguous country
            # observed across all linked entities and never replace an existing value.
            cur.execute(
                """
                WITH candidates AS (
                    SELECT r.person_id,
                           min(e.country_code)::char(2) AS country_code
                    FROM contact.entity_person_relation AS r
                    JOIN entity.entity AS e ON e.entity_id = r.entity_id
                    WHERE e.country_code IS NOT NULL
                      AND btrim(e.country_code) <> ''
                    GROUP BY r.person_id
                    HAVING count(DISTINCT e.country_code) = 1
                )
                UPDATE contact.person AS p
                SET country_code = c.country_code,
                    updated_at = now()
                FROM candidates AS c
                WHERE p.person_id = c.person_id
                  AND (p.country_code IS NULL OR btrim(p.country_code) = '')
                """
            )
            metrics["people_country_filled"] = int(cur.rowcount or 0)
        conn.commit()
    return metrics


def main() -> None:
    print(json.dumps(apply_source_catalog_patch(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
