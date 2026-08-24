from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import re
import time
import uuid

from app.contact_ingest import country_inference as engine
from app.contact_ingest import country_inference_work as work
from app.contact_ingest import country_inference_work_guard as membership_guard
from app.db import postgres_conn


CONTACT_COUNTRY_RUNTIME_MODEL_VERSION = "CONTACT_COUNTRY_RUNTIME_MODEL_V4"

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

_ORIGINAL_UNKNOWN_CONTACT_COUNT = engine._unknown_contact_count


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


def _emit_engine(payload: dict[str, object]) -> None:
    event = str(payload.get("event") or "CONTACT_COUNTRY_INFERENCE_PROGRESS")
    body = {key: value for key, value in payload.items() if key != "event"}
    _emit(event, **body)


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


def _unknown_contact_count_with_commit(cur) -> int:
    """End the count transaction while retaining the session advisory lock.

    V1 intentionally holds a session-level advisory lock for the whole inference
    run. PostgreSQL session advisory locks survive COMMIT, so there is no reason to
    keep the transaction opened by the initial unknown-count SELECT alive while
    reference models and tens of thousands of contacts are processed on separate
    connections. Keeping it open trips the normal 60-second
    idle_in_transaction_session_timeout on real runs and can turn an otherwise
    successful 60+ minute preview into a fatal error during final unlock.
    """
    result = _ORIGINAL_UNKNOWN_CONTACT_COUNT(cur)
    cur.connection.commit()
    return result


def _country_inference_run(run_id: str) -> dict[str, object] | None:
    """Read one persisted inference run without re-evaluating contacts."""
    normalized_run_id = str(uuid.UUID(run_id))
    work.ensure_country_inference_work_schema()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_id::text, rule_version, status, apply_mode,
                       min_confidence, min_margin, batch_size, metrics,
                       error_message, started_at, finished_at
                FROM contact.country_inference_run
                WHERE run_id = %s::uuid
                """,
                (normalized_run_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    result = dict(row)
    result["work_units"] = work.work_summary_for_run(normalized_run_id)
    return result


def _show_run(run_id: str) -> int:
    try:
        row = _country_inference_run(run_id)
    except (ValueError, AttributeError) as exc:
        _emit("CONTACT_COUNTRY_INFERENCE_RUN_LOOKUP_ERROR", run_id=run_id, error=str(exc))
        return 2
    if row is None:
        _emit("CONTACT_COUNTRY_INFERENCE_RUN_NOT_FOUND", run_id=run_id)
        return 1
    _emit("CONTACT_COUNTRY_INFERENCE_RUN", run=row)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Infer missing contact countries with durable Work Engine entity-range batches"
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--show-run", metavar="RUN_ID")
    mode.add_argument(
        "--resume-run",
        metavar="RUN_ID",
        help="Resume an interrupted Work Engine-backed country inference run",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Activate only ACCEPTED high-confidence inferred countries as a view overlay",
    )
    parser.add_argument("--min-confidence", type=float, default=engine.DEFAULT_MIN_CONFIDENCE)
    parser.add_argument("--min-margin", type=float, default=engine.DEFAULT_MIN_MARGIN)
    parser.add_argument("--batch-size", type=int, default=engine.DEFAULT_BATCH_SIZE)
    parser.add_argument("--max-entities", type=int, default=None)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.show_run:
        return _show_run(args.show_run)

    # V1 remains the scoring/audit/apply authority. The operator runtime replaces
    # only the unbounded reference-model builder, the long-lived count transaction,
    # and the execution owner. Durable Work Engine state is stored per run_id and
    # entity UUID range; no official trademark fact semantics are changed.
    engine.build_reference_models = build_runtime_reference_models
    engine._unknown_contact_count = _unknown_contact_count_with_commit
    _emit(
        "CONTACT_COUNTRY_RUNTIME_START",
        inference_rule_version=engine.COUNTRY_INFERENCE_VERSION,
        city_training_scope="CONTACT_ENTITIES_ONLY",
        global_entity_mention_training_scan=False,
        long_run_transaction_policy="COMMIT_COUNT_TRANSACTION_KEEP_SESSION_LOCK",
        work_engine_owner_scope=work.WORK_OWNER_SCOPE,
        work_engine_checkpoint_version=work.CHECKPOINT_VERSION,
        work_engine_partition_kind=work.PARTITION_KIND,
        membership_guard_version=membership_guard.MEMBERSHIP_GUARD_VERSION,
        resume_run_id=args.resume_run,
    )
    try:
        membership_guard.ensure_country_inference_work_membership_guard()
        result = work.run_country_inference_resumable(
            apply=args.apply,
            min_confidence=args.min_confidence,
            min_margin=args.min_margin,
            batch_size=args.batch_size,
            max_entities=args.max_entities,
            resume_run_id=args.resume_run,
            emit=_emit_engine,
        )
    except Exception as exc:
        _emit(
            "CONTACT_COUNTRY_INFERENCE_FATAL",
            resume_run_id=args.resume_run,
            error=f"{type(exc).__name__}: {exc}",
        )
        return 2
    _emit("CONTACT_COUNTRY_INFERENCE_COMPLETE", **result)
    return 0 if result.get("status") in {"SUCCESS", "BUSY"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
