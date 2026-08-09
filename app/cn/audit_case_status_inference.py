from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import date
from typing import Any, Iterable

from app.cn.case_status_inference import (
    MODEL_STAGE,
    MODEL_VERSION,
    CaseEvidence,
    evaluate_case_status,
)


AUDIT_NAME = "CN_CASE_STATUS_INFERENCE_HISTORICAL_VALIDATION"
AUDIT_VERSION = "CN_CASE_STATUS_INFERENCE_AUDIT_V1_DATA_COVERAGE_CLOCK"
DEFAULT_BATCH_SIZE = 5000
DEFAULT_SAMPLE_PER_RULE = 12


def _as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def validate_as_of_date(requested: date | None, coverage_date: date) -> date:
    """Keep heuristic time anchored to loaded data, never to the wall clock."""
    if requested is None:
        return coverage_date
    if requested > coverage_date:
        raise ValueError(
            f"as_of_date {requested.isoformat()} exceeds CN data coverage "
            f"{coverage_date.isoformat()}"
        )
    return requested


def row_to_evidence(row: dict[str, Any], *, as_of_date: date) -> CaseEvidence:
    known = int(row.get("known_item_count") or 0)
    final = int(row.get("final_inactive_item_count") or 0)
    dated_final = int(row.get("dated_final_item_count") or 0)

    # A TOTAL loss date is usable only if every currently final-inactive item has
    # an actual dated STATUS_CHANGED transition. FIRST_OBSERVED is deliberately
    # excluded by the SQL layer because first visibility is not an event date.
    total_final_date = None
    if known > 0 and final == known and dated_final == known:
        total_final_date = _as_date(row.get("last_dated_final_inactive_date"))

    application_number = str(row["application_number"])
    return CaseEvidence(
        application_number=application_number,
        as_of_date=as_of_date,
        filing_date=_as_date(row.get("filing_date")),
        prelim_pub_date=_as_date(row.get("prelim_pub_date")),
        registration_pub_date=_as_date(row.get("registration_pub_date")),
        valid_until=_as_date(row.get("valid_until")),
        known_item_count=known,
        final_inactive_item_count=final,
        inactive_high_confidence_item_count=int(
            row.get("inactive_high_confidence_item_count") or 0
        ),
        unknown_item_count=int(row.get("unknown_item_count") or 0),
        first_final_inactive_date=_as_date(row.get("first_dated_final_inactive_date")),
        total_final_inactive_date=total_final_date,
        first_high_confidence_inactive_date=_as_date(
            row.get("first_dated_high_confidence_inactive_date")
        ),
        evidence_refs=(
            f"cn_case_current:{application_number}",
            f"cn_goods_scope_lifecycle_current:{application_number}",
            f"cn_goods_item_observation:{application_number}",
        ),
    )


