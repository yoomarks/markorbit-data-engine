from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from app.cn.research_filing_to_prelim_acceptance import (
    DEFAULT_REPLAY_BATCH_SIZE,
    _resolve_engine_sha,
    build_acceptance_receipt,
)
from app.cn.research_filing_to_prelim_duration import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_MAX_ROWS,
    DurationDatasetMaterialization,
    _serving_epoch,
    iter_live_duration_rows,
    materialize_duration_dataset,
    normalize_duration_observation,
)


EVIDENCE_VERSION = "CN_FILING_TO_PRELIM_RESEARCH_EVIDENCE_V1"
QUANTILE_METHOD = "NEAREST_RANK"


@dataclass(frozen=True)
class DurationSummaryMaterialization:
    materialization: DurationDatasetMaterialization
    summary: dict[str, Any]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _nearest_rank(sorted_values: list[int], *, numerator: int, denominator: int) -> int:
    if not sorted_values:
        raise ValueError("nearest-rank quantile requires at least one value")
    if numerator <= 0 or denominator <= 0 or numerator > denominator:
        raise ValueError("nearest-rank fraction is invalid")
    rank = (len(sorted_values) * numerator + denominator - 1) // denominator
    return sorted_values[rank - 1]


def build_descriptive_summary(
    materialization: DurationDatasetMaterialization,
    *,
    valid_durations: Iterable[int],
    computed_at: str,
) -> dict[str, Any]:
    values = list(valid_durations)
    if not values:
        raise RuntimeError("descriptive evidence requires at least one VALID duration row")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
        raise ValueError("VALID duration values must be non-negative integers")
    if len(values) != materialization.valid_rows:
        raise RuntimeError("descriptive duration count does not match materialization valid_rows")

    dataset_ref = materialization.dataset_ref
    if materialization.valid_rows + materialization.invalid_date_order_rows != dataset_ref.row_count:
        raise RuntimeError("descriptive quality counts do not reconcile to dataset row_count")
    if dataset_ref.watermark is None:
        raise RuntimeError("descriptive evidence requires a dataset watermark")

    ordered = sorted(values)
    statistics = {
        "count": len(ordered),
        "min_days": ordered[0],
        "p25_days": _nearest_rank(ordered, numerator=1, denominator=4),
        "median_days": _nearest_rank(ordered, numerator=1, denominator=2),
        "p75_days": _nearest_rank(ordered, numerator=3, denominator=4),
        "max_days": ordered[-1],
    }
    return {
        "schemaVersion": 1,
        "sourceSystem": "MARKORBIT_DATA_ENGINE",
        "dataset_ref_id": dataset_ref.dataset_ref_id,
        "engine_version": dataset_ref.engine_version,
        "query_fingerprint_sha256": dataset_ref.query_fingerprint_sha256,
        "row_count": dataset_ref.row_count,
        "integrity_sha256": dataset_ref.integrity_sha256,
        "watermark": dataset_ref.watermark,
        "valid_rows": materialization.valid_rows,
        "invalid_date_order_rows": materialization.invalid_date_order_rows,
        "quantile_method": QUANTILE_METHOD,
        "statistics": statistics,
        "objective_only": True,
        "legal_conclusion": False,
        "predictive_claim": False,
        "raw_population_rows_emitted": False,
        "computed_at": computed_at,
    }


def _capture_valid_durations(
    rows: Iterable[Mapping[str, Any]],
    *,
    values: list[int],
) -> Iterator[Mapping[str, Any]]:
    for row in rows:
        observation = normalize_duration_observation(row)
        if observation.quality == "VALID":
            if observation.duration_days is None:
                raise RuntimeError("VALID duration observation unexpectedly has no duration_days")
            values.append(observation.duration_days)
        yield row


def build_live_summary_materialization(
    *,
    batch_size: int,
    max_rows: int,
    engine_version: str,
) -> DurationSummaryMaterialization:
    before = _serving_epoch()
    computed_at = _utc_now()
    values: list[int] = []
    rows = _capture_valid_durations(
        iter_live_duration_rows(batch_size=batch_size, max_rows=max_rows),
        values=values,
    )
    materialization = materialize_duration_dataset(
        rows,
        engine_version=engine_version,
        watermark=before.watermark,
        generated_at=computed_at,
        max_rows=max_rows,
    )
    after = _serving_epoch()
    if after != before:
        raise RuntimeError(
            "CN serving epoch changed during descriptive research materialization; replay identity is unresolved"
        )
    summary = build_descriptive_summary(
        materialization,
        valid_durations=values,
        computed_at=computed_at,
    )
    return DurationSummaryMaterialization(materialization=materialization, summary=summary)


