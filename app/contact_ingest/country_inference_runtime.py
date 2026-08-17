from __future__ import annotations

from collections import Counter, defaultdict
import json
import re
import time

from app.contact_ingest import country_inference as engine
from app.db import postgres_conn


CONTACT_COUNTRY_RUNTIME_MODEL_VERSION = "CONTACT_COUNTRY_RUNTIME_MODEL_V2"

# The original V1 city learner included every entity.entity_mention row. That is
# acceptable for small fixtures but unsafe on a real trademark corpus: millions of
# mention rows can be materialized in Python before the first progress event. The
# runtime learner deliberately trains only from source-grounded CONTACT entities.
# Unknown contacts that have an explicit mention country still get that evidence
# directly in V1's per-entity context, so excluding the global mention corpus from
# the city reference model is conservative rather than lossy for strong evidence.
_CONTACT_CITY_COUNTS_SQL = r"""
WITH contact_entities AS (
    SELECT entity_id FROM contact.raw_record WHERE entity_id IS NOT NULL
    UNION
    SELECT entity_id FROM contact.entity_person_relation
    UNION
    SELECT entity_id FROM contact.channel WHERE entity_id IS NOT NULL
)
SELECT
    e.city,
    e.country_code,
    count(*) AS row_count
FROM contact_entities AS ce
JOIN entity.entity AS e ON e.entity_id = ce.entity_id
LEFT JOIN contact.entity_country_inference AS ci
  ON ci.entity_id = e.entity_id AND ci.applied_at IS NOT NULL
WHERE e.country_code IS NOT NULL
  AND e.country_code ~ '^[A-Z]{2}$'
  AND e.city IS NOT NULL
  AND btrim(e.city) <> ''
  AND ci.entity_id IS NULL
GROUP BY e.city, e.country_code
"""


def _emit(event: str, **payload: object) -> None:
    print(
        json.dumps(
            {
                "event": event,
                "runtime_model_version": CONTACT_COUNTRY_RUNTIME_MODEL_VERSION,
                **payload,
            },
            ensure_ascii=False,
            default=str,
        ),
        flush=True,
    )


def build_contact_scoped_city_model() -> dict[str, tuple[str, float, int]]:
    """Build city→country training from contact-owned, source-grounded rows only."""
    started = time.monotonic()
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    grouped_rows = 0
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_CONTACT_CITY_COUNTS_SQL)
            for row in cur.fetchall():
                city = engine._normalize_city(row.get("city"))
                country = str(row.get("country_code") or "").upper()
                row_count = int(row.get("row_count") or 0)
                if city and re.fullmatch(r"[A-Z]{2}", country) and row_count > 0:
                    counts[city][country] += row_count
                    grouped_rows += 1

    model: dict[str, tuple[str, float, int]] = {}
    for city, per_country in counts.items():
        total = sum(per_country.values())
        country, top = per_country.most_common(1)[0]
        dominance = top / total if total else 0.0
        if top >= 3 and dominance >= 0.90:
            model[city] = (country, dominance, total)

    _emit(
        "CONTACT_COUNTRY_CITY_MODEL_READY",
        grouped_source_rows=grouped_rows,
        normalized_city_candidates=len(counts),
        accepted_city_keys=len(model),
        elapsed_seconds=round(time.monotonic() - started, 2),
    )
    return model


def build_runtime_reference_models() -> engine.ReferenceModels:
    """Bound reference-model construction before V1 evaluates unknown contacts."""
    started = time.monotonic()
    _emit(
        "CONTACT_COUNTRY_REFERENCE_MODEL_START",
        policy="CONTACT_SCOPED_SOURCE_FACTS_ONLY",
    )
    city_country = build_contact_scoped_city_model()

    domain_started = time.monotonic()
    _emit("CONTACT_COUNTRY_DOMAIN_MODEL_START")
    domain_country = engine._build_domain_model()
    _emit(
        "CONTACT_COUNTRY_DOMAIN_MODEL_READY",
        accepted_domain_keys=len(domain_country),
        elapsed_seconds=round(time.monotonic() - domain_started, 2),
    )

    _emit(
        "CONTACT_COUNTRY_REFERENCE_MODEL_READY",
        accepted_city_keys=len(city_country),
        accepted_domain_keys=len(domain_country),
        elapsed_seconds=round(time.monotonic() - started, 2),
    )
    return engine.ReferenceModels(
        city_country=city_country,
        domain_country=domain_country,
    )


def main() -> int:
    # V1 remains the scoring/audit/apply authority. Only its unbounded reference
    # model builder is replaced at the operator boundary.
    engine.build_reference_models = build_runtime_reference_models
    _emit(
        "CONTACT_COUNTRY_RUNTIME_START",
        inference_rule_version=engine.COUNTRY_INFERENCE_VERSION,
        city_training_scope="CONTACT_ENTITIES_ONLY",
        global_entity_mention_training_scan=False,
    )
    return engine.main()


if __name__ == "__main__":
    raise SystemExit(main())
