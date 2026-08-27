import hashlib

import pytest

from app.research_dataset import (
    ResearchDatasetContractError,
    build_research_dataset_ref_v1,
    parse_research_dataset_ref_v1,
    replay_matches,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _history_ref(**overrides):
    values = {
        "engine_version": "M1.7",
        "fact_schema_version": "cn-case-history-v1",
        "jurisdictions": ["CN"],
        "resource_kinds": ["trademark_case_history"],
        "query": {
            "resource": "trademark_case_history",
            "filters": {"application_year": {"gte": 2022, "lte": 2024}},
            "projection": ["case_id", "event_type", "event_date"],
            "order_by": ["case_id", "event_date", "event_id"],
        },
        "watermark": "cn-history-event-id:0000000000005000",
        "completeness": "COMPLETE_TO_WATERMARK",
        "pagination": {"mode": "cursor", "ordering": ["case_id", "event_date", "event_id"]},
        "row_count": 37,
        "generated_at": "2026-08-28T00:00:00+08:00",
        "integrity_sha256": _digest("stable-history-result"),
    }
    values.update(overrides)
    return build_research_dataset_ref_v1(**values)


def test_same_history_scope_has_same_dataset_identity_across_replay():
    first = _history_ref(generated_at="2026-08-28T00:00:00+08:00")
    replay = _history_ref(generated_at="2026-08-28T00:05:00+08:00")

    assert first.dataset_ref_id == replay.dataset_ref_id
    assert first.query_fingerprint_sha256 == replay.query_fingerprint_sha256
    assert replay_matches(first, replay)


def test_query_key_order_does_not_change_identity():
    first = _history_ref()
    reordered = _history_ref(
        query={
            "order_by": ["case_id", "event_date", "event_id"],
            "projection": ["case_id", "event_type", "event_date"],
            "filters": {"application_year": {"lte": 2024, "gte": 2022}},
            "resource": "trademark_case_history",
        }
    )

    assert first.dataset_ref_id == reordered.dataset_ref_id


def test_watermark_change_produces_new_dataset_identity():
    first = _history_ref()
    later = _history_ref(watermark="cn-history-event-id:0000000000005001")

    assert first.dataset_ref_id != later.dataset_ref_id
    assert not replay_matches(first, later)


def test_scope_change_produces_new_dataset_identity():
    first = _history_ref()
    changed = _history_ref(
        query={
            "resource": "trademark_case_history",
            "filters": {"application_year": {"gte": 2023, "lte": 2024}},
            "projection": ["case_id", "event_type", "event_date"],
            "order_by": ["case_id", "event_date", "event_id"],
        }
    )

    assert first.dataset_ref_id != changed.dataset_ref_id


def test_result_drift_fails_replay_even_with_same_query_identity():
    first = _history_ref()
    drifted = _history_ref(row_count=38, integrity_sha256=_digest("changed-history-result"))

    assert first.query_fingerprint_sha256 == drifted.query_fingerprint_sha256
    assert not replay_matches(first, drifted)


def test_round_trip_parser_rejects_tampered_identity():
    ref = _history_ref()
    payload = ref.to_dict()
    assert parse_research_dataset_ref_v1(payload) == ref

    payload["dataset_ref_id"] = "research-dataset_tampered"
    with pytest.raises(ResearchDatasetContractError, match="dataset_ref_id"):
        parse_research_dataset_ref_v1(payload)


def test_exactly_one_temporal_boundary_is_required():
    with pytest.raises(ResearchDatasetContractError, match="exactly one"):
        _history_ref(watermark=None, as_of=None)

    with pytest.raises(ResearchDatasetContractError, match="exactly one"):
        _history_ref(as_of="2026-08-27T00:00:00Z")


def test_sampling_requires_deterministic_seed():
    with pytest.raises(ResearchDatasetContractError, match="sampling.seed"):
        _history_ref(sampling={"strategy": "HASH_BOUNDED"})

    sampled = _history_ref(sampling={"strategy": "HASH_BOUNDED", "seed": 20260828, "limit": 1000})
    assert sampled.sampling == {"strategy": "HASH_BOUNDED", "seed": 20260828, "limit": 1000}
