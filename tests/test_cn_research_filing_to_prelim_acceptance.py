from __future__ import annotations

from copy import deepcopy

import pytest

from app.cn.research_filing_to_prelim_acceptance import (
    _resolve_engine_sha,
    build_acceptance_receipt,
)
from app.cn.research_filing_to_prelim_duration import materialize_duration_dataset


ENGINE_SHA = "1" * 40
ROWS = [
    {
        "application_number": "100001",
        "filing_date": "2024-01-02",
        "prelim_pub_date": "2024-04-02",
        "source_package_id": "00000000-0000-0000-0000-000000000001",
        "source_effective_date": "2024-04-30",
        "source_row_hash": "a" * 64,
        "record_hash": "b" * 64,
        "source_rank": 2000000000000001,
    },
    {
        "application_number": "100002",
        "filing_date": "2024-02-01",
        "prelim_pub_date": "2024-02-29",
        "source_package_id": "00000000-0000-0000-0000-000000000002",
        "source_effective_date": "2024-04-30",
        "source_row_hash": "c" * 64,
        "record_hash": "d" * 64,
        "source_rank": 2000000000000002,
    },
]


def _materialize(rows: list[dict[str, object]], *, generated_at: str):
    return materialize_duration_dataset(
        rows,
        engine_version=f"git:{ENGINE_SHA}",
        watermark=(
            "cn-serving-epoch:coverage=2026-07-31:"
            "max-success-sequence=1234:success-count=99"
        ),
        generated_at=generated_at,
        max_rows=10_000,
    )


def test_acceptance_receipt_proves_batch_independent_replay() -> None:
    first = _materialize(ROWS, generated_at="2026-08-28T00:00:00Z")
    replay = _materialize(ROWS, generated_at="2026-08-28T01:00:00Z")

    receipt = build_acceptance_receipt(
        first,
        replay,
        engine_sha=ENGINE_SHA,
        max_rows=10_000,
        first_batch_size=5_000,
        replay_batch_size=1_000,
    )

    assert receipt["status"] == "PASS"
    assert receipt["data_engine_sha"] == ENGINE_SHA
    assert receipt["dataset_ref_id"] == first.dataset_ref.dataset_ref_id
    assert receipt["query_fingerprint_sha256"] == first.dataset_ref.query_fingerprint_sha256
    assert receipt["integrity_sha256"] == first.dataset_ref.integrity_sha256
    assert receipt["row_count"] == 2
    assert receipt["replay_match"] is True
    assert receipt["physical_batch_size_in_identity"] is False
    assert receipt["raw_population_rows_emitted"] is False
    assert receipt["legal_conclusion"] is False


def test_acceptance_rejects_content_drift() -> None:
    first = _materialize(ROWS, generated_at="2026-08-28T00:00:00Z")
    changed = deepcopy(ROWS)
    changed[1]["record_hash"] = "e" * 64
    replay = _materialize(changed, generated_at="2026-08-28T01:00:00Z")

    with pytest.raises(RuntimeError, match="research replay mismatch"):
        build_acceptance_receipt(
            first,
            replay,
            engine_sha=ENGINE_SHA,
            max_rows=10_000,
            first_batch_size=5_000,
            replay_batch_size=1_000,
        )


def test_acceptance_rejects_wrong_engine_identity() -> None:
    first = _materialize(ROWS, generated_at="2026-08-28T00:00:00Z")
    replay = _materialize(ROWS, generated_at="2026-08-28T01:00:00Z")

    with pytest.raises(RuntimeError, match="engine identity mismatch"):
        build_acceptance_receipt(
            first,
            replay,
            engine_sha="2" * 40,
            max_rows=10_000,
            first_batch_size=5_000,
            replay_batch_size=1_000,
        )


def test_acceptance_rejects_empty_population() -> None:
    first = _materialize([], generated_at="2026-08-28T00:00:00Z")
    replay = _materialize([], generated_at="2026-08-28T01:00:00Z")

    with pytest.raises(RuntimeError, match="at least one factual row"):
        build_acceptance_receipt(
            first,
            replay,
            engine_sha=ENGINE_SHA,
            max_rows=10_000,
            first_batch_size=5_000,
            replay_batch_size=1_000,
        )


def test_engine_sha_must_be_exact_and_accepts_git_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _resolve_engine_sha(f"git:{ENGINE_SHA.upper()}") == ENGINE_SHA

    monkeypatch.setenv("MARKORBIT_DATA_ENGINE_SHA", ENGINE_SHA)
    assert _resolve_engine_sha() == ENGINE_SHA

    with pytest.raises(ValueError, match="40-character"):
        _resolve_engine_sha("abc123")
