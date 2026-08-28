from __future__ import annotations

from datetime import date

import pytest

from app.cn.research_filing_to_prelim_duration import (
    DurationIntegrityAccumulator,
    _duration_batch_sql,
    materialize_duration_dataset,
    normalize_duration_observation,
)
from app.research_dataset import replay_matches


ROWS = [
    {
        "application_number": "100001",
        "filing_date": "2024-01-02",
        "prelim_pub_date": "2024-04-02",
    },
    {
        "application_number": "100002",
        "filing_date": date(2024, 2, 1),
        "prelim_pub_date": date(2024, 2, 29),
    },
    {
        "application_number": "100003",
        "filing_date": "2024-06-01",
        "prelim_pub_date": "2024-05-31",
    },
]


def _materialize(rows: list[dict[str, object]], *, generated_at: str = "2026-08-28T00:00:00Z"):
    return materialize_duration_dataset(
        rows,
        engine_version="0.4.0",
        watermark="cn-data-coverage:2026-07-31",
        generated_at=generated_at,
        max_rows=10_000,
    )


def test_normalizes_objective_duration_without_coercing_negative_dates() -> None:
    valid = normalize_duration_observation(ROWS[0])
    assert valid.duration_days == 91
    assert valid.quality == "VALID"

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


def test_content_drift_fails_replay_even_when_query_identity_is_unchanged() -> None:
    changed = [dict(row) for row in ROWS]
    changed[1]["prelim_pub_date"] = "2024-03-01"

    first = _materialize(ROWS)
    drifted = _materialize(changed)

    assert first.dataset_ref.dataset_ref_id == drifted.dataset_ref.dataset_ref_id
    assert first.dataset_ref.integrity_sha256 != drifted.dataset_ref.integrity_sha256
    assert not replay_matches(first.dataset_ref, drifted.dataset_ref)


def test_watermark_or_population_bound_changes_query_identity() -> None:
    base = _materialize(ROWS)
    other_watermark = materialize_duration_dataset(
        ROWS,
        engine_version="0.4.0",
        watermark="cn-data-coverage:2026-08-31",
        generated_at="2026-08-28T00:00:00Z",
        max_rows=10_000,
    )
    other_bound = materialize_duration_dataset(
        ROWS,
        engine_version="0.4.0",
        watermark="cn-data-coverage:2026-07-31",
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


def test_required_temporal_fields_fail_closed() -> None:
    with pytest.raises(ValueError, match="prelim_pub_date is required"):
        normalize_duration_observation(
            {
                "application_number": "100004",
                "filing_date": "2024-01-01",
                "prelim_pub_date": None,
            }
        )


def test_keyset_sql_is_bounded_and_escapes_cursor() -> None:
    sql = _duration_batch_sql(after_application_number="A'100", batch_size=250)

    assert "application_number > 'A''100'" in sql
    assert "ORDER BY application_number ASC" in sql
    assert "LIMIT 250" in sql
    assert "filing_date IS NOT NULL" in sql
    assert "prelim_pub_date IS NOT NULL" in sql

    with pytest.raises(ValueError, match="batch_size"):
        _duration_batch_sql(after_application_number="", batch_size=0)