def summarize_rows(
    rows: Iterable[dict[str, Any]],
    *,
    as_of_date: date,
    coverage_date: date,
    sample_per_rule: int = DEFAULT_SAMPLE_PER_RULE,
) -> dict[str, Any]:
    rule_hits: Counter[str] = Counter()
    cause_hits: Counter[str] = Counter()
    scope_hits: Counter[str] = Counter()
    confidence_hits: Counter[str] = Counter()
    overlap_distribution: Counter[str] = Counter()
    samples: dict[str, list[dict[str, Any]]] = defaultdict(list)

    scanned = 0
    candidate_cases = 0
    total_candidates = 0
    invalid_rows = 0
    conflict_cases = 0
    unknown_goods_cases = 0
    final_loss_without_dated_observation = 0
    total_final_without_complete_dated_lineage = 0
    invalid_samples: list[dict[str, str]] = []
    conflict_samples: list[dict[str, Any]] = []

    for row in rows:
        scanned += 1
        known = int(row.get("known_item_count") or 0)
        final = int(row.get("final_inactive_item_count") or 0)
        dated_final = int(row.get("dated_final_item_count") or 0)

        if int(row.get("unknown_item_count") or 0) > 0:
            unknown_goods_cases += 1
        if final > 0 and not row.get("first_dated_final_inactive_date"):
            final_loss_without_dated_observation += 1
        if known > 0 and final == known and dated_final != known:
            total_final_without_complete_dated_lineage += 1

        try:
            evidence = row_to_evidence(row, as_of_date=as_of_date)
        except (KeyError, TypeError, ValueError) as exc:
            invalid_rows += 1
            if len(invalid_samples) < 20:
                invalid_samples.append(
                    {
                        "application_number": str(row.get("application_number") or ""),
                        "error": str(exc),
                    }
                )
            continue

        evaluation = evaluate_case_status(evidence)
        candidate_count = len(evaluation.candidates)
        overlap_distribution[str(candidate_count)] += 1
        if not candidate_count:
            continue

        candidate_cases += 1
        total_candidates += candidate_count
        causes = {candidate.inferred_cause for candidate in evaluation.candidates}
        if len(causes) > 1:
            conflict_cases += 1
            if len(conflict_samples) < 20:
                conflict_samples.append(
                    {
                        "application_number": evidence.application_number,
                        "rules": [candidate.rule_id for candidate in evaluation.candidates],
                        "causes": sorted(causes),
                    }
                )

        for candidate in evaluation.candidates:
            rule_hits[candidate.rule_id] += 1
            cause_hits[candidate.inferred_cause] += 1
            scope_hits[str(candidate.inferred_scope)] += 1
            confidence_hits[str(candidate.confidence_band)] += 1
            if len(samples[candidate.rule_id]) < sample_per_rule:
                samples[candidate.rule_id].append(
                    {
                        "application_number": evidence.application_number,
                        "inferred_status": candidate.inferred_status,
                        "inferred_cause": candidate.inferred_cause,
                        "inferred_scope": str(candidate.inferred_scope),
                        "confidence_score": candidate.confidence_score,
                        "filing_date": evidence.filing_date,
                        "prelim_pub_date": evidence.prelim_pub_date,
                        "registration_pub_date": evidence.registration_pub_date,
                        "first_final_inactive_date": evidence.first_final_inactive_date,
                        "total_final_inactive_date": evidence.total_final_inactive_date,
                        "known_item_count": evidence.known_item_count,
                        "final_inactive_item_count": evidence.final_inactive_item_count,
                    }
                )

    warnings: list[str] = []
    if final_loss_without_dated_observation:
        warnings.append("final_goods_loss_without_dated_status_change")
    if total_final_without_complete_dated_lineage:
        warnings.append("total_final_scope_without_complete_dated_item_lineage")
    if conflict_cases:
        warnings.append("multiple_heuristic_causes_for_same_case")

    status = "FAIL" if invalid_rows else ("PASS_WITH_WARNINGS" if warnings else "PASS")
    return {
        "status": status,
        "audit": AUDIT_NAME,
        "audit_version": AUDIT_VERSION,
        "model_version": MODEL_VERSION,
        "model_stage": MODEL_STAGE,
        "data_clock": {
            "coverage_date": coverage_date,
            "as_of_date": as_of_date,
            "wall_clock_time_used": False,
        },
        "population": {
            "scanned_cases_with_goods_inactivity_signal": scanned,
            "cases_with_candidates": candidate_cases,
            "cases_without_candidates": scanned - candidate_cases - invalid_rows,
            "invalid_evidence_rows": invalid_rows,
            "total_candidates": total_candidates,
        },
        "rule_hits": dict(sorted(rule_hits.items())),
        "cause_hits": dict(sorted(cause_hits.items())),
        "scope_hits": dict(sorted(scope_hits.items())),
        "confidence_band_hits": dict(sorted(confidence_hits.items())),
        "overlap": {
            "candidate_count_distribution": dict(sorted(overlap_distribution.items())),
            "cases_with_multiple_distinct_causes": conflict_cases,
            "samples": conflict_samples,
        },
        "evidence_quality": {
            "cases_with_unknown_goods": unknown_goods_cases,
            "final_loss_without_dated_status_change": final_loss_without_dated_observation,
            "total_final_scope_without_complete_dated_item_lineage": (
                total_final_without_complete_dated_lineage
            ),
            "invalid_samples": invalid_samples,
        },
        "samples_by_rule": dict(sorted(samples.items())),
        "limitations": [
            "FIRST_OBSERVED is never treated as a loss date; only STATUS_CHANGED transitions provide temporal goods-loss evidence.",
            "R7 is not evaluated because renewal/grace deadlines and renewal/restoration events are not yet reconstructed as durable official evidence.",
            "Rule hit counts are empirical candidate counts, not measured legal accuracy.",
            "Promotion from EMPIRICAL requires manual ground-truth review against official CNIPA notices/events.",
        ],
        "promotion_decision": "NOT_ELIGIBLE_WITHOUT_MANUAL_GROUND_TRUTH",
        "warnings": warnings,
    }


