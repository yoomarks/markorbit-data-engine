from __future__ import annotations

import argparse
import json
import uuid
from typing import Any

from app.contact_ingest.country_inference import ensure_country_inference_schema
from app.db import postgres_conn


def _pairs(rows: list[dict[str, Any]], key: str, value: str) -> dict[str, int]:
    return {str(row[key] or ""): int(row[value] or 0) for row in rows}


def audit_persisted_run(run_id: str) -> dict[str, Any]:
    """Audit one persisted preview without recomputing any contact inference."""
    normalized_run_id = str(uuid.UUID(run_id))
    ensure_country_inference_schema()

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_id::text, rule_version, status, apply_mode,
                       min_confidence::float8 AS min_confidence,
                       min_margin::float8 AS min_margin,
                       batch_size, metrics, error_message, started_at, finished_at
                FROM contact.country_inference_run
                WHERE run_id = %s::uuid
                """,
                (normalized_run_id,),
            )
            run = cur.fetchone()
            if run is None:
                raise LookupError(f"country inference run not found: {normalized_run_id}")
            run = dict(run)
            metrics = dict(run.get("metrics") or {})

            cur.execute(
                """
                SELECT status, count(*) AS row_count
                FROM contact.entity_country_inference
                WHERE last_run_id = %s::uuid
                GROUP BY status
                ORDER BY status
                """,
                (normalized_run_id,),
            )
            status_counts = _pairs(cur.fetchall(), "status", "row_count")

            cur.execute(
                """
                SELECT country_code, count(*) AS row_count
                FROM contact.entity_country_inference
                WHERE last_run_id = %s::uuid
                  AND status = 'ACCEPTED'
                GROUP BY country_code
                ORDER BY row_count DESC, country_code
                """,
                (normalized_run_id,),
            )
            accepted_country_counts = _pairs(cur.fetchall(), "country_code", "row_count")

            cur.execute(
                """
                SELECT confidence_band, count(*) AS row_count
                FROM (
                    SELECT CASE
                        WHEN confidence >= 0.99 THEN '0.9900-1.0000'
                        WHEN confidence >= 0.98 THEN '0.9800-0.9899'
                        WHEN confidence >= 0.95 THEN '0.9500-0.9799'
                        WHEN confidence >= 0.90 THEN '0.9000-0.9499'
                        ELSE '0.8600-0.8999'
                    END AS confidence_band
                    FROM contact.entity_country_inference
                    WHERE last_run_id = %s::uuid
                      AND status = 'ACCEPTED'
                ) AS banded
                GROUP BY confidence_band
                ORDER BY confidence_band DESC
                """,
                (normalized_run_id,),
            )
            accepted_confidence_bands = _pairs(
                cur.fetchall(), "confidence_band", "row_count"
            )

            cur.execute(
                """
                SELECT evidence_kind, count(DISTINCT entity_id) AS entity_count
                FROM (
                    SELECT ci.entity_id, item->>'kind' AS evidence_kind
                    FROM contact.entity_country_inference AS ci
                    CROSS JOIN LATERAL jsonb_array_elements(ci.evidence) AS item
                    WHERE ci.last_run_id = %s::uuid
                      AND ci.status = 'ACCEPTED'
                ) AS evidence_rows
                WHERE evidence_kind IS NOT NULL AND evidence_kind <> ''
                GROUP BY evidence_kind
                ORDER BY entity_count DESC, evidence_kind
                """,
                (normalized_run_id,),
            )
            accepted_evidence_kind_entities = _pairs(
                cur.fetchall(), "evidence_kind", "entity_count"
            )

            cur.execute(
                """
                SELECT evidence_combo, count(*) AS entity_count
                FROM (
                    SELECT ci.entity_id,
                           COALESCE(
                               (
                                   SELECT string_agg(kind, '+' ORDER BY kind)
                                   FROM (
                                       SELECT DISTINCT item->>'kind' AS kind
                                       FROM jsonb_array_elements(ci.evidence) AS item
                                       WHERE item->>'kind' IS NOT NULL
                                         AND item->>'kind' <> ''
                                   ) AS kinds
                               ),
                               'NONE'
                           ) AS evidence_combo
                    FROM contact.entity_country_inference AS ci
                    WHERE ci.last_run_id = %s::uuid
                      AND ci.status = 'ACCEPTED'
                ) AS combos
                GROUP BY evidence_combo
                ORDER BY entity_count DESC, evidence_combo
                LIMIT 30
                """,
                (normalized_run_id,),
            )
            accepted_evidence_combos_top30 = _pairs(
                cur.fetchall(), "evidence_combo", "entity_count"
            )

            cur.execute(
                """
                SELECT evidence->0->>'kind' AS evidence_kind,
                       count(*) AS entity_count,
                       min((evidence->0->>'weight')::numeric)::float8 AS min_weight,
                       max((evidence->0->>'weight')::numeric)::float8 AS max_weight,
                       avg(confidence)::float8 AS avg_confidence
                FROM contact.entity_country_inference
                WHERE last_run_id = %s::uuid
                  AND status = 'ACCEPTED'
                  AND jsonb_array_length(evidence) = 1
                GROUP BY evidence->0->>'kind'
                ORDER BY entity_count DESC, evidence_kind
                """,
                (normalized_run_id,),
            )
            accepted_single_evidence = [dict(row) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT COALESCE(country_code, '??') || '>' ||
                       COALESCE(runner_up_country_code, '??') AS country_pair,
                       count(*) AS entity_count
                FROM contact.entity_country_inference
                WHERE last_run_id = %s::uuid
                  AND status = 'CONFLICT'
                GROUP BY country_pair
                ORDER BY entity_count DESC, country_pair
                LIMIT 30
                """,
                (normalized_run_id,),
            )
            conflict_pairs_top30 = _pairs(cur.fetchall(), "country_pair", "entity_count")

            cur.execute(
                """
                SELECT
                    count(*) FILTER (WHERE ci.status = 'ACCEPTED') AS accepted_rows,
                    count(*) FILTER (
                        WHERE ci.status = 'ACCEPTED' AND ci.applied_at IS NOT NULL
                    ) AS already_applied_rows,
                    count(*) FILTER (
                        WHERE ci.status = 'ACCEPTED' AND e.country_code IS NOT NULL
                    ) AS accepted_with_source_country_now,
                    count(*) FILTER (
                        WHERE ci.status = 'ACCEPTED'
                          AND ci.confidence < %s::numeric
                    ) AS accepted_below_run_threshold
                FROM contact.entity_country_inference AS ci
                JOIN entity.entity AS e ON e.entity_id = ci.entity_id
                WHERE ci.last_run_id = %s::uuid
                """,
                (run["min_confidence"], normalized_run_id),
            )
            integrity = dict(cur.fetchone())

    persisted_rows = sum(status_counts.values())
    metrics_evaluated = int(metrics.get("evaluated") or 0)
    metrics_accepted = int(metrics.get("accepted") or 0)
    accepted_rows = int(integrity.get("accepted_rows") or 0)
    integrity_checks = {
        "run_status_success": run["status"] == "SUCCESS",
        "preview_mode": not bool(run["apply_mode"]),
        "persisted_rows_match_evaluated": persisted_rows == metrics_evaluated,
        "accepted_rows_match_metrics": accepted_rows == metrics_accepted,
        "accepted_below_run_threshold": int(
            integrity.get("accepted_below_run_threshold") or 0
        ),
        "already_applied_rows": int(integrity.get("already_applied_rows") or 0),
        "accepted_with_source_country_now": int(
            integrity.get("accepted_with_source_country_now") or 0
        ),
    }
    activation_candidate_rows = max(
        0,
        accepted_rows
        - integrity_checks["already_applied_rows"]
        - integrity_checks["accepted_with_source_country_now"],
    )
    activation_integrity_ready = all(
        (
            integrity_checks["run_status_success"],
            integrity_checks["preview_mode"],
            integrity_checks["persisted_rows_match_evaluated"],
            integrity_checks["accepted_rows_match_metrics"],
            integrity_checks["accepted_below_run_threshold"] == 0,
        )
    )

    return {
        "run_id": normalized_run_id,
        "rule_version": run["rule_version"],
        "status": run["status"],
        "apply_mode": bool(run["apply_mode"]),
        "min_confidence": run["min_confidence"],
        "min_margin": run["min_margin"],
        "persisted_rows": persisted_rows,
        "status_counts": status_counts,
        "accepted_country_counts": accepted_country_counts,
        "accepted_confidence_bands": accepted_confidence_bands,
        "accepted_evidence_kind_entities": accepted_evidence_kind_entities,
        "accepted_evidence_combos_top30": accepted_evidence_combos_top30,
        "accepted_single_evidence": accepted_single_evidence,
        "conflict_pairs_top30": conflict_pairs_top30,
        "integrity_checks": integrity_checks,
        "activation_candidate_rows": activation_candidate_rows,
        "activation_integrity_ready": activation_integrity_ready,
        "note": (
            "Counts in this report are persisted-run scoped. Evidence-kind counts are "
            "accepted entities containing that evidence kind, not raw evidence occurrences."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit a persisted contact-country inference run without recomputation"
    )
    parser.add_argument("run_id")
    args = parser.parse_args()
    try:
        report = audit_persisted_run(args.run_id)
    except (ValueError, LookupError) as exc:
        print(
            json.dumps(
                {"event": "CONTACT_COUNTRY_INFERENCE_AUDIT_ERROR", "error": str(exc)},
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 2
    print(
        json.dumps(
            {"event": "CONTACT_COUNTRY_INFERENCE_AUDIT", "report": report},
            ensure_ascii=False,
            default=str,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
