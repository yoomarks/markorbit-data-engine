from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import uuid
from typing import Any, Callable

from app.contact_ingest.country_inference import (
    COUNTRY_INFERENCE_LOCK,
    COUNTRY_INFERENCE_VERSION,
    _unknown_contact_count,
    ensure_country_inference_schema,
)
from app.contact_ingest.country_inference_run_audit import audit_persisted_run
from app.db import postgres_conn


PERSISTED_ACTIVATION_VERSION = "CONTACT_COUNTRY_PERSISTED_ACTIVATION_V1"
SAFE_ACTIVATION_MIN_CONFIDENCE = 0.90
DEFERRED_ONLY_EVIDENCE_KIND = "CITY_CORPUS_MODEL"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)


def activate_persisted_run(
    run_id: str,
    *,
    emit: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Activate the conservative high-quality subset of one persisted preview."""
    normalized_run_id = str(uuid.UUID(run_id))
    ensure_country_inference_schema()

    with postgres_conn() as lock_conn:
        with lock_conn.cursor() as cur:
            cur.execute(
                "SELECT pg_try_advisory_lock(hashtext(%s)) AS acquired",
                (COUNTRY_INFERENCE_LOCK,),
            )
            acquired = bool(cur.fetchone()["acquired"])
            lock_conn.commit()

        if not acquired:
            result = {
                "status": "BUSY",
                "run_id": normalized_run_id,
                "activation_version": PERSISTED_ACTIVATION_VERSION,
                "message": "contact country inference lock is held by another process",
            }
            if emit is not None:
                emit({"event": "CONTACT_COUNTRY_PERSISTED_ACTIVATION_BUSY", **result})
            return result

        try:
            report = audit_persisted_run(normalized_run_id)
            if report["rule_version"] != COUNTRY_INFERENCE_VERSION:
                raise RuntimeError(
                    "persisted run rule version is not the currently supported activation version: "
                    f"{report['rule_version']}"
                )
            if not report["activation_integrity_ready"]:
                raise RuntimeError(
                    "persisted run failed activation integrity checks; re-audit before activation"
                )

            if emit is not None:
                emit(
                    {
                        "event": "CONTACT_COUNTRY_PERSISTED_ACTIVATION_START",
                        "run_id": normalized_run_id,
                        "activation_version": PERSISTED_ACTIVATION_VERSION,
                        "audited_candidate_rows": report["activation_candidate_rows"],
                        "already_applied_rows": report["integrity_checks"][
                            "already_applied_rows"
                        ],
                        "source_country_rows": report["integrity_checks"][
                            "accepted_with_source_country_now"
                        ],
                    }
                )

            safe_min_confidence = max(
                float(report["min_confidence"]), SAFE_ACTIVATION_MIN_CONFIDENCE
            )
            with postgres_conn() as conn:
                with conn.cursor() as cur:
                    # Defense in depth against persisted-row corruption. These checks
                    # are evaluated under the inference advisory lock immediately before
                    # activation, so no ACCEPTED row can be activated below the original
                    # run thresholds or without a country code.
                    cur.execute(
                        """
                        SELECT
                            count(*) FILTER (
                                WHERE ci.status = 'ACCEPTED' AND ci.country_code IS NULL
                            ) AS accepted_without_country,
                            count(*) FILTER (
                                WHERE ci.status = 'ACCEPTED'
                                  AND ci.confidence < %s::numeric
                            ) AS accepted_below_confidence,
                            count(*) FILTER (
                                WHERE ci.status = 'ACCEPTED'
                                  AND (ci.confidence - ci.runner_up_confidence) < %s::numeric
                            ) AS accepted_below_margin
                        FROM contact.entity_country_inference AS ci
                        WHERE ci.last_run_id = %s::uuid
                        """,
                        (
                            report["min_confidence"],
                            report["min_margin"],
                            normalized_run_id,
                        ),
                    )
                    threshold_check = dict(cur.fetchone())
                    invalid_counts = {
                        key: int(value or 0) for key, value in threshold_check.items()
                    }
                    if any(invalid_counts.values()):
                        raise RuntimeError(
                            "persisted ACCEPTED rows failed transactional activation checks: "
                            f"{invalid_counts}"
                        )

                    # The V1 preview intentionally maximizes recall. Activation is more
                    # conservative: confidence below 0.90 is deferred, and city-corpus
                    # evidence cannot activate a country by itself because multiple city
                    # tokens can be correlated rather than independent observations.
                    cur.execute(
                        """
                        SELECT
                            count(*) FILTER (
                                WHERE ci.status = 'ACCEPTED'
                                  AND ci.applied_at IS NULL
                                  AND e.country_code IS NULL
                            ) AS audited_candidates,
                            count(*) FILTER (
                                WHERE ci.status = 'ACCEPTED'
                                  AND ci.applied_at IS NULL
                                  AND e.country_code IS NULL
                                  AND ci.confidence < %s::numeric
                            ) AS deferred_low_confidence,
                            count(*) FILTER (
                                WHERE ci.status = 'ACCEPTED'
                                  AND ci.applied_at IS NULL
                                  AND e.country_code IS NULL
                                  AND ci.confidence >= %s::numeric
                                  AND jsonb_array_length(ci.evidence) > 0
                                  AND NOT EXISTS (
                                      SELECT 1
                                      FROM jsonb_array_elements(ci.evidence) AS item
                                      WHERE COALESCE(item->>'kind', '') <> %s
                                  )
                            ) AS deferred_city_only,
                            count(*) FILTER (
                                WHERE ci.status = 'ACCEPTED'
                                  AND ci.applied_at IS NULL
                                  AND e.country_code IS NULL
                                  AND ci.confidence >= %s::numeric
                                  AND NOT (
                                      jsonb_array_length(ci.evidence) > 0
                                      AND NOT EXISTS (
                                          SELECT 1
                                          FROM jsonb_array_elements(ci.evidence) AS item
                                          WHERE COALESCE(item->>'kind', '') <> %s
                                      )
                                  )
                            ) AS safe_candidates
                        FROM contact.entity_country_inference AS ci
                        JOIN entity.entity AS e ON e.entity_id = ci.entity_id
                        WHERE ci.last_run_id = %s::uuid
                        """,
                        (
                            safe_min_confidence,
                            safe_min_confidence,
                            DEFERRED_ONLY_EVIDENCE_KIND,
                            safe_min_confidence,
                            DEFERRED_ONLY_EVIDENCE_KIND,
                            normalized_run_id,
                        ),
                    )
                    plan = {key: int(value or 0) for key, value in dict(cur.fetchone()).items()}
                    if emit is not None:
                        emit(
                            {
                                "event": "CONTACT_COUNTRY_PERSISTED_ACTIVATION_PLAN",
                                "run_id": normalized_run_id,
                                "safe_min_confidence": safe_min_confidence,
                                **plan,
                            }
                        )

                    unknown_before = _unknown_contact_count(cur)
                    cur.execute(
                        """
                        UPDATE contact.entity_country_inference AS ci
                        SET applied_at = now()
                        FROM entity.entity AS e
                        WHERE ci.entity_id = e.entity_id
                          AND ci.last_run_id = %s::uuid
                          AND ci.rule_version = %s
                          AND ci.status = 'ACCEPTED'
                          AND ci.country_code IS NOT NULL
                          AND ci.confidence >= %s::numeric
                          AND (ci.confidence - ci.runner_up_confidence) >= %s::numeric
                          AND ci.applied_at IS NULL
                          AND e.country_code IS NULL
                          AND NOT (
                              jsonb_array_length(ci.evidence) > 0
                              AND NOT EXISTS (
                                  SELECT 1
                                  FROM jsonb_array_elements(ci.evidence) AS item
                                  WHERE COALESCE(item->>'kind', '') <> %s
                              )
                          )
                        """,
                        (
                            normalized_run_id,
                            report["rule_version"],
                            safe_min_confidence,
                            report["min_margin"],
                            DEFERRED_ONLY_EVIDENCE_KIND,
                        ),
                    )
                    newly_applied_rows = int(cur.rowcount or 0)
                    if newly_applied_rows != plan["safe_candidates"]:
                        raise RuntimeError(
                            "activation row count changed inside transaction: "
                            f"planned={plan['safe_candidates']} applied={newly_applied_rows}"
                        )

                    cur.execute(
                        """
                        SELECT
                            count(*) FILTER (
                                WHERE ci.status = 'ACCEPTED' AND ci.applied_at IS NOT NULL
                            ) AS applied_rows_after,
                            count(*) FILTER (
                                WHERE ci.status = 'ACCEPTED'
                                  AND ci.applied_at IS NULL
                                  AND e.country_code IS NOT NULL
                            ) AS source_country_rows_after,
                            count(*) FILTER (
                                WHERE ci.status = 'ACCEPTED'
                                  AND ci.applied_at IS NULL
                                  AND e.country_code IS NULL
                            ) AS remaining_audited_candidates,
                            count(*) FILTER (
                                WHERE ci.status = 'ACCEPTED'
                                  AND ci.applied_at IS NULL
                                  AND e.country_code IS NULL
                                  AND ci.confidence >= %s::numeric
                                  AND NOT (
                                      jsonb_array_length(ci.evidence) > 0
                                      AND NOT EXISTS (
                                          SELECT 1
                                          FROM jsonb_array_elements(ci.evidence) AS item
                                          WHERE COALESCE(item->>'kind', '') <> %s
                                      )
                                  )
                            ) AS remaining_safe_candidates
                        FROM contact.entity_country_inference AS ci
                        JOIN entity.entity AS e ON e.entity_id = ci.entity_id
                        WHERE ci.last_run_id = %s::uuid
                        """,
                        (
                            safe_min_confidence,
                            DEFERRED_ONLY_EVIDENCE_KIND,
                            normalized_run_id,
                        ),
                    )
                    after = {key: int(value or 0) for key, value in dict(cur.fetchone()).items()}
                    if after["remaining_safe_candidates"] != 0:
                        raise RuntimeError(
                            "safe activation left eligible candidates unapplied: "
                            f"{after['remaining_safe_candidates']}"
                        )
                    unknown_after = _unknown_contact_count(cur)

                    activation_metrics = {
                        "activation_version": PERSISTED_ACTIVATION_VERSION,
                        "activated_at": datetime.now(timezone.utc).isoformat(),
                        "safe_min_confidence": safe_min_confidence,
                        "deferred_only_evidence_kind": DEFERRED_ONLY_EVIDENCE_KIND,
                        **plan,
                        "newly_applied_rows": newly_applied_rows,
                        **after,
                        "unknown_before": unknown_before,
                        "unknown_after": unknown_after,
                        "source_country_fields_mutated": False,
                        "semantics": "INFERRED_CONTACT_GEO_OVERLAY_NOT_OFFICIAL_TRADEMARK_FACT",
                    }
                    cur.execute(
                        """
                        UPDATE contact.country_inference_run
                        SET metrics = COALESCE(metrics, '{}'::jsonb)
                                      || jsonb_build_object('activation', %s::jsonb)
                        WHERE run_id = %s::uuid
                        """,
                        (_json(activation_metrics), normalized_run_id),
                    )
                conn.commit()

            if newly_applied_rows:
                try:
                    from app.contact_ingest.directory_cached import invalidate_contact_view_cache

                    invalidate_contact_view_cache()
                except Exception:
                    # applied_at is part of the cache generation contract, so a local
                    # invalidation failure must not roll back a committed activation.
                    pass

            result = {
                "status": "SUCCESS",
                "run_id": normalized_run_id,
                "rule_version": report["rule_version"],
                "activation_version": PERSISTED_ACTIVATION_VERSION,
                "audited_candidate_rows_before": int(report["activation_candidate_rows"]),
                "safe_min_confidence": safe_min_confidence,
                "deferred_only_evidence_kind": DEFERRED_ONLY_EVIDENCE_KIND,
                **plan,
                "newly_applied_rows": newly_applied_rows,
                **after,
                "unknown_before": unknown_before,
                "unknown_after": unknown_after,
                "source_country_fields_mutated": False,
                "semantics": "INFERRED_CONTACT_GEO_OVERLAY_NOT_OFFICIAL_TRADEMARK_FACT",
            }
            if emit is not None:
                emit({"event": "CONTACT_COUNTRY_PERSISTED_ACTIVATION_COMPLETE", **result})
            return result
        finally:
            with lock_conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_unlock(hashtext(%s))",
                    (COUNTRY_INFERENCE_LOCK,),
                )
            lock_conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Activate the conservative high-quality subset of a persisted "
            "contact-country preview without recomputing inference"
        )
    )
    parser.add_argument("run_id")
    args = parser.parse_args()
    try:
        result = activate_persisted_run(args.run_id, emit=_emit)
    except (ValueError, LookupError, RuntimeError) as exc:
        _emit(
            {
                "event": "CONTACT_COUNTRY_PERSISTED_ACTIVATION_ERROR",
                "run_id": args.run_id,
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        return 2
    return 0 if result.get("status") in {"SUCCESS", "BUSY"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