def _coverage_date() -> date:
    from app.db import postgres_conn

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT max(COALESCE(dataset_release_date, source_period_end)) AS coverage_date
                FROM control.source_package
                WHERE jurisdiction = 'CN'
                  AND status = 'SUCCESS'
                  AND package_kind = 'MONTHLY_PATCH'
                """
            )
            row = cur.fetchone()
    value = None if row is None else row.get("coverage_date")
    parsed = _as_date(value)
    if parsed is None:
        raise RuntimeError(
            "No successful CN MONTHLY_PATCH has a dataset_release_date/source_period_end; "
            "temporal case-status validation has no safe data clock."
        )
    return parsed


def _case_batch_sql(*, after_application_number: str, batch_size: int) -> str:
    cursor = after_application_number.replace("'", "''")
    return f"""
        WITH
        lifecycle AS
        (
            SELECT
                application_number,
                sum(toUInt64(known_item_count)) AS known_item_count,
                sum(toUInt64(final_inactive_item_count)) AS final_inactive_item_count,
                sum(toUInt64(inactive_high_confidence_item_count))
                    AS inactive_high_confidence_item_count,
                sum(toUInt64(unknown_item_count)) AS unknown_item_count
            FROM markorbit_facts.cn_goods_scope_lifecycle_current FINAL
            WHERE is_deleted = 0
            GROUP BY application_number
            HAVING final_inactive_item_count > 0 OR inactive_high_confidence_item_count > 0
        ),
        targets AS
        (
            SELECT
                c.application_number,
                c.filing_date,
                c.prelim_pub_date,
                c.registration_pub_date,
                c.valid_until,
                life.known_item_count,
                life.final_inactive_item_count,
                life.inactive_high_confidence_item_count,
                life.unknown_item_count
            FROM markorbit_facts.cn_case_current AS c FINAL
            INNER JOIN lifecycle AS life ON life.application_number = c.application_number
            WHERE c.is_deleted = 0
              AND c.application_number > '{cursor}'
            ORDER BY c.application_number
            LIMIT {int(batch_size)}
        ),
        current_items AS
        (
            SELECT item.application_number, item.class_no, item.goods_item_key
            FROM markorbit_facts.cn_goods_item_current AS item FINAL
            INNER JOIN targets AS target
              ON target.application_number = item.application_number
            WHERE item.is_deleted = 0
        ),
        dated_final_by_item AS
        (
            SELECT
                obs.application_number,
                obs.class_no,
                obs.goods_item_key,
                min(obs.source_effective_date) AS first_final_date
            FROM markorbit_facts.cn_goods_item_observation AS obs FINAL
            INNER JOIN current_items AS item
              ON item.application_number = obs.application_number
             AND item.class_no = obs.class_no
             AND item.goods_item_key = obs.goods_item_key
            WHERE obs.transition_type = 'STATUS_CHANGED'
              AND obs.new_operational_effect = 'INACTIVE_CONFIRMED'
              AND obs.previous_operational_effect NOT IN (
                  'INACTIVE_HIGH_CONFIDENCE', 'INACTIVE_CONFIRMED'
              )
              AND obs.source_effective_date IS NOT NULL
            GROUP BY obs.application_number, obs.class_no, obs.goods_item_key
        ),
        dated_final AS
        (
            SELECT
                application_number,
                count() AS dated_final_item_count,
                min(first_final_date) AS first_dated_final_inactive_date,
                max(first_final_date) AS last_dated_final_inactive_date
            FROM dated_final_by_item
            GROUP BY application_number
        ),
        dated_high_confidence_by_item AS
        (
            SELECT
                obs.application_number,
                obs.class_no,
                obs.goods_item_key,
                min(obs.source_effective_date) AS first_high_confidence_date
            FROM markorbit_facts.cn_goods_item_observation AS obs FINAL
            INNER JOIN current_items AS item
              ON item.application_number = obs.application_number
             AND item.class_no = obs.class_no
             AND item.goods_item_key = obs.goods_item_key
            WHERE obs.transition_type = 'STATUS_CHANGED'
              AND obs.new_operational_effect = 'INACTIVE_HIGH_CONFIDENCE'
              AND obs.previous_operational_effect NOT IN (
                  'INACTIVE_HIGH_CONFIDENCE', 'INACTIVE_CONFIRMED'
              )
              AND obs.source_effective_date IS NOT NULL
            GROUP BY obs.application_number, obs.class_no, obs.goods_item_key
        ),
        dated_high_confidence AS
        (
            SELECT
                application_number,
                min(first_high_confidence_date) AS first_dated_high_confidence_inactive_date
            FROM dated_high_confidence_by_item
            GROUP BY application_number
        )
        SELECT
            target.*,
            ifNull(final.dated_final_item_count, 0) AS dated_final_item_count,
            final.first_dated_final_inactive_date,
            final.last_dated_final_inactive_date,
            high.first_dated_high_confidence_inactive_date
        FROM targets AS target
        LEFT JOIN dated_final AS final ON final.application_number = target.application_number
        LEFT JOIN dated_high_confidence AS high
          ON high.application_number = target.application_number
        ORDER BY target.application_number
    """


def _dict_rows(result: Any) -> list[dict[str, Any]]:
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]


def iter_case_rows(*, batch_size: int = DEFAULT_BATCH_SIZE) -> Iterable[dict[str, Any]]:
    from app.db import clickhouse_client

    client = clickhouse_client()
    cursor = ""
    while True:
        rows = _dict_rows(
            client.query(
                _case_batch_sql(
                    after_application_number=cursor,
                    batch_size=batch_size,
                )
            )
        )
        if not rows:
            break
        yield from rows
        cursor = str(rows[-1]["application_number"])
        if len(rows) < batch_size:
            break


def build_audit(
    *,
    as_of_date: date | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    sample_per_rule: int = DEFAULT_SAMPLE_PER_RULE,
) -> dict[str, Any]:
    coverage_date = _coverage_date()
    effective_as_of = validate_as_of_date(as_of_date, coverage_date)
    return summarize_rows(
        iter_case_rows(batch_size=batch_size),
        as_of_date=effective_as_of,
        coverage_date=coverage_date,
        sample_per_rule=sample_per_rule,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit empirical CN case-status inference rules on durable M1.6 evidence."
    )
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--sample-per-rule", type=int, default=DEFAULT_SAMPLE_PER_RULE)
    args = parser.parse_args()
    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be positive")
    if args.sample_per_rule < 0:
        raise SystemExit("--sample-per-rule cannot be negative")
    print(
        json.dumps(
            build_audit(
                as_of_date=args.as_of,
                batch_size=args.batch_size,
                sample_per_rule=args.sample_per_rule,
            ),
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
