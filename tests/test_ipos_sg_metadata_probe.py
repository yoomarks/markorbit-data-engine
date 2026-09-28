"""Bounded SG metadata probe tests; no source CSV or production Work is touched."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError

import pytest

from app.snapshot_delta.ipos_sg import IPOS_DATASET_ID
from app.snapshot_delta.ipos_sg_metadata_probe import (
    MAX_METADATA_BYTES,
    METADATA_URL,
    SG_TIMEZONE,
    IposMetadataProbeError,
    decide_ipos_maintenance,
    parse_ipos_metadata,
    probe_ipos_metadata,
    sg_metadata_probe_due,
)

CHECKED = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)


def fixture(*, updated: str = "2026-09-28T02:22:15+08:00", size: int = 3930345513) -> dict:
    return {
        "code": 0,
        "data": {
            "datasetId": IPOS_DATASET_ID,
            "format": "CSV",
            "lastUpdatedAt": updated,
            "datasetSize": size,
            "columnMetadata": {
                "order": ["applicationNumber", "markStatus"],
                "map": {"applicationNumber": {"type": "Text"}},
            },
        },
        "errorMsg": "",
    }


class FakeResponse:
    def __init__(self, payload: bytes, *, url: str = METADATA_URL):
        self.payload, self.url = payload, url

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def geturl(self):
        return self.url

    def read(self, limit: int):
        return self.payload[:limit]


def test_real_metadata_shape_has_untrusted_revision_and_bounded_digest():
    observed = parse_ipos_metadata(fixture(), checked_at=CHECKED)
    assert observed.dataset_id == IPOS_DATASET_ID
    assert observed.dataset_size_bytes == 3930345513
    assert observed.source_updated_at.isoformat() == "2026-09-27T18:22:15+00:00"
    assert len(observed.metadata_identity_sha256) == 64
    assert len(observed.column_metadata_sha256) == 64
    assert observed.source_revision_trusted is False


@pytest.mark.parametrize(
    "bad",
    [
        {"code": 1, "data": fixture()["data"]},
        {"code": 0, "data": {**fixture()["data"], "datasetId": "other"}},
        {"code": 0, "data": {**fixture()["data"], "format": "JSON"}},
        {"code": 0, "data": {**fixture()["data"], "lastUpdatedAt": "2026-09-28T12:00:00"}},
        {"code": 0, "data": {**fixture()["data"], "datasetSize": 0}},
        {"code": 0, "data": {**fixture()["data"], "columnMetadata": None}},
    ],
)
def test_source_schema_drift_fails_closed(bad):
    with pytest.raises(IposMetadataProbeError):
        parse_ipos_metadata(bad, checked_at=CHECKED)


def test_probe_only_official_metadata_with_bounded_bytes_and_no_csv():
    calls = []
    payload = json.dumps(fixture()).encode()

    def opener(request, *, timeout):
        calls.append((request.full_url, request.get_method(), request.headers, timeout))
        return FakeResponse(payload)

    observation = probe_ipos_metadata(opener=opener, now=lambda: CHECKED, api_key="test-api-key")
    assert observation.source_uri == METADATA_URL
    assert calls[0][0] == METADATA_URL
    assert calls[0][1] == "GET"
    assert calls[0][2]["X-api-key"] == "test-api-key"
    assert calls[0][3] == 12
    assert len(calls) == 1


def test_redirect_and_oversize_response_are_rejected():
    payload = json.dumps(fixture()).encode()
    with pytest.raises(IposMetadataProbeError, match="redirected"):
        probe_ipos_metadata(
            opener=lambda *_args, **_kw: FakeResponse(payload, url="https://other.test")
        )
    with pytest.raises(IposMetadataProbeError, match="byte budget"):
        probe_ipos_metadata(
            opener=lambda *_args, **_kw: FakeResponse(b"x" * (MAX_METADATA_BYTES + 1))
        )


def test_429_retry_bounded_and_no_watermark_on_failure():
    attempts = []
    sleeps = []
    payload = json.dumps(fixture()).encode()

    def opener(*_args, **_kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise HTTPError(METADATA_URL, 429, "rate", None, None)
        return FakeResponse(payload)

    result = probe_ipos_metadata(opener=opener, sleeper=sleeps.append, now=lambda: CHECKED)
    assert result.dataset_id == IPOS_DATASET_ID
    assert len(attempts) == 2
    assert sleeps == [1.0]
    with pytest.raises(IposMetadataProbeError, match="retries"):
        probe_ipos_metadata(
            opener=lambda *_a, **_k: (_ for _ in ()).throw(
                HTTPError(METADATA_URL, 429, "rate", None, None)
            ),
            sleeper=lambda *_: None,
            max_attempts=2,
        )


def test_monday_wednesday_friday_2100_sgt_once_per_slot_even_after_failure():
    monday = datetime(2026, 9, 28, 21, 0, tzinfo=SG_TIMEZONE)
    assert sg_metadata_probe_due(now=monday, last_attempt_at=None)
    assert not sg_metadata_probe_due(now=monday + timedelta(hours=1), last_attempt_at=monday)
    assert not sg_metadata_probe_due(now=monday - timedelta(minutes=1), last_attempt_at=None)
    assert sg_metadata_probe_due(now=monday + timedelta(days=2), last_attempt_at=monday)
    assert not sg_metadata_probe_due(now=monday + timedelta(days=1), last_attempt_at=None)


def test_same_metadata_cannot_skip_due_weekly_full():
    observation = parse_ipos_metadata(fixture(), checked_at=CHECKED)
    decision = decide_ipos_maintenance(
        observation=observation,
        prior_observation=observation,
        last_full_success_at=CHECKED - timedelta(days=8),
        now=CHECKED,
    )
    assert decision.action == "WEEKLY_FULL_OPERATOR_REVIEW"
    assert decision.full_refresh_due and decision.operator_review_required
    assert not decision.downloaded_csv and not decision.recurring_schedule_enabled


def test_same_row_count_but_metadata_change_is_a_candidate_not_verified_content():
    before = parse_ipos_metadata(fixture(updated="2026-09-20T02:00:00+08:00"), checked_at=CHECKED)
    after = parse_ipos_metadata(fixture(), checked_at=CHECKED)
    assert before.dataset_size_bytes == after.dataset_size_bytes
    decision = decide_ipos_maintenance(
        observation=after,
        prior_observation=before,
        last_full_success_at=CHECKED - timedelta(days=1),
        now=CHECKED,
    )
    assert decision.action == "METADATA_CHANGE_CANDIDATE_REVIEW"
    assert decision.metadata_changed_candidate
    assert not decision.full_refresh_due


def test_metadata_regression_requires_review_not_fake_unchanged():
    before = parse_ipos_metadata(fixture(), checked_at=CHECKED)
    after = parse_ipos_metadata(fixture(updated="2026-09-20T02:00:00+08:00"), checked_at=CHECKED)
    decision = decide_ipos_maintenance(
        observation=after,
        prior_observation=before,
        last_full_success_at=CHECKED - timedelta(days=9),
        now=CHECKED,
    )
    assert decision.action == "SOURCE_METADATA_REGRESSION_REVIEW"
    assert not decision.full_refresh_due
    assert decision.operator_review_required


def test_future_full_snapshot_clock_skew_is_not_a_skip_signal():
    observation = parse_ipos_metadata(fixture(), checked_at=CHECKED)
    with pytest.raises(IposMetadataProbeError, match="future"):
        decide_ipos_maintenance(
            observation=observation,
            prior_observation=observation,
            last_full_success_at=CHECKED + timedelta(days=1),
            now=CHECKED,
        )
