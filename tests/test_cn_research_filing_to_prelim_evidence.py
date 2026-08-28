from __future__ import annotations

from copy import deepcopy

import pytest

from app.cn.research_filing_to_prelim_duration import materialize_duration_dataset
from app.cn.research_filing_to_prelim_evidence import (
    EVIDENCE_VERSION,
    QUANTILE_METHOD,
    DurationSummaryMaterialization,
    build_descriptive_summary,
    build_evidence_bundle,
)


ENGINE_SHA = "1" * 40
WATERMARK = (
    "cn-serving-epoch:coverage=2026-07-31:"
    "max-success-sequence=1234:success-count=99"
)
ROWS = [
    {
        "application_number": "100001",
        "filing_date": "2024-01-01",
        "prelim_pub_date": "2024-01-01",
        "source_package_id": "00000000-0000-0000-0000-000000000001",
        "source_effective_date": "2024-04-30",
        "source_row_hash": "a" * 64,
        "record_hash": "b" * 64,
        "source_rank": 2000000000000001,
    },
    {
        "application_number": "100002",
        "filing_date": "2024-01-01",
        "prelim_pub_date": "2024-01-11",
        "source_package_id": "00000000-0000-0000-0000-000000000002",
        "source_effective_date": "2024-04-30",
        "source_row_hash": "c" * 64,
        "record_hash": "d" * 64,
        "source_rank": 2000000000000002,
    },
    {
        "application_number": "100003",
        "filing_date": "2024-01-01",
        "prelim_pub_date": "2024-01-21",
        "source_package_id": "00000000-0000-0000-0000-000000000003",
        "source_effective_date": "2024-04-30",
        "source_row_hash": "e" * 64,
        "record_hash": "f" * 64,
        "source_rank": 2000000000000003,
    },
    {
        "application_number": "100004",
        "filing_date": "2024-01-01",
        "prelim_pub_date": "2024-01-31",
        "source_package_id": "00000000-0000-0000-0000-000000000004",
        "source_effective_date": "2024-04-30",
        "source_row_hash": "1" * 64,
        "record_hash": "2" * 64,
        "source_rank": 2000000000000004,
    },
    {
        "application_number": "100005",
        "filing_date": "2024-02-02",
        "prelim_pub_date": "2024-02-01",
        "source_package_id": "00000000-0000-0000-0000-000000000005",
        "source_effective_date": "2024-04-30",
        "source_row_hash": "3" * 64,
        "record_hash": "4" * 64,
        "source_rank": 2000000000000005,
    },
]
VALID_DURATIONS = [0, 10, 20, 30]


def _materialize(*, generated_at: str):
    return materialize_duration_dataset(
        ROWS,
        engine_version=f"git:{ENGINE_SHA}",
        watermark=WATERMARK,
        generated_at=generated_at,
        max_rows=10_000,
    )


def _summary_run(*, generated_at: str) -> DurationSummaryMaterialization:
    materialization = _materialize(generated_at=generated_at)
    return DurationSummaryMaterialization(
        materialization=materialization,
        summary=build_descriptive_summary(
            materialization,
            valid_durations=VALID_DURATIONS,
            computed_at=generated_at,
        ),
    )


def test_descriptive_summary_uses_nearest_rank_and_excludes_invalid_date_order() -> None:
    materialization = _materialize(generated_at="2026-08-28T00:00:00Z")

    summary = build_descriptive_summary(
        materialization,
        valid_durations=VALID_DURATIONS,
        computed_at="2026-08-28T00:00:00Z",
    )

    assert summary["quantile_method"] == QUANTILE_METHOD
    assert summary["row_count"] == 5
    assert summary["valid_rows"] == 4
    assert summary["invalid_date_order_rows"] == 1
    assert summary["statistics"] == {
        "count": 4,
        "min_days": 0,
        "p25_days": 0,
        "median_days": 10,
        "p75_days": 20,
        "max_days": 30,
    }
    assert summary["objective_only"] is True
    assert summary["legal_conclusion"] is False
    assert summary["predictive_claim"] is False
    assert summary["raw_population_rows_emitted"] is False


def test_descriptive_summary_rejects_duration_count_drift() -> None:
    materialization = _materialize(generated_at="2026-08-28T00:00:00Z")

    with pytest.raises(RuntimeError, match="does not match materialization valid_rows"):
        build_descriptive_summary(
            materialization,
            valid_durations=[0, 10, 20],
            computed_at="2026-08-28T00:00:00Z",
        )


def test_evidence_bundle_binds_dataset_receipt_and_two_replayed_summaries() -> None:
    first = _summary_run(generated_at="2026-08-28T00:00:00Z")
    replay = _summary_run(generated_at="2026-08-28T01:00:00Z")

    bundle = build_evidence_bundle(
        first,
        replay,
        engine_sha=ENGINE_SHA,
        max_rows=10_000,
        first_batch_size=5_000,
        replay_batch_size=1_000,
    )

    assert bundle["evidence_version"] == EVIDENCE_VERSION
    assert bundle["status"] == "PASS"
    assert bundle["dataset"] == first.materialization.dataset_ref.to_dict()
    assert bundle["acceptance_receipt"]["status"] == "PASS"
    assert bundle["acceptance_receipt"]["replay_match"] is True
    assert bundle["first_summary"]["statistics"] == bundle["replay_summary"]["statistics"]
    assert bundle["raw_population_rows_emitted"] is False
    assert "rows" not in bundle


def test_evidence_bundle_rejects_descriptive_statistic_replay_drift() -> None:
    first = _summary_run(generated_at="2026-08-28T00:00:00Z")
    replay = _summary_run(generated_at="2026-08-28T01:00:00Z")
    changed = deepcopy(replay.summary)
    changed["statistics"]["median_days"] = 20
    replay = DurationSummaryMaterialization(
        materialization=replay.materialization,
        summary=changed,
    )

    with pytest.raises(RuntimeError, match="descriptive statistic replay mismatch"):
        build_evidence_bundle(
            first,
            replay,
            engine_sha=ENGINE_SHA,
            max_rows=10_000,
            first_batch_size=5_000,
            replay_batch_size=1_000,
        )


def test_evidence_bundle_requires_different_physical_batch_sizes() -> None:
    first = _summary_run(generated_at="2026-08-28T00:00:00Z")
    replay = _summary_run(generated_at="2026-08-28T01:00:00Z")

    with pytest.raises(ValueError, match="different physical batch sizes"):
        build_evidence_bundle(
            first,
            replay,
            engine_sha=ENGINE_SHA,
            max_rows=10_000,
            first_batch_size=5_000,
            replay_batch_size=5_000,
        )