def _assert_summary_binding(
    materialization: DurationDatasetMaterialization,
    summary: Mapping[str, Any],
) -> None:
    dataset_ref = materialization.dataset_ref
    expected = {
        "dataset_ref_id": dataset_ref.dataset_ref_id,
        "engine_version": dataset_ref.engine_version,
        "query_fingerprint_sha256": dataset_ref.query_fingerprint_sha256,
        "row_count": dataset_ref.row_count,
        "integrity_sha256": dataset_ref.integrity_sha256,
        "watermark": dataset_ref.watermark,
        "valid_rows": materialization.valid_rows,
        "invalid_date_order_rows": materialization.invalid_date_order_rows,
    }
    for key, value in expected.items():
        if summary.get(key) != value:
            raise RuntimeError(f"descriptive summary is not bound to exact materialization: {key}")


def build_evidence_bundle(
    first: DurationSummaryMaterialization,
    replay: DurationSummaryMaterialization,
    *,
    engine_sha: str,
    max_rows: int,
    first_batch_size: int,
    replay_batch_size: int,
) -> dict[str, Any]:
    if first_batch_size == replay_batch_size:
        raise ValueError("evidence replay requires different physical batch sizes")

    receipt = build_acceptance_receipt(
        first.materialization,
        replay.materialization,
        engine_sha=engine_sha,
        max_rows=max_rows,
        first_batch_size=first_batch_size,
        replay_batch_size=replay_batch_size,
    )
    _assert_summary_binding(first.materialization, first.summary)
    _assert_summary_binding(replay.materialization, replay.summary)
    if first.summary["statistics"] != replay.summary["statistics"]:
        raise RuntimeError("descriptive statistic replay mismatch")

    return {
        "evidence_version": EVIDENCE_VERSION,
        "status": "PASS",
        "redacted": True,
        "objective_only": True,
        "dataset": first.materialization.dataset_ref.to_dict(),
        "acceptance_receipt": receipt,
        "first_summary": first.summary,
        "replay_summary": replay.summary,
        "raw_population_rows_emitted": False,
    }


def run_evidence(
    *,
    engine_sha: str | None = None,
    max_rows: int = DEFAULT_MAX_ROWS,
    first_batch_size: int = DEFAULT_BATCH_SIZE,
    replay_batch_size: int = DEFAULT_REPLAY_BATCH_SIZE,
) -> dict[str, Any]:
    normalized_sha = _resolve_engine_sha(engine_sha)
    if max_rows <= 0:
        raise ValueError("max_rows must be positive")
    if first_batch_size <= 0 or replay_batch_size <= 0:
        raise ValueError("batch sizes must be positive")
    if first_batch_size == replay_batch_size:
        raise ValueError("evidence replay requires different physical batch sizes")

    engine_version = f"git:{normalized_sha}"
    first = build_live_summary_materialization(
        batch_size=first_batch_size,
        max_rows=max_rows,
        engine_version=engine_version,
    )
    replay = build_live_summary_materialization(
        batch_size=replay_batch_size,
        max_rows=max_rows,
        engine_version=engine_version,
    )
    return build_evidence_bundle(
        first,
        replay,
        engine_sha=normalized_sha,
        max_rows=max_rows,
        first_batch_size=first_batch_size,
        replay_batch_size=replay_batch_size,
    )


def _emit_evidence(evidence: Mapping[str, Any], output: str | None) -> None:
    encoded = json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2)
    print(encoded)
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{encoded}\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the bounded CN filing-to-preliminary-publication research acceptance and emit "
            "one redacted metadata-only evidence bundle for Core Brain evaluation."
        )
    )
    parser.add_argument(
        "--engine-sha",
        default=None,
        help=(
            "Exact 40-character Data Engine git SHA. Defaults to "
            "MARKORBIT_DATA_ENGINE_SHA/GITHUB_SHA."
        ),
    )
    parser.add_argument("--max-rows", type=int, default=DEFAULT_MAX_ROWS)
    parser.add_argument("--first-batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument(
        "--replay-batch-size", type=int, default=DEFAULT_REPLAY_BATCH_SIZE
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional durable path for the redacted metadata-only JSON evidence bundle.",
    )
    args = parser.parse_args()

    try:
        evidence = run_evidence(
            engine_sha=args.engine_sha,
            max_rows=args.max_rows,
            first_batch_size=args.first_batch_size,
            replay_batch_size=args.replay_batch_size,
        )
    except Exception as exc:
        failure = {
            "evidence_version": EVIDENCE_VERSION,
            "status": "BLOCKED",
            "redacted": True,
            "objective_only": True,
            "reason": str(exc),
            "raw_population_rows_emitted": False,
        }
        _emit_evidence(failure, args.output)
        return 2

    _emit_evidence(evidence, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
