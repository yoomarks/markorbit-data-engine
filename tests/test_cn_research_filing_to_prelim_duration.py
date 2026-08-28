from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.cn.research_filing_to_prelim_duration import (
    DurationDatasetMaterialization,
    DurationIntegrityAccumulator,
    _duration_batch_sql,
    materialize_duration_dataset,
    normalize_duration_observation,
)
from app.research_dataset import replay_matches


def _row(
    application_number: str,
    filing_date: object,
    prelim_pub_date: object,
    *,
    source_package_id: str,
    source_rank: int,
) -> dict[str, Any]:
    return {
        "application_number": application_number,
        "filing_date": filing_date,
        "prelim_pub_date": prelim_pub_date,
        "source_package_id": source_package_id,
        "source_effective_date": "2026-07-31",
        "source_row_hash": f"source-row-{application_number}",
        "record_hash": f"record-{application_number}",
        "source_rank": source_rank,
    }


ROWS = [
    _row(
        "100001",
        "2024-01-02",
        "2024-04-02",
        source_package_id="00000000-0000-0000-0000-000000000001",
        source_rank=1,
    ),
    _row(
        "100002",
        date(2024, 2, 1),
        date(2024, 2, 29),
        source_package_id="00000000-0000-0000-0000-000000000002",
        source_rank=2,
    ),
    _row(
        "100003",
        "2024-06-01",
        "2024-05-31",
        source_package_id="00000000-0000-0000-0000-000000000003",
        source_rank=3,
    ),
]


def _materialize(
    rows: list[dict[str, Any]],
    *,
    generated_at: str = "2026-08-28T00:00:00Z",
) -> DurationDatasetMaterialization:
    return materialize_duration_dataset(
        rows,
        engine_version="git:test-head",
        watermark=(
            "cn-serving-epoch:coverage=2026-07-31:"
            "max-success-sequence=42:success-count=40"
        ),
        generated_at=generated_at,
        max_rows=10_000,
    )


def test_normalizes_objective_duration_without_coercing_negative_dates() -> None:
    valid = normalize_duration_observation(ROWS[0])
    assert valid.duration_days == 91
    assert valid.quality == "VALID"
    assert valid.source_package_id.endswith("0001")

    invalid = normalize_duration_observation(ROWS[2])
    assert invalid.duration_days is None
    assert invalid.quality == "INVALID_DATE_ORDER"


def test_materialization_is_replay_stable_and_generated_at_is_not_identity() -> None:
    first = _materialize(ROWS)
    replay = _materialize(ROWS, generated_at="2026-08-28T01:00:00Z")

    assert first.dataset_ref.dataset_ref_id == replay.dataset_ref.dataset_ref_id
    assert first.dataset_ref.integrity_sha256 == replay.dataset_ref.integrity_sha256
    assert replay_matches(first.dataset_ref, replay.dataset_ref)
    assert first.valid_rows == 2
    assert first.invalid_date_order_rows == 1
    assert first.dataset_ref.row_count == 3
    assert first.dataset_ref.query["legal_conclusion"] is False
    assert first.dataset_ref.query["missing_temporal_policy"] == "EXCLUDE_DECLARED"
    assert first.dataset_ref.query["replay_scope"] == "QUIESCENT_CURRENT_SERVING_EPOCH"
    assert first.dataset_ref.query["historic_as_of_reconstruction"] is False


def test_content_or_lineage_drift_fails_replay_under_same_query_identity() -> None:
    changed_date = [dict(row) for row in ROWS]
    changed_date[1]["prelim_pub_date"] = "2024-03-01"
    changed_lineage = [dict(row) for row in ROWS]
    changed_lineage[1]["record_hash"] = "record-100002-revised"

    first = _materialize(ROWS)
    date_drifted = _materialize(changed_date)
    lineage_drifted = _materialize(changed_lineage)

    assert first.dataset_ref.dataset_ref_id == date_drifted.dataset_ref.dataset_ref_id
    assert first.dataset_ref.dataset_ref_id == lineage_drifted.dataset_ref.dataset_ref_id
    assert first.dataset_ref.integrity_sha256 != date_drifted.dataset_ref.integrity_sha256
    assert first.dataset_ref.integrity_sha256 != lineage_drifted.dataset_ref.integrity_sha256
    assert not replay_matches(first.dataset_ref, date_drifted.dataset_ref)
    assert not replay_matches(first.dataset_ref, lineage_drifted.dataset_ref)


def test_watermark_or_population_bound_changes_query_identity() -> None:
    base = _materialize(ROWS)
    other_watermark = materialize_duration_dataset(
        ROWS,
        engine_version="git:test-head",
        watermark=(
            "cn-serving-epoch:coverage=2026-08-31:"
            "max-success-sequence=43:success-count=41"
        ),
        generated_at="2026-08-28T00:00:00Z",
        max_rows=10_000,
    )
    other_bound = materialize_duration_dataset(
        ROWS,
        engine_version="git:test-head",
        watermark=(
            "cn-serving-epoch:coverage=2026-07-31:"
            "max-success-sequence=42:success-count=40"
        ),
        generated_at="2026-08-28T00:00:00Z",
        max_rows=20_000,
    )

    assert base.dataset_ref.dataset_ref_id != other_watermark.dataset_ref.dataset_ref_id
    assert base.dataset_ref.dataset_ref_id != other_bound.dataset_ref.dataset_ref_id


def test_integrity_requires_strict_application_number_order() -> None:
    accumulator = DurationIntegrityAccumulator()
    accumulator.add(normalize_duration_observation(ROWS[1]))

    with pytest.raises(ValueError, match="strictly ordered"):
        accumulator.add(normalize_duration_observation(ROWS[0]))


def test_required_temporal_or_lineage_fields_fail_closed() -> None:
    missing_temporal = dict(ROWS[0])
    missing_temporal["prelim_pub_date"] = None
    with pytest.raises(ValueError, match="prelim_pub_date is required"):
        normalize_duration_observation(missing_temporal)

    missing_lineage = dict(ROWS[0])
    missing_lineage["source_package_id"] = ""
    with pytest.raises(ValueError, match="source_package_id is required"):
        normalize_duration_observation(missing_lineage)


def test_keyset_sql_is_bounded_and_carries_source_lineage() -> None:
    sql = _duration_batch_sql(after_application_number="A'100", batch_size=250)

    assert "application_number > 'A''100'" in sql
    assert "ORDER BY application_number ASC" in sql
    assert "LIMIT 250" in sql
    assert "filing_date IS NOT NULL" in sql
    assert "prelim_pub_date IS NOT NULL" in sql
    assert "source_package_id" in sql
    assert "source_row_hash" in sql
    assert "record_hash" in sql
    assert "source_rank" in sql

    with pytest.raises(ValueError, match="batch_size"):
        _duration_batch_sql(after_application_number="", batch_size=0)
